# 开发文档

> 这份文档面向修改代码的人。它描述当前实现（v0.1.0）的结构、约定和扩展方式。
> 使用层面的说明看 [README](../README.md)，部署与排错看 [实施文档](DEPLOYMENT.md)。

## 1. 项目定位

一个跑在电脑上的本地 Web 应用，通过 adb 管理同一局域网内的 Android TV：装应用、管应用、管文件。

不做的事：不投屏、不遥控、不做系统级破解、不内置 adb 二进制。范围收在「应用生命周期 + 文件」上。

## 2. 设计原则

| 原则 | 具体体现 |
|---|---|
| **零第三方依赖** | 只用 Python 标准库。用户不需要 pip install，`start.bat` 双击即用。这也是不用 Flask / FastAPI 的原因——为几十个接口引入依赖树不划算 |
| **单进程 + 线程** | HTTP 服务用 `ThreadingHTTPServer`，安装任务跑在独立线程，日志通过内存事件总线推给 SSE。没有消息队列、没有数据库 |
| **通道可插拔** | 安装通道是纯函数 `f(serial, apk, opts, log) -> (ok, msg)`，注册进 `CHANNELS` 字典即可被调度 |
| **失败自动降级** | `auto` 模式按 `adb → tcast → pm` 依次尝试，任一成功即停 |
| **危险操作默认关闭** | HOME 类 APK 默认拦截、关键包禁止卸载、文件操作限定白名单、局域网默认不开 |

## 3. 技术选型

| 决策 | 选择 | 理由 |
|---|---|---|
| 后端 | Python 标准库 `http.server` | 目标机器通常已有 Python；无依赖 = 无安装成本。性能不是瓶颈（单人使用、低频请求） |
| 前端 | 原生 JS + CSS，无构建步骤 | 改完刷新即可，不需要 node/npm。代码量小（`app.js` 约 470 行），不值得上框架 |
| 通信 | JSON + SSE | SSE 比 WebSocket 简单（纯 HTTP、自带重连），日志推送够用 |
| 界面形态 | 本地网页 | 跨平台、可响应式适配手机、方便以后打成 exe。原生 GUI 要 PyQt（约 100 MB 依赖）或 tkinter（部分 Python 发行版缺失） |
| 状态存储 | JSON 文件 | 配置项不到 15 个，不需要数据库 |

## 4. 架构

### 4.1 模块依赖

```
                 ┌──────────────┐
                 │  ui/ (前端)  │
                 └──────┬───────┘
                   HTTP │ JSON / SSE
                 ┌──────▼───────┐
                 │  server.py   │  HTTP 层：路由、静态文件、鉴权、下载/导出
                 └──┬────────┬──┘
                    │        │
            ┌───────▼──┐  ┌──▼────────┐
            │ jobs.py  │  │  core.py  │  配置 / adb 发现 / 设备信息 / APK 解析
            └────┬─────┘  └───────────┘
                 │            ▲
            ┌────▼────────────┴──┐
            │   channels.py      │  三条安装通道 + 错误码映射
            └────────────────────┘
```

依赖方向是单向的：`server → jobs → channels → core`。`core` 不依赖任何本项目模块。

### 4.2 线程模型

| 线程 | 职责 |
|---|---|
| 主线程 | `serve_forever()`，接收连接 |
| 每请求一线程 | `ThreadingHTTPServer` 默认行为。SSE 连接会长期占用一个线程（每 0.8 秒发一次 `: ping`） |
| 安装线程 | `jobs.start()` 里 `threading.Thread(daemon=True)`，同一时刻最多一个 |

**并发约束**：`jobs.CURRENT` 保证全局只有一个安装任务。第二条 `POST /api/install` 会收到 409。这是刻意设计——两条通道同时抢电视的安装会话会互相打架。

### 4.3 事件总线

`jobs.HUB` 是一个极简的发布订阅：

- `publish(ev)`：加自增 `seq`，写入 `history`，再推给所有订阅者的 list
- 订阅者就是 SSE 连接的 list（不是 queue，读端一次性排空）
- `history` 上限 800 条，超出后保留最近 500 条
- 新 SSE 连接会先补发最近 150 条 backlog，再进入实时推送

事件有两种：

```jsonc
{"type": "log", "level": "info|ok|warn|error|dim", "text": "[22:31:05] ...", "seq": 12}
{"type": "job", "job": {"id", "status", "serial", "opts", "items": [...]}, "seq": 13}
```

