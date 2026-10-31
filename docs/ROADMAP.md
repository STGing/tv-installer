# 路线图与已知问题

> 记录待办、已确认但尚未修复的问题，以及调研中发现的风险点。按优先级排序，处理完的移到 `CHANGELOG.md`。

## 版本规划

| 版本 | 主题 | 状态 |
|---|---|---|
| v0.1.0 | PC 端可用：三通道安装、应用管理、文件管理、设置 | ✅ 已完成 |
| v0.2.0 | 安装诊断 + 白名单管理（见下文「已规划增强」） | 计划中 |
| v0.3.0 | 分包 APK（`.apks` / split）支持 | 待定 |
| v0.4.0 | 批量更新扫描、应用中文名解析 | 待定 |
| 未排期 | 截图 / 远程按键 / 手机端 PWA 打磨 | 待定 |

---

## 已知问题

### P1 · `aapt2` 缺失时 HOME 护栏失效

**位置**：`core.apk_info()` → `jobs._run()`

**现象**：HOME 类 APK 的拦截依赖 `apk_info()` 里的 `home` 字段，而这个字段是用 `aapt2 dump xmltree` 解析 `AndroidManifest.xml` 得到的。如果机器上找不到 aapt2：

- `info["error"]` 会是「找不到 aapt2/aapt（仍可直接安装，只是看不到包名版本）」
- `info["home"]` 保持 `False`
- `jobs._run()` 里的 `if meta.get("home") and not opts.get("allow_home")` 判断失效
- **结果：桌面类 APK 会被直接装进去，可能变成第二个 HOME 候选，导致电视开机弹「选择桌面」**

**影响面**：没装 Android SDK Build-Tools 的用户。

**建议修复方向**：

1. 识别不到 aapt2 时，把 `home` 标记为 `None`（未知），在 `jobs._run()` 里对未知状态要求显式确认，而不是默认放行；
2. 或者在界面顶部常驻提示「未检测到 aapt2，桌面类防护不可用」；
3. 兜底方案：装完如果发现包是 HOME 候选（`cmd package query-activities -c android.intent.category.HOME`），提示用户。

---

### P1 · TCL `InstallConfig` 的 `config_content` 格式异常

**位置**：电视侧，不是本项目代码。与本项目无直接依赖，但会影响「被禁止安装」类问题的判断。

**现象**：抓电视 logcat 时能看到：

```
E Config:Grabber:InstallConfig: Exception: Value {"enable":"true","strategies":[...]}
  of type org.json.JSONObject cannot be converted to JSONArray
```

TCL 的 `Config:Grabber` 期望 `config_content` 是一个 **JSONArray**（对应出厂文件里的 `"content": [ {...} ]` 结构），但配置库里存的是一个 **JSONObject**（少了外层数组）。

**来源推断**：本机曾用脚本改写白名单（写 `launcherStrategy.packages`），写入时丢了外层数组。对照 `/vendor/etc/default_feature_config.json` 的原始格式：

```json
{
  "content": [
    { "enable": "true", "strategies": [ ... ] }
  ],
  "project": { "name": "InstallConfig", "type": "database" }
}
```

数据库里应该存 `content` 数组，实际存成了数组里的那个对象。

**当前影响**：校验链加载失败，等于**安装校验处于关闭状态**（配合 `package_verifier_enable=0`，任何安装都不走 verifier）。所以现在装什么都没问题。

**潜在风险**：固件 OTA、恢复出厂、清 `com.tcl.providers.config` 数据时，配置库会按出厂默认重建（格式恢复正确），**校验链可能重新生效**，那时桌面类 APK 又会装不上。

**建议处理**：

1. 只需判断当前状态，不需要修：`adb shell content query --uri content://com.tcl.providers.config/InstallConfig`，看 `config_content` 是 `{` 还是 `[` 开头；
2. 要恢复正确格式，把 `config_content` 写回 `[{...}]`（外层补数组），或直接 `pm clear com.tcl.providers.config` 让系统重建——**后者会丢白名单，且属于高危操作，务必先备份原值**；
3. 处理前先记下原始 JSON，出问题能原样写回。

---

### P2 · tcast 通道的端口与休眠依赖

**位置**：`channels.py` 的 `HTTP_PORT = 8765`、`TC_PORT = 6553`

**问题**：

- 8765 端口硬编码。被其他程序占用时，tcast 通道直接不可用，日志只有一句「本地 HTTP 服务启动失败」，用户不知道该关掉什么。
- 电视深度休眠后 6553 会断开，通道超时 900 秒才开始报错，等待很久。
- 依赖电视上的 `com.tcl.MultiScreenInteraction_TV`，非 TCL / 雷鸟机型没有这个服务。

**建议修复方向**：8765 放进配置项；连接失败时快速探测端口是否可达，不可达就跳过该通道而不是死等。

---

### P2 · 应用列表不解析中文名

**位置**：`server.list_apps()`

**现象**：「应用」页只有包名和版本号。安装队列里能看到中文名（因为那是从 APK 本地解析的），但已装应用没有。

**原因**：批量取名字需要 `pm dump` 或逐个 `aapt2 dump badging $(pm path <pkg>)`，几十个包会很慢，当前为了列表加载速度牺牲了可读性。

**建议修复方向**：懒加载——列表先出包名，点开某行时再解析名字并缓存。

---

### P3 · 单文件任务队列，不支持并发

