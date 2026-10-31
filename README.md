# TV Installer

在电脑上给 Android TV / 电视盒子装第三方应用的本地工具。启动一个网页界面，拖入 APK 即可安装，**零第三方依赖**（Python 3.8+ 标准库 + 系统里的 adb）。

> A local web UI for sideloading APKs to Android TV over ADB, with automatic multi-channel fallback for OEM install restrictions (TCL / 雷鸟等).

## 为什么需要它

国产电视（TCL、雷鸟等）在系统安装器里做了定制校验：安装请求会先经过 `com.android.packageinstaller` 的 verifier，再按 TCL 下发的策略链（`InstallConfig`）逐条判断。声明了 `HOME` 的 APK、签名对不上的包、被云端列入黑名单的包，都会在电视上直接提示「禁止安装」，报错是 `INSTALL_FAILED_VERIFICATION_FAILURE`。

开源社区里的同类工具（couchkit、Android-TV-Desktop-Toolkit 等）本质都是一条 `adb install`，撞上这套校验只能失败。本项目内置三条安装通道并自动降级，把「装不上」的概率压到最低。

## 功能

| 模块 | 能力 |
|---|---|
| 安装 | 拖拽 / 多选 APK、批量队列、实时日志（SSE）、自动识别 **新装 / 更新 / 降级 / 重装** |
| 通道 | `adb install` → TCL 多屏互动 6553 → `push + pm install`，失败自动降级，也可手动指定 |
| 应用 | 列出第三方应用（版本号、更新时间）、启动、强制停止、清数据、卸载、导出 APK 到电脑 |
| 文件 | 浏览电视目录、上传、下载、重命名、删除、新建文件夹、**直接安装电视上已有的 APK** |
| 设置 | 默认 IP / 端口 / 通道、adb 路径、自动点「同意」、允许装桌面类、局域网访问开关 |

## 快速开始

### 前置条件

1. **Python 3.8+**（Windows 安装时勾选 `Add Python to PATH`）
2. **adb**（Android Platform Tools）。可以装 [Android SDK Platform-Tools](https://developer.android.com/studio/releases/platform-tools)，也可以直接用你已有的 adb：
   - 已加入 `PATH` → 自动识别
   - 设置了 `ANDROID_HOME` / `ANDROID_SDK_ROOT` → 自动识别
   - 都没有 → 在界面「设置」里手填 `adb.exe` 的完整路径

> 只需要 `adb`。`aapt2` 是可选的，没有它只是看不到 APK 的包名和版本号，不影响安装。

### 电视端准备

1. 电视和电脑连**同一个 Wi-Fi**
2. 打开开发者选项：设置 → 关于 → 版本号连点 5~7 次
3. 打开 **ADB 调试 / 网络调试**（不同机型叫法不同）
4. 记下电视的 IP：设置 → 网络 → 当前连接详情

### 运行

```bash
# Windows：双击
start.bat

# 或者手动
python server.py
```

浏览器会自动打开 `http://127.0.0.1:8770/`。填电视 IP → 点「连接」→ 把 APK 拖进虚线框 → 「开始安装」。

常用参数：

```bash
python server.py --port 8780     # 端口被占用时换一个
python server.py --local         # 只监听 127.0.0.1（默认行为）
python server.py --no-browser    # 不自动开浏览器
```

## 三条安装通道

| 通道 | 做法 | 适用场景 |
|---|---|---|
| `adb` | `adb install -r [-d] [-g]` | 常规最快，绝大多数第三方应用 |
| `tcast` | TCL 多屏互动（`com.tcl.MultiScreenInteraction_TV`，TCP 6553）+ `adb reverse` + 本地 HTTP 供电视拉取，自动点电视上的「同意」 | 撞上 `INSTALL_FAILED_VERIFICATION_FAILURE` 时绕过校验 |
| `pm` | `adb push /data/local/tmp` + `pm install -r` | adb 流式安装异常时的兜底 |

「自动」模式按 `adb → tcast → pm` 依次尝试。日志里能看到每一步的命令、原始输出，以及中文的失败原因（签名不一致、版本降级、存储不足、架构不匹配等）。

### 关于电视的安装校验

实测结论（TCL 65T6L Pro / Android 11）：

- 校验链挂在 **PackageInstaller 的 verifier** 上，收到 `PACKAGE_NEEDS_VERIFICATION` 才跑
- 由 `content://com.tcl.providers.config/InstallConfig` 下发的策略链控制，按 `priority 0→5` 执行，**第一条给出结论的策略就定局**
- 其中 `launcherStrategy` 只对**声明了 `HOME` 的包**做签名比对，普通应用在这一层直接放行
- 所以「某些 app 被禁止安装」通常指的是**桌面 / 启动器类** APK
- `adb install` 走的是 shell 直接 commit session，**不触发这条 verifier**，因此能装进去

想深入了解可以看 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) 的「背景：OEM 安装校验」一节。

## 安全护栏