## 5. 模块详解

### 5.1 core.py

| 函数 | 说明 |
|---|---|
| `load_config()` / `save_config()` / `update_config(patch)` | 读写 `config.json`。只保留 `DEFAULTS` 里声明过的键，未知键会被丢弃 |
| `sdk_root()` | 依次读 `ANDROID_HOME` → `ANDROID_SDK_ROOT` → `ANDROID_SDK`，都不存在返回空串 |
| `adb_path()` | 查找顺序：配置里的 `adb` → `PATH` 里的 `adb` → `$SDK/platform-tools/adb` → Windows 上 `%LOCALAPPDATA%\Android\Sdk\...` |
| `aapt_paths()` | 扫 `$SDK/build-tools/*`（版本倒序）+ `PATH`，返回候选列表。没有 aapt 不影响安装 |
| `adb(args, serial, timeout, binary)` | **所有 adb 调用的唯一入口**。统一处理 `-s serial`、编码、超时、Windows 下隐藏控制台窗口 |
| `sh(serial, command)` | `adb shell` 的便捷封装，返回 stdout+stderr 合并后的字符串 |
| `list_devices()` | 解析 `adb devices -l`，只取状态为 `device` 的行 |
| `device_info(serial)` | 读 `ro.product.model`、`ro.build.version.release`、`ro.product.cpu.abi`、`wm size` |
| `tv_ip(serial)` | 从 `serial`（`ip:port` 形式）截出 IP；USB 设备则解析 `ip route` 的 `src` |
| `installed_version(serial, pkg)` | `dumpsys package` 取 `versionName` / `versionCode`，未安装返回 `None` |
| `apk_info(path, serial)` | 解析 APK 元信息，见下 |
| `is_protected(pkg)` | 内置 `PROTECTED_PKGS` + 配置里的 `protected_extra` |
| `safe_file_path(path)` | 判断路径是否落在 `SAFE_FILE_ROOTS` 之内 |

**`apk_info()` 的解析流程**：

1. `aapt2 dump badging` 取 `package` / `versionCode` / `versionName` / `sdkVersion` / `application-label`
2. 中文名优先：`application-label-zh` → `application-label-zh-CN` → 任意 `application-label-*` → `application: label` → 文件名
3. `aapt2 dump xmltree --file AndroidManifest.xml` 里搜 `android.intent.category.HOME` 判断是否桌面类
4. 如果传了 `serial`，和电视上的已装版本比：新的 `versionCode` 更大 → `update`，更小 → `downgrade`，相等 → `reinstall`，未安装 → `install`

`action` 字段只用于界面展示，实际安装参数由 `opts.downgrade` 控制。

### 5.2 channels.py

三条通道的统一签名：

```python
def channel_x(serial: str, apk: str, opts: dict, log: callable) -> tuple[bool, str]
```

`log(text, level="info")` 由 `jobs` 注入，负责同时打到控制台和 SSE。

#### adb 通道

```python
args = ["install", "-r"] + (["-d"] if downgrade) + (["-g"] if grant) + [apk]
```

- 只看输出里有没有 `Success`
- 失败走 `explain()` 翻译成中文

#### tcast 通道（TCL 多屏互动）

协议细节（`TCP 6553`，纯文本，消息以 `>>` 分隔字段，帧之间用不可见分隔符）：

```
1. adb reverse tcp:8765 tcp:8765            # 让电视能访问电脑上的 HTTP 服务
2. 本地起 ApkServer（HTTP_PORT=8765），任意 GET 都返回同一个 APK，支持 Range
3. socket 连 电视IP:6553
4. 发 "159>>1>>0"                            # 握手
5. 发 "223>>INSTALL>>包名>>应用名>>httpUrl>>2>>versionCode>>大小MB>>>>>>md5"
6. 收消息：
   "223>>INSTALL>>pkg>>...>>confirm_pending" → 电视弹确认框，自动点「同意」
   "223>>PROGRESS>>pkg>>...>>stage>>百分数"   → stage=5 表示安装完成
   "223>>INSTALL>>pkg>>...>>denied"          → 电视端拒绝
7. 每 5 秒发 "150>>" 保活；总超时 900 秒
8. 收尾：移除 adb reverse、关 socket、关 HTTP 服务
```

自动点「同意」的实现：`uiautomator dump` 导出界面 XML，找到文案匹配「同意 / 允许 / 安装」的节点，取其 `bounds` 中心坐标，`input tap x y`。找不到就退回人工确认。