`jobs.CURRENT` 保证同时只有一个安装任务，第二条请求返回 409。这是有意设计（避免两条通道抢电视的安装会话），但界面上的表现是「点了没反应，只有一个 toast」。

**建议**：队列化（排队等待而不是直接拒绝），或至少在按钮上明确置灰。

---

### P3 · 取消是协作式的

`jobs.cancel()` 只置标志位，正在跑的 `adb install` 不会被中断。大包安装时点「取消」要等当前这步结束。

**建议**：记录子进程 PID，取消时 `terminate()`。

---

## 已规划增强

### E1 · 安装诊断（v0.2.0）

**动机**：现在安装失败只把错误码翻成中文（`channels.ERRLIB`）。用户看到「TCL 安装校验拒绝」之后仍然不知道下一步该干什么——是被哪条策略拦的？是开关问题还是签名问题？

**设计**：

1. 安装失败时自动执行一组采集：
   - `adb logcat -d` 过滤 `verifier|Strategy|InstallConfig|PackageInstaller`，取最近 N 条
   - `settings get global package_verifier_enable` / `verifier_verify_adb_installs`
   - `content query --uri content://com.tcl.providers.config/InstallConfig`（读策略链）
   - `pm list packages | grep <包名>`（确认是否装上了但没启动）
2. 按规则给出结论：
   - 命中 `signature does not match` → 桌面类签名校验，建议开白名单或换 tcast
   - 命中 `blackListStrategy` → 包在黑名单，只能改策略或关校验
   - `package_verifier_enable=1` 且是 adb 通道 → 提示 adb 可能被 verifier 拦
   - 签名冲突 / 降级 / ABI → 已有映射，直接复用
3. 结果在日志抽屉里展开成一块「诊断」，可复制

**涉及文件**：`channels.py`（新增 `diagnose()`）、`jobs.py`（失败时调用）、`ui/app.js`（渲染诊断块）。

---

### E2 · 白名单管理（v0.2.0）

**动机**：遇到桌面类 APK 装不上，目前唯一的解法是去另一个仓库敲 `tcl-cfg.sh` 命令行，门槛太高。

**设计**：

1. 「设置」页加一块「TCL 安装白名单」
2. 显示当前 `launcherStrategy.packages` 的内容
3. 支持把某个包名加进 / 移出白名单
4. 写入方式沿用已验证的方案：`app_process` + 反射拿 `IContentProvider`，以 shell 身份 `update()`
5. **写入前必须自动备份原 `config_content` 到本地文件**，并提供「恢复备份」按钮

**风险与约束**：

- 这是改电视侧配置，操作前要有明确的风险提示和二次确认
- 写入格式必须是 TCL 期望的结构（注意上面 P1 里说的数组包裹问题，别重蹈覆辙）
- 提供「读当前值」的能力，让用户在改之前能看到完整 JSON

**涉及文件**：`core.py`（配置备份路径）、新增 `tclcfg.py`（移植 `app_process` 方案）、`server.py`（两个 API）、`ui/`（设置页 UI）。

---

### E3 · 分包 APK 支持

支持 `.apks`（bundletool 产物）、`.xapk`、以及含 `split_config.*.apk` 的目录。命令换成 `adb install-multiple`，需要先解包再一起推。

### E4 · 批量更新

选定一个本地目录，扫描里面所有 APK，与电视已装版本比对，列出「可更新」清单，一键全更。

### E5 · 应用备份 / 恢复

把已装应用连同数据一起备份（`adb backup` 或 `pm path` + 数据目录打包），换机时恢复。注意 Android 11 之后 `adb backup` 限制变多。

### E6 · 截图与远程按键

`adb exec-out screencap -p` 截图，`adb shell input keyevent` 发送遥控键。方便装机时不用来回拿遥控器。

---

## 调研记录（供后续参考）

### GitHub 同类项目对比

| 项目 | 技术栈 | 短板（相对本项目） |
|---|---|---|
| [vistrocity/couchkit](https://github.com/vistrocity/couchkit) | 原生桌面 GUI | 单条 adb 通道，无 OEM 校验绕过 |
| [steven-ahfu/Android-TV-Desktop-Toolkit](https://github.com/steven-ahfu/Android-TV-Desktop-Toolkit) | Python + CustomTkinter | 功能全但仍只有 `adb install` |
| [AnthonyGress/Android-Toolkit](https://github.com/AnthonyGress/Android-Toolkit) | Electron | 体积大，无降级通道 |
| `android-tv-debloat-toolkit` | Python + PySimpleGUI | 已停维护，安装仍单通道 |

结论：社区工具普遍把 `adb install` 当作唯一路径，遇到 OEM 定制校验就无解。本项目的差异点就是多通道降级 + 对校验链的理解。

### TCL 校验链实测摘要

详细分析见 [开发文档 §8](DEVELOPMENT.md#8-背景oem-安装校验tcl-实测)。核心几条：

- 校验挂在 PackageInstaller 的 verifier，按 `priority 0→5` 执行策略链，第一个定局
- `launcherStrategy` 只对声明 HOME 的包做签名比对，普通应用在它第一行就放行了
- 所以「某些 app 被禁止安装」通常是桌面类 APK
- `adb install` 走 shell commit session，不触发 verifier，能绕过
- 以下设备级措施**验证无效**，不要浪费时间：`settings put global verifier_verify_adb_installs 0`（本来就是 0）、禁用 verifier 组件（shell 无权限）、`persist.tcl.debug.installapk`、`persist.sys.matrix.mi.enable`
