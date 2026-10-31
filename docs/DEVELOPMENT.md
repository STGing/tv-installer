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

这一节是本项目存在的理由。改通道逻辑之前必须先看——尤其是 8.4 那张「谁经过校验」的表，写错会导致排错方向完全跑偏。

TCL 国行固件（MT5879 / Android 11）在三个位置做了定制：**安装校验**、**开机桌面**、**第三方服务绑定**。与安装直接相关的是第一个；后两个决定了「装完为什么打不开 / 为什么开机弹选择框」，在 8.7 简述。

### 8.1 校验发生在哪

调用链（来自反编译与运行时日志）：

```text
安装请求（用户在电视上点 APK / 商店下载 / 多屏互动推送）
   │
   ├─→ PackageInstaller 创建安装会话，用户点「安装」
   │        │
   │        └─→ 发出 PACKAGE_NEEDS_VERIFICATION 广播
   │                 │
   │                 └─→ com.android.packageinstaller/.verifier.VerifierReciver
   │                          ├─ 绑定 StrategyService
   │                          ├─ 读 content://com.tcl.providers.config/InstallConfig
   │                          └─ 按 priority 0→5 逐条跑策略
   │                                   └─ 第一个 onCheckEnd() 的结论即最终结论
   │
   └─→ adb install / pm install（shell 直接提交会话，跳过上面整条链）
```

关键点：**它不是「装完再检查」，而是「装之前先问一遍」**。所以「禁止安装」的提示出现在进度条开始之前。

### 8.2 策略链全貌

`InstallConfig.config_content` 的结构（出厂默认，`/vendor/etc/default_feature_config.json`）：

```jsonc
[
  {
    "enable": "true",
    "strategies": [
      {"name": "safeStrategy",      "enable": "true",  "priority": "0"},
      {"name": "blackListStrategy", "enable": "true",  "packages": [],                    "priority": "1"},
      {"name": "thridStrategy",     "enable": "false", "packages": ["com.dangbeimarket", "com.shafa.market", "com.mayi.tv"], "priority": "2"},
      {"name": "launcherStrategy",  "enable": "true",  "packages": [],                    "priority": "3"},
      {"name": "overDueStrategy",   "enable": "true",  "packages": [],                    "priority": "4"},
      {"name": "pmStrategy",        "enable": "true",                                     "priority": "5"}
    ]
  }
]
```

注意最外层是**数组**，里面套一个对象。这个细节在 8.5 会变成一个真实的坑。

### 8.3 逐条策略的真实逻辑

按 priority 升序，先命中先定局：

| 策略 | 出厂状态 | 逻辑 | 拦谁 |
|---|---|---|---|
| `safeStrategy` | 启用 | 白名单型放行，识别安全来源 | 一般放行，兜底 |
| `blackListStrategy` | 启用（列表空） | 包名在 `packages` 里 → 拒绝 | 厂商拉黑的包 |
| `thridStrategy` | **禁用** | 只放行系统应用 / 安装器发起的显式安装 | 第三方商店来源（出厂的开关就是关的） |
| `launcherStrategy` | 启用 | **只对声明 HOME 的包生效**，见下 | 桌面 / 启动器类 APK |
| `overDueStrategy` | 启用 | 过期 / 超期策略 | 少见 |
| `pmStrategy` | 启用 | 识别 adb / pm 发起 → 一律拒绝 | 但实际到不了这里 |

`launcherStrategy` 是唯一真正会误伤正常安装的一条，它的逻辑等价于：

```java
if (!mEnable) { onCheckEnd(true); return; }                  // 策略关闭 → 放行
if (!isLauncher()) { onCheckEnd(true); return; }             // 不是 HOME 应用 → 定局放行
String pkg = ...;
if (mWhiteList.contains(pkg)) { onCheckEnd(true); return; }   // 白名单命中 → 放行
if (isTclLauncher(pkg)) { onCheckEnd(true); return; }         // 与「当前默认桌面」同签名 → 放行
onCheckEnd(false);                                            // 其余一律拒绝
```

两个容易误解的地方：