素材来源是 TCL 自带的多屏互动服务，这条路径以系统身份发起安装，能过掉普通 `adb install` 会被拦的部分场景。

#### pm 通道

```python
adb push <apk> /data/local/tmp/tvi_<随机8位>.apk
adb shell pm install -r [-d] [-g] <远端路径>
# finally: 删掉远端临时文件
```

`channel_pm_device()` 是变体：跳过 push，直接 `pm install` 电视上已有的 APK。

#### 错误码映射

`ERRLIB` 把 Android 的安装错误码翻成可执行的中文建议，例如：

| 错误码 | 提示 |
|---|---|
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | 签名与已装版本不一致，需先卸载原应用 |
| `INSTALL_FAILED_VERSION_DOWNGRADE` | 目标版本更低，勾选「允许降级」再试 |
| `INSTALL_FAILED_NO_MATCHING_ABIS` | APK 的 CPU 架构与电视不匹配（需 armeabi-v7a） |
| `INSTALL_FAILED_VERIFICATION_FAILURE` | TCL 安装校验拒绝（换多屏互动通道） |

新增错误码只需要往这个字典里加一行。

### 5.3 jobs.py

```python
job = {
    "id": "a1b2c3d4",
    "serial": "192.168.1.20:5555",
    "opts": {"channel": "auto", "allow_home": False, "downgrade": False, "grant": False, "auto_accept": True},
    "status": "running",          # running | done
    "cancel": False,
    "items": [
        {"name": "抖音TV.apk", "path": "...", "kind": "file|device",
         "status": "pending|running|done|failed|canceled",
         "msg": "", "meta": {...apk_info...}}
    ],
}
```

`_run()` 的执行顺序：

```
for item in items:
    if 已取消: 标记 canceled; continue
    if item.meta.home and not opts.allow_home: 标记 failed; continue     ← 护栏
    plan = ["pm-device"] if kind == "device" else (["adb","tcast","pm"] if auto else [channel])
    for ch in plan:
        ok, msg = 调用对应通道
        if ok: break
        if 手动指定通道: break                                            ← 不降级
    标记 done / failed
```

取消是**协作式**的：置 `job.cancel = True`，当前这一步跑完后才停，不会打断正在进行的 adb 进程。

### 5.4 server.py

请求入口 `Handler(BaseHTTPRequestHandler)`：

- `do_GET`：先鉴权 → `/api/*` 走 `_api_get`，其余当静态文件（路径必须落在 `ui/` 内，防目录穿越）
- `do_POST`：鉴权 → `_api_post`

**鉴权**：`_authorized()` 的逻辑是——没开局域网、或来源是 `127.0.0.1`，直接放行；否则要 `?pin=` 或 cookie `tvpin` 匹配。首次通过 URL 带 PIN 访问会种 cookie 并 302 回首页。

**SSE**：`_sse()` 先补发 150 条 backlog，然后循环「有事件就发，没事件发心跳」。

**下载/导出**：都用 `adb exec-out cat <path>` 拿二进制流，`Content-Length` 来自 `stat`（下载）或实际字节数（导出）。

### 5.5 ui/

| 文件 | 作用 |
|---|---|
| `index.html` | 四个 Tab 的结构：安装 / 应用 / 文件 / 设置 |
| `app.css` | 单文件样式，含响应式（手机端底部导航） |
| `app.js` | 全部逻辑：状态、渲染、API 调用、SSE 订阅。无框架，用模板字符串拼 DOM |
| `manifest.webmanifest` + `sw.js` | PWA：可「添加到主屏幕」，`sw.js` 只做静态资源缓存 |
| `make_icons.py` | 纯标准库生成 icon-192 / icon-512（zlib + struct 手写 PNG） |

`app.js` 里全局状态放在 `S` 对象（`S.serial`、`S.queue`、`S.apps`…），`render*()` 函数负责把状态刷到 DOM。没有虚拟 DOM，直接重建列表——数据量小（几十条），够用。

## 6. 一次安装的完整时序

```
浏览器                server.py              jobs.py            channels.py          电视
  │  POST /api/install   │                     │                    │                │
  ├─────────────────────>│                     │                    │                │
  │                      │ jobs.start()        │                    │                │
  │                      ├────────────────────>│ 开线程             │                │
  │  200 {job}           │                     │                    │                │
  │<─────────────────────┤                     │                    │                │
  │                      │                     │ for item:          │                │
  │                      │                     ├───────────────────>│ adb install    │
  │                      │                     │                    ├───────────────>│
  │  SSE: log/job        │  HUB.publish()      │                    │   Success      │
  │<─────────────────────┤<────────────────────┤<───────────────────┤<───────────────│
  │                      │                     │ 更新 item.status   │                │
```