- **桌面类拦截**：APK 声明 `android.intent.category.HOME` 时默认拒绝安装，需手动勾选「允许装桌面类」。防止误装第二个桌面导致开机弹选择框。
- **关键包保护**：`com.android.systemui`、`com.tcl.cyberui`、`com.tcl.providers.config`、`com.android.packageinstaller` 等包的卸载 / 清数据请求会被直接拒绝。自己的桌面可以用 `protected_extra` 追加保护。
- **文件操作白名单**：删除 / 改名 / 新建只允许在 `/sdcard`、`/storage`、`/mnt`、`/data/local/tmp` 下，`/system`、`/data/app` 一律拒绝。
- **只监听本机**：默认 `127.0.0.1`。需要手机访问时在「设置」里开局域网，并用 6 位访问码鉴权（改完需重启服务）。

## 目录结构

```
tv-installer/
├── server.py            HTTP 路由：静态页 + JSON API + SSE
├── core.py              配置、adb/aapt 发现、设备信息、APK 解析
├── channels.py          三条安装通道 + 错误码中文映射
├── jobs.py              安装任务队列 + 事件总线（同一时刻只跑一个任务）
├── make_icons.py        生成 PWA 图标（纯标准库画 PNG）
├── ui/                  前端：index.html / app.css / app.js / manifest / sw.js / 图标
├── docs/                开发文档、实施文档、路线图
├── config.json          本地配置（首次运行自动生成，不入库）
└── start.bat            Windows 启动脚本
```

## API

所有接口都在 `http://127.0.0.1:8770` 下，请求和响应均为 JSON（下载接口除外）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/state` | adb / aapt 路径、设备列表与信息、设置、局域网地址 |
| POST | `/api/connect` | `{ip, port}` 连接电视 |
| POST | `/api/disconnect` | 断开当前连接 |
| POST | `/api/settings` | 更新设置（IP、端口、通道、开关、adb 路径、`protected_extra`） |
| POST | `/api/upload?name=x.apk` | 原始字节流上传 APK，返回解析结果 |
| POST | `/api/inspect` | `{path, serial}` 解析本地 APK（包名、版本、中文名、是否 HOME、与已装版本对比） |
| POST | `/api/install` | `{serial, apks:[路径], channel, allow_home, downgrade, grant}` |
| POST | `/api/install-device` | `{serial, paths:[电视上的 APK]}` 直接装电视本地 APK |
| POST | `/api/cancel` | 取消当前任务 |
| GET | `/api/events` | SSE：实时日志 + 任务进度 |
| GET | `/api/apps?serial=` | 第三方应用列表（含版本号、`protected` 标记） |
| POST | `/api/app` | `{serial, package, action: launch\|stop\|clear\|uninstall}` |
| GET | `/api/app/export?package=` | 把电视上的应用 APK 导到电脑 |
| GET | `/api/files?path=/sdcard` | 目录列表 |
| POST | `/api/files/{mkdir,rename,delete,upload}` | 文件操作（受白名单限制） |
| GET | `/api/files/download?path=` | 下载文件 |

## 配置

首次运行会在项目根目录生成 `config.json`：

```json
{
  "adb": null,
  "last_ip": "",
  "last_port": 5555,
  "channel": "auto",
  "auto_accept": true,
  "allow_home": false,
  "grant": false,
  "downgrade": false,
  "lan": false,
  "http_port": 8770,
  "pin": "由程序随机生成",
  "protected_extra": []
}
```

| 键 | 说明 |
|---|---|
| `adb` | adb 可执行文件路径，`null` 表示自动查找 |
| `channel` | `auto` / `adb` / `tcast` / `pm` |
| `auto_accept` | 多屏互动通道是否自动点电视上的「同意」 |
| `allow_home` | 是否允许安装声明 HOME 的包 |
| `grant` | 安装时是否加 `-g` 自动授予运行时权限 |
| `downgrade` | 是否允许降级安装（`-d`） |
| `lan` | 是否监听 `0.0.0.0` 供局域网（手机）访问 |
| `pin` | 局域网访问码，首次运行自动生成 |
| `protected_extra` | 额外保护、禁止卸载的包名数组 |

## 已知限制

- 必须能访问到 adb：工具本身不内置 platform-tools，需要本机已有。
- 多屏互动通道依赖电视上的 `com.tcl.MultiScreenInteraction_TV` 在运行（TCL / 雷鸟自带），且电视不能处于深度休眠。
- 不支持分包 APK（`.apks` / split APK）和 `.xapk`。
- 应用列表只显示包名，不解析应用中文名（安装队列里的名字来自 APK 本身）。

## 文档

- [开发文档](docs/DEVELOPMENT.md)：架构、模块职责、数据流、API 契约、扩展指南
- [实施文档](docs/DEPLOYMENT.md)：环境准备、部署、验收清单、排错手册
- [路线图与已知问题](docs/ROADMAP.md)：待办事项、已发现但未修复的问题

## 许可

[MIT](LICENSE)