1. **`isLauncher()` 判断的是「这个 APK 是否声明了 `MAIN + HOME`」**，不是「它是不是桌面」。普通视频应用在这里就定局放行了，压根走不到签名比对。
2. **`isTclLauncher()` 的比对对象不是硬编码的 `com.tcl.cyberui`**，而是先 `getHomeActivities()` 取「当前默认桌面」的包，取不到才回退到 cyberui。推论：**一旦自家桌面成为默认桌面，同签名的自家新版本天然通过校验**，不需要白名单。

`pmStrategy` 虽然写着「拒绝 adb / pm」，但它在 priority 5，而 `launcherStrategy`（priority 3）对非 HOME 包早已放行定局——所以它实际几乎不生效。**这也是「为什么 adb 能装」的一个次要原因，但不是主因**，主因见 8.4。

### 8.4 三条通道与校验的关系（最重要的一张表）

| 通道 | 是否经过 verifier | 依据 | 后果 |
|---|---|---|---|
| `adb install` | **不经过** | shell 直接 commit 安装会话，不产生 `PACKAGE_NEEDS_VERIFICATION`；且 `verifier_verify_adb_installs=0` | 策略链完全影响不到它 |
| `pm install`（push 后） | **不经过** | 同上 | 同上 |
| `tcast`（多屏互动 6553） | **经过** | 由系统安装器创建会话，等同用户手动安装，只是「同意」被自动点了 | 桌面类 APK 仍会被 `launcherStrategy` 拦 |

实测证据（2026-10-31，本机）：构造一个声明 `HOME`、包名全新、签名与任何桌面都不同的测试 APK，

- `adb install -r` → `Success`，`logcat` 里没有任何 verifier 拒绝记录
- `adb install --no-incremental -r` → 同样 `Success`
- 同一份包在电视 UI 上点安装 → 被 `launcherStrategy` 拒绝

所以正确的表述是：**「adb 能装」不是因为绕过了什么，而是根本不在那条链上**。`tcast` 才是「走正门、但把按门铃的手自动化了」。

这一点直接决定了排错顺序（8.7）。

### 8.5 隐藏陷阱：`config_content` 必须是 JSON 数组

这是本项目开发过程中踩得最深的一个坑，值得单独记。

`com.tcl.providers.config` 里所有 project 的 `config_content` 约定都是 **JSON 数组**（`IpelEngine` 是 `[{"name":"enable","value":"true"},…]`）。`InstallConfig` 只是碰巧内容是一整个对象，于是很容易被写成裸对象：

```diff
- {"enable":"true","strategies":[…]},              ← 错：裸对象
+ [{"enable":"true","strategies":[…]}]             ← 对：数组包对象
```

写成裸对象不会报错，也不会立刻有症状，它只会在**真正发生安装行为时**打一条日志：

```text
E Config:Grabber:InstallConfig: Exception: Value {"enable":"true","strategies":[...]}
  of type org.json.JSONObject cannot be converted to JSONArray
```

后果是整份 `InstallConfig` 没被加载 → `launcherStrategy` 拿不到 `mEnable` 也拿不到 `mWhiteList` → **整条校验链静默失效（fail-open）**。表现是「什么都拦不住」，很容易被误判成「这台电视的校验本来就是关的」。

更麻烦的是它的隐蔽性：这条日志**开机时不会出现**（`InstallConfig` 是安装流程按需读取的，不在开机加载的 44 个 project 里），只有装东西时才有一条 `E` 级日志，不主动 `grep` 就永远看不到。

排查命令：

```bash
# 看库里存的格式（必须以 [ 开头）
adb shell content query --uri content://com.tcl.providers.config/InstallConfig | head -c 120

# 装一次东西之后抓这条日志
adb logcat -d | grep 'Grabber:InstallConfig'
# 正常：Currrent content from Db is ：[{"enable": "true", ...
# 异常：Exception: Value {...} of type org.json.JSONObject cannot be converted to JSONArray
```

> 注：`content update --bind` 写不了这个字段——JSON 里的冒号会被 `column:type:value` 的解析规则拆坏，报 `[ERROR] Binding not well formed`。必须用 `app_process` 反射拿 `IContentProvider` 去 `update()`。