失败时 `channels` 返回 `(False, 原因)`，`jobs` 记日志并尝试下一条通道；三条都失败则 `item.status = failed`。

## 7. API 契约

约定：

- 所有响应都是 JSON，`{"ok": bool, ...}`；失败时 `msg` 放中文原因
- `serial` 一律用 `adb devices` 里的 serial（网络设备形如 `192.168.1.20:5555`）
- 未授权返回 401，任务冲突返回 409，路径越界返回 400

### 关键接口

**`POST /api/install`**

```jsonc
// 请求
{
  "serial": "192.168.1.20:5555",
  "apks": ["C:/Users/me/Desktop/app.apk"],   // 绝对路径，或 /api/upload 返回的路径
  "channel": "auto",                          // auto | adb | tcast | pm
  "allow_home": false,
  "downgrade": false,
  "grant": false,
  "auto_accept": true
}
// 响应
{"ok": true, "job": {"id": "a1b2c3d4", "status": "running", "items": [...]}}
```

**`POST /api/inspect`** — 只解析不安装，用于入队前预览：

```jsonc
{"path": "...", "serial": "..."}
// →
{"ok": true, "meta": {"package", "label", "versionName", "versionCode",
                      "home", "action", "installed", "size", "error"}}
```

**`GET /api/events`** — SSE，事件格式见 4.3。

## 8. 背景：OEM 安装校验（TCL 实测）

这部分是本项目存在的理由，改通道逻辑前建议先看。

TCL 国行固件（MT5879 / Android 11）在三个位置做了定制：安装校验、开机桌面、第三方服务绑定。与安装相关的是第一个。

### 8.1 校验发生在哪

`com.android.packageinstaller/.verifier.VerifierReciver` 接收 `PACKAGE_NEEDS_VERIFICATION` 广播，拉起 `StrategyService`，读 `content://com.tcl.providers.config/InstallConfig` 的 `config_content`，按策略链逐条执行。

### 8.2 策略链

按 `priority` 升序执行，**第一个给出结论的策略定局**：

```jsonc
{
  "enable": "true",
  "strategies": [
    {"name": "safeStrategy",      "enable": "true",  "priority": "0"},
    {"name": "blackListStrategy", "enable": "true",  "packages": [], "priority": "1"},
    {"name": "thridStrategy",     "enable": "false", "packages": ["com.dangbeimarket", "..."], "priority": "2"},
    {"name": "launcherStrategy",  "enable": "true",  "packages": [], "priority": "3"},
    {"name": "overDueStrategy",   "enable": "true",  "packages": [], "priority": "4"},
    {"name": "pmStrategy",        "enable": "true",  "priority": "5"}
  ]
}
```

各策略的作用：

- `blackListStrategy`：包名在 `packages` 里直接拒绝
- `thridStrategy`：只放行系统应用或安装器发起的显式安装（出厂默认关闭）
- `launcherStrategy`：**只对声明 HOME 的包生效**，逻辑等价于：

  ```java
  if (!mEnable) { onCheckEnd(true); return; }
  if (!isLauncher()) { onCheckEnd(true); return; }        // 普通应用直接放行
  if (mWhiteList.contains(pkg)) { onCheckEnd(true); return; }
  if (isTclLauncher(pkg)) { onCheckEnd(true); return; }   // 与当前默认桌面同签名
  onCheckEnd(false);
  ```

- `pmStrategy`：识别 adb / pm 发起，一律拒绝

这解释了「为什么只有某些 app 被拦」：普通应用在 `launcherStrategy` 的第一行就放行定局了，压根走不到 `pmStrategy`；只有桌面类 APK 会进入签名比对并被拒。

### 8.3 为什么 adb 能过

`adb install` 走 shell 直接 commit session，**不经过 verifier**（`package_verifier_enable` / `verifier_verify_adb_installs` 为 0，且 TCL 的校验链只在带验证 token 的安装器会话里跑）。实测：一个声明 HOME、全新签名、全新包名的测试 APK，`adb install -r` 与 `--no-incremental` 均成功，日志里没有任何 verifier 拒绝记录。

