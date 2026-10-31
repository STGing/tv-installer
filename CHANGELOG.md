# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 文档

- README 拆成双语：`README.md`（英文，GitHub 默认展示）+ `README.zh-CN.md`（简体中文），两份顶部互相切换
- 新增 `docs/screenshots/`：安装页与设置页的真实界面截图，两份 README 都内嵌展示
- 新增[《踩坑记录》](docs/PITFALLS.md)：18 个真实踩过的坑，按「现象 → 根因 → 解决 → 教训」组织，并单列 5 条**被实测推翻的早期结论**
- 重写[《开发文档》§8](docs/DEVELOPMENT.md)：补全 6 条策略链的逐条逻辑、三条通道与校验的关系矩阵、`config_content` 格式陷阱（数组 vs 对象）、白名单持久性边界、装完打不开的两条定制链
- 修正三处**错误表述**：
  - `aapt2` 曾被写作「可选组件」——实际是桌面类护栏的前提，缺失会 fail-open
  - `tcast` 曾被写作「绕过校验的通道」——实际它走 `PackageInstaller`，是三条通道里**唯一**经过校验的
  - `INSTALL_FAILED_VERIFICATION_FAILURE` 的处置建议从「换 tcast」改为按通道 + 是否 HOME 包分情况判断
- [实施文档](docs/DEPLOYMENT.md)新增 8.6「装桌面类 APK：写电视白名单」，含状态确认、写入、回读、三条持久性边界
- [路线图](docs/ROADMAP.md)同步 `InstallConfig` 格式问题为**已修复**，并纠正「等 OTA 会让格式变对」的因果倒置

### 计划中

- 安装诊断：失败时自动采集 logcat、校验开关、策略链，给出定位结论
- TCL 安装白名单管理界面（读写 `launcherStrategy.packages`，写入前自动备份）
- 分包 APK（`.apks` / split）支持
- `aapt2` 缺失时护栏改为 fail-closed（解析不确定就问，而不是默认放行）

详见 [路线图](docs/ROADMAP.md)。

## [0.1.0] - 2026-10-31

首个可用版本：PC 端完整流程跑通，在 TCL 65T6L Pro（MT5879 / Android 11）上完成真机验证。

### 新增

- **本地 Web 界面**：四个 Tab（安装 / 应用 / 文件 / 设置），响应式布局，可「添加到主屏幕」以 PWA 形式打开
- **三条安装通道**
  - `adb`：`adb install -r [-d] [-g]`
  - `tcast`：TCL 多屏互动 TCP 6553 推送，配 `adb reverse` + 本地 HTTP（支持 Range）+ `uiautomator` 自动点「同意」
  - `pm`：`adb push /data/local/tmp` + `pm install -r`
  - `auto` 模式按 `adb → tcast → pm` 自动降级
- **APK 解析**：包名、版本号、中文应用名、目标 SDK、是否声明 HOME；与电视已装版本比对后标记 `新装 / 更新 / 降级 / 重装`
- **任务队列 + 实时日志**：SSE 推送，日志带级别着色，同一时刻只跑一个任务
- **应用管理**：批量列出第三方应用（版本号、更新时间）、启动、强制停止、清数据、卸载、导出 APK 到电脑
- **文件管理**：浏览（含 `/sdcard` 软链解析）、上传、下载、重命名、删除、新建文件夹、直接安装电视上已有的 APK
- **设置**：电视 IP / 端口、默认通道、adb 路径、自动授权、允许降级、允许装桌面类、自动点「同意」、局域网访问开关、额外保护包
- **安全护栏**：HOME 类 APK 默认拦截；关键系统包禁止卸载/清数据；文件操作限定 `/sdcard` `/storage` `/mnt` `/data/local/tmp`；默认只监听 `127.0.0.1`
- **局域网访问**：可选开启，6 位访问码鉴权（cookie + query 双通道）

### 修复

- 目录列表遇到软链接（如 `/sdcard`）时解析为空
- 无头浏览器下连接区输入框塌缩
- APK 中文名（`application-label-zh-CN`）解析

### 已知问题

见 [路线图 · 已知问题](docs/ROADMAP.md#已知问题)。其中需要优先注意的是「`aapt2` 缺失时 HOME 护栏失效」。

[Unreleased]: https://github.com/<your-account>/tv-installer/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/<your-account>/tv-installer/releases/tag/v0.1.0