### 8.6 白名单的持久性边界

写入 `launcherStrategy.packages` 后，什么时候还在、什么时候会没：

| 触发条件 | 结果 |
|---|---|
| 普通重启 / 关机再开 | ✅ 保留（实测连续 3 次重启，带假包名标记验证过） |
| 固件 OTA 升级 / 恢复出厂 / 清 `com.tcl.providers.config` 数据 | ❌ 回到出厂默认（空列表） |
| 云端下发更高版本的该 project | ❌ 可能被覆盖 |

判断「现在会不会被重建」只需比一个版本号：

```bash
adb shell content query --uri content://com.tcl.providers.config/Version   # 本机
grep -o '"Version": "[^"]*"' /vendor/etc/default_feature_config.json        # 出厂
```

两者相等 → 不会重建（`ConfigProvider` 只在「DB 版本落后或缺失」时才导入默认值）；固件升级后出厂版本号会变，那一刻整库重置。

### 8.7 对开发与排错的影响

**通道选择的优先级**：

1. 普通应用 → `adb`，别绕远路
2. 桌面类 APK → 先判断它是否在电视白名单里；不在的话 `tcast` 一样会被拦，此时要么写白名单，要么接受 `adb` 通道
3. `tcast` 的正确定位是「adb 不可用时的替代路径」，不是「绕过校验的银弹」

**用户报「装不上」时的判断顺序**：

```text
① meta.home == true ?           → 大概率是 launcherStrategy，指向白名单
② 报错是 UPDATE_INCOMPATIBLE ?  → 签名冲突，与 TCL 无关，需先卸载
③ 报错是 VERSION_DOWNGRADE ?    → 版本更低，开 -d 或换包
④ 报错是 NO_MATCHING_ABIS ?     → 架构不符，无解
⑤ 以上都不是，且电视上手动装也被拦 → 查 8.5 的格式问题
```

**装完打不开 / 开机异常**，那是另外两条定制链，不在安装校验范围内：

| 现象 | 原因 | 关联 |
|---|---|---|
| 装完按 HOME 弹「选择桌面」 | 出现了第二个 HOME 候选 | 8.3 的 `isLauncher()` |
| 换成非 `directBootAware` 的桌面后**卡开机动画** | TCL 在 user 0 解锁前就 resolve HOME，此时非 directBootAware 组件不可见 → `No home screen found` | 见 [PITFALLS P-05](PITFALLS.md) |
| 桌面的守护服务起不来 | `canBindServiceGo()` 默认拒绝第三方绑定，需要 TCL 自定义 AppOps | 本项目不涉及 |


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
- [ ] **两份 README 都改过**：`README.md`（英文）与 `README.zh-CN.md`（中文）章节结构一一对应，只改一份等于制造两套事实
- [ ] **截图与实际界面一致**：改过 `ui/` 就重拍 `docs/screenshots/`，拍法见 [`INDEX.md` 第 9.1 节](../INDEX.md)
- [ ] **没有残留占位符**：`grep -rn "<your-account>\|<你的账号>\|TODO" --include="*.md" .`
- [ ] **git 身份是你自己的**：`git config user.email`（占位邮箱推上去不会关联到账号）

一次跑完的隐私扫描（提交前用）：

```bash
grep -rnE "([0-9]{1,3}\.){3}[0-9]{1,3}|[A-Za-z]:\\\\|/Users/|DESKTOP-|@[A-Za-z0-9.-]+\.[a-z]{2,}" \
  --include="*.py" --include="*.md" --include="*.js" --include="*.json" --include="*.bat" . \
  | grep -vE "127\.0\.0\.1|0\.0\.0\.0|192\.168\.1\.20|8\.8\.8\.8|@users\.noreply\.github\.com"
```

期望输出为空。有输出就逐条判断：是真的泄露，还是文档里的通用示例。

> 为什么要单独列这三条：`aapt2` 的「可选 / 必需」在同一份文档里自相矛盾过、`<your-account>` 占位符跟着提交进了仓库、提交作者一度是本机 Windows 用户名。**这类问题的共同点是不会报错**，只能靠清单拦住。