同一个 APK 在电视上手动点安装，会被 `launcherStrategy` 拒绝。

### 8.4 对开发的影响

- adb 通道是首选，不要因为「怕被拦」就优先走 tcast
- 「被禁止安装」的用户反馈，先判断是不是桌面类 APK（`meta.home == true`）
- 真正需要绕过校验的场景，tcast 通道是备用路径

## 9. 扩展指南

### 9.1 新增一条安装通道

1. 在 `channels.py` 写一个同签名的函数：

   ```python
   def channel_foo(serial, apk, opts, log):
       log("$ foo install " + os.path.basename(apk))
       return True, "INSTALLED (foo)"      # 或 False, "中文失败原因"
   ```

2. 注册：`CHANNELS = {"adb": ..., "tcast": ..., "pm": ..., "foo": channel_foo}`
3. 加进自动降级顺序：`jobs.py` 的 `_run()` 里 `plan = ["adb", "tcast", "pm", "foo"]`
4. 前端下拉选项：`ui/index.html` 里 `#channel` 加 `<option value="foo">`

注意：新通道如果会长期占用电视（比如 tcast 的 socket），必须在 `finally` 里清理，否则下一条通道会撞车。

### 9.2 新增一个 API

1. `server.py` 的 `_api_get` 或 `_api_post` 里加一个 `if p == "/api/xxx":` 分支
2. 需要保护包的用 `core.is_protected()`，需要文件路径的用 `core.safe_file_path()` 或 `SAFE_PATH_RE`
3. `ui/app.js` 里用现成的 `api(path, body)` 调用

### 9.3 改前端

无构建步骤，改完刷新浏览器即可。注意 `sw.js` 有缓存，改动 `index.html` / `app.js` / `app.css` 后如果看不到效果，DevTools → Application → Service Workers → Unregister，或者改 `sw.js` 里的 `CACHE` 版本号。

## 10. 编码约定

- 注释和用户可见文案用中文，标识符用英文
- 所有外部命令必须有超时（`core.adb()` 的 `timeout` 参数），默认 120 秒，装包类给 1800 秒
- 捕获异常后要给用户一句能看懂的话，不要把 traceback 直接抛到界面
- 新增破坏性操作必须同时加护栏和二次确认
- 不引入第三方依赖；确有需要先在 issue 里讨论

## 11. 调试

```bash
# 看服务端实时日志（jobs.log 同时打到 stdout）
python server.py --no-browser

# 单独测 adb 通道
adb -s 192.168.1.20:5555 install -r app.apk

# 看 APK 元信息
aapt2 dump badging app.apk | head -5
aapt2 dump xmltree --file AndroidManifest.xml app.apk | grep -i category

# 看电视当前的安装策略链
adb shell content query --uri content://com.tcl.providers.config/InstallConfig

# 看校验器日志（在电视上点安装时抓）
adb logcat -c && adb logcat | grep -iE "verifier|Strategy|InstallConfig"

# 检查模拟器/设备是否真的连上
adb devices -l
```

**常见问题定位**：

| 现象 | 先查 |
|---|---|
| 连不上电视 | 同一 Wi-Fi？电视 ADB 开关开着？`adb connect <ip>:5555` 手动试 |
| 装了没反应 | 电视上 `pm list packages \| grep 包名` 确认；再看 `adb install` 原始输出 |
| tcast 卡住 | 电视是否休眠；`com.tcl.MultiScreenInteraction_TV` 是否在跑；6553 是否被占 |
| 界面不刷新 | SSE 连接是否建立（DevTools → Network → events）；`sw.js` 缓存 |

## 12. 发布流程

版本号规则：`MAJOR.MINOR.PATCH`，写在 `CHANGELOG.md` 顶部。

```bash
# 1. 自检
python -m py_compile server.py core.py channels.py jobs.py
node --check ui/app.js          # 有 node 的话

# 2. 真机验证：连接、装一个普通 APK、应用列表、卸载、文件上传下载

# 3. 更新 CHANGELOG.md 与 README 的「已知限制」

# 4. 打 tag
git add -A
git commit -m "release: v0.2.0"
git tag -a v0.2.0 -m "v0.2.0"
git push origin main --tags
```

发版前必查：

- [ ] 没有硬编码的个人路径 / IP / 包名
- [ ] `config.json` 没有被提交
- [ ] `python -m py_compile` 全过
- [ ] 真机上至少完成一次「装 → 列表 → 卸载」闭环
