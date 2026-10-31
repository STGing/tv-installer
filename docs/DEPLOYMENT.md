# 实施文档

> 面向「把工具跑起来并稳定用下去」的人。开发相关看 [开发文档](DEVELOPMENT.md)。

## 1. 适用场景

- 一台电脑，通过 Wi-Fi 管理一台或多台（分次连接）Android TV / 电视盒子
- 典型对象：TCL、雷鸟、小米、索尼等开了 ADB 调试的电视
- 主要用途：给电视装第三方 APK（尤其是应用商店里没有的）、更新、卸载、回看已装应用、在电视与电脑之间传文件

## 2. 环境要求

| 项 | 要求 | 检查方式 |
|---|---|---|
| Python | 3.8 或更高 | `python --version` |
| adb | Android Platform-Tools，任意较新版本 | `adb version` |
| aapt2（**必需**） | 来自 Android SDK Build-Tools | `aapt2 version` |
| 网络 | 电脑与电视同一局域网，且未被 AP 隔离 | `ping <电视IP>` |
| 端口 | 本机 `8770` 可用；用 tcast 通道还需 `8765` 可用 | 见下方「端口冲突」 |

### 2.1 安装 adb

三种方式任选：

**A. 已有 Android SDK** — 确认环境变量，工具会自动找到：

```bash
# Windows（PowerShell，永久生效）
setx ANDROID_HOME "C:\Users\<你>\AppData\Local\Android\Sdk"

# macOS / Linux（写进 ~/.zshrc 或 ~/.bashrc）
export ANDROID_HOME="$HOME/Library/Android/sdk"
export PATH="$ANDROID_HOME/platform-tools:$PATH"
```

**B. 只用 Platform-Tools** — 从 [官方页面](https://developer.android.com/studio/releases/platform-tools) 下载压缩包，解压后把目录加进 `PATH`。

**C. 已有别的 adb** — 比如某些电视助手自带的 `adb.exe`。不用改环境变量，启动后在界面「设置」里把 `adb` 路径填成它的绝对路径即可。

> 工具不内置 adb，也不下载 adb。

### 2.2 关于 aapt2

**这不是可选组件，务必装上。** 原因不只是「看不到包名版本」：

| 缺 aapt2 的后果 | 严重程度 |
|---|---|
| 看不到包名、版本号、中文名 | 影响体验 |
| 无法判断「新装 / 更新 / 降级」 | 影响体验 |
| **无法识别 `category.HOME` → 桌面类护栏静默失效** | ⚠️ 安全相关 |
| **无法判断签名与已装版本是否一致** | 装到一半才报错 |

护栏失效的具体表现：装一个桌面类 APK 不会有任何提示，直接装进去，然后电视开机弹「选择桌面」——而你完全不知道是这个工具放进去的。

获取方式：装 Android SDK Build-Tools，或从 [Build-Tools 归档](https://dl.google.com/android/repository/build-tools_r34-windows.zip) 解压后把 `aapt2.exe` 所在目录加进 `PATH`。装完启动服务，确认 `/api/state` 返回的 `aapt` 字段不为 `null`。

## 3. 部署

### 3.1 获取代码

```bash
git clone https://github.com/STGing/tv-installer.git
cd tv-installer
```

或者直接下载 ZIP 解压。

### 3.2 启动

**Windows**：双击 `start.bat`。脚本会检查 Python、切换工作目录、启动服务，出错时停住让你看提示。

**macOS / Linux**：

```bash
python3 server.py
```

启动后终端会打印：

```
==========================================================
  TV Installer 正在运行
  本机： http://127.0.0.1:8770/
  adb：  /path/to/adb
  aapt： /path/to/aapt2
==========================================================
```

浏览器会自动打开。若 `adb` 显示为 `None`，先去「设置」里填路径。

### 3.3 首次配置

1. 界面右上角 →「设置」
2. 填 **电视 IP**（端口默认 5555）
3. 确认 **adb 路径** 不是空
4. 选默认通道：一般留「自动」
5. 保存

电视 IP 怎么找：设置 → 网络 → 有线/无线网络 → 当前连接详情。或者用路由器管理页看设备列表。

## 4. 电视端配置

不同品牌路径不同，本质是三步：

1. **打开开发者选项**
   TCL / 雷鸟：设置 → 系统 → 关于 → 版本号，连点 5~7 次
   小米：设置 → 关于 → 产品型号，连点 7 次
2. **打开 ADB 调试**
   TCL：设置 → 系统 → 开发者选项 →「网络调试」或「ADB 调试」
   小米：账号与安全 → ADB 调试（需登录小米账号）
3. **记下 IP，确认与电脑同网段**

部分机型每次开机都要重新打开 ADB 调试，属于正常。

## 5. 连接电视

界面上填 IP → 点「连接」。成功后：

- 顶部设备名变成电视型号
- 终端打印 `connected to <ip>:5555`
- 状态里能看到 Android 版本、CPU 架构、分辨率

**首次连接可能要在电视上点「允许调试」**，勾选「一律允许」省事。

连接失败按这个顺序查：

| 检查项 | 命令 / 操作 |
|---|---|
| 网络通不通 | `ping <电视IP>` |
| 5555 端口开不开 | `adb connect <电视IP>:5555` |
| 端口是不是被改过 | 部分机型 ADB 端口不是 5555，在开发者选项里看 |
| 电视是否在休眠 | 按一下遥控器唤醒。深度休眠时 ADB 会断开 |
| 是否有其他程序占着 adb | `adb kill-server` 后重试；或关掉别的电视助手 |

## 6. 使用

### 6.1 安装应用

1. 「安装」页 → 把 APK 拖进虚线框（或点击选择），支持多选
2. 每个文件会显示：应用名、包名、版本、大小，以及 `新装 / 更新 / 降级 / 重装` 标签
3. 按需勾选：
   - **允许降级** — 装的版本比电视上低时勾
   - **允许装桌面类** — 装第三方桌面（当贝桌面、Emotn UI 等）时必须勾
   - **自动授权** — 装完自动给运行时权限（`-g`）
   - **自动点「同意」** — tcast 通道用，省得跑去电视上点
4. 点「开始安装」，右侧日志实时滚动

日志里每一步都能看到实际执行的命令和原始返回，失败时会给中文原因。

### 6.2 更新应用

把新版 APK 拖进去即可。工具会比对电视上的 `versionCode`：

- 显示「更新」→ 直接装，会覆盖
- 显示「降级」→ 需要勾「允许降级」
- 显示「重装」→ 版本号相同，覆盖安装

如果报 `签名与已装版本不一致`，说明这个 APK 和电视上那个不是同一个签名（比如一个是官方版一个是修改版），必须先卸载再装。

### 6.3 管理应用

「应用」页列出所有第三方应用（`pm list packages -3`），带版本号、安装/更新时间。每行操作：

- **启动** — `monkey -p <pkg> -c LAUNCHER 1`
- **停止** — `am force-stop`
- **清数据** — `pm clear`
- **卸载** — `pm uninstall`
- **导出** — 把电视上的 APK 拉回电脑

带 🔒 标记的是受保护包，卸载和清数据会被拒绝。要保护自己的桌面，在 `config.json` 的 `protected_extra` 里加包名：

```json
"protected_extra": ["com.example.mylauncher"]
```

### 6.4 文件管理

「文件」页可以浏览电视文件系统。拖入文件即上传，点文件名下载。

可写范围受限制：只能操作 `/sdcard`、`/storage`、`/mnt`、`/data/local/tmp` 下的内容。`/system`、`/data/app` 等系统目录一律拒绝——这是护栏，不是 bug。

列表里的 `.apk` 文件有「安装」按钮，可以直接装电视上的包，不用先传到电脑。

### 6.5 局域网访问（可选）

默认只监听 `127.0.0.1`，别的设备访问不到。需要手机操作时：

1. 「设置」→ 打开「允许局域网访问」→ 保存
2. **重启服务**（配置在启动时读取）
3. 终端会打印 `手机： http://<电脑IP>:8770/  访问码 123456`
4. 手机浏览器打开该地址，输入 6 位访问码

也可以命令行临时开：

```bash
python server.py --lan
```

用手机浏览器菜单里的「添加到主屏幕」，会以全屏 App 形式打开（PWA）。

> 安全提醒：访问码是唯一的防护，只在可信局域网里开。不要做公网端口映射。

## 7. 验收清单

部署完成后建议按这个顺序走一遍，全部通过说明环境没问题：

- [ ] 服务启动，终端显示 adb 与 aapt 路径都不为空
- [ ] 点「连接」后，顶部显示电视型号
- [ ] 拖入一个普通 APK，能正确显示包名和版本
- [ ] 安装成功，电视上能看到新应用并能启动
- [ ] 再装一个更高版本的同一个包，标签显示「更新」，装完版本号变了
- [ ] 「应用」页能列出刚装的应用，卸载成功
- [ ] 「文件」页能浏览 `/sdcard`，上传一个文件再下载回来，内容一致
- [ ] 尝试删除 `/system` 下的东西，被拒绝
- [ ] （可选）装一个桌面类 APK 不勾「允许装桌面类」，被拦截并有提示

## 8. 排错手册

### 8.1 服务本身

| 现象 | 原因 | 处理 |
|---|---|---|
| 双击 `start.bat` 闪退 | 没装 Python 或没加 PATH | 装 Python 勾选 Add to PATH；或手动 `python server.py` 看报错 |
| `Address already in use` | 8770 被占用 | `python server.py --port 8780` |
| 页面打不开但服务在跑 | 代理 / 防火墙 | 直接访问 `http://127.0.0.1:8770/`；关掉系统代理 |
| 界面样式错乱 | 浏览器缓存了旧 CSS | `Ctrl+F5` 强刷；或注销 Service Worker |
| 改代码后没生效 | `sw.js` 缓存 | DevTools → Application → Service Workers → Unregister |

端口被谁占了（Windows）：

```bash
netstat -ano | findstr :8770
tasklist | findstr <上面查到的PID>
```

### 8.2 设备连接

| 现象 | 原因 | 处理 |
|---|---|---|
| `找不到 adb` | PATH 里没有 | 「设置」里填 adb 绝对路径 |
| 连接超时 | 不在同一网段 / 电视休眠 / AP 隔离 | ping 通再说；路由器关掉「AP 隔离」 |
| 连接成功但立即断开 | 电视休眠策略 | 电视设置里关掉「休眠时断开网络」；或保持屏幕亮 |
| `more than one device` | 同时连着多台 | 工具内部用 `-s` 指定 serial，正常不会遇到；手动 adb 时加 `-s` |

### 8.3 安装失败

日志里会给错误码和中文建议，对照处理：

| 错误码 | 含义 | 处理 |
|---|---|---|
| `INSTALL_FAILED_VERIFICATION_FAILURE` | 电视的安装校验拒绝了 | 先判断是不是桌面类 APK，见下方说明 |
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | 同包名不同签名 | 先在「应用」页卸载，再装 |
| `INSTALL_PARSE_FAILED_INCONSISTENT_CERTIFICATES` | 同上 | 同上 |
| `INSTALL_FAILED_VERSION_DOWNGRADE` | 版本更低 | 勾「允许降级」 |
| `INSTALL_FAILED_NO_MATCHING_ABIS` | CPU 架构不匹配 | 电视是 `armeabi-v7a`，找对应版本；ARM64-only 的包装不了 |
| `INSTALL_FAILED_OLDER_SDK` | APK 要求更高系统版本 | 无解，换低版本 APK |
| `INSTALL_FAILED_INSUFFICIENT_STORAGE` | 存储不足 | 「应用」页清数据或卸载；清 `/sdcard` 缓存 |
| `INSTALL_FAILED_USER_RESTRICTED` | 电视禁止未知来源 | 电视设置里允许「未知来源应用安装」 |
| `INSTALL_PARSE_FAILED_NO_CERTIFICATES` | APK 未签名 | 重新下载官方包 |
| `INSTALL_FAILED_INVALID_APK` | 包损坏 / 不完整 | 重新下载，对比文件大小 |

**关于 `INSTALL_FAILED_VERIFICATION_FAILURE` 的判断顺序**（很容易走错方向）：

```text
① 先抓 logcat，看是谁在拒：logcat -d | grep -E "verifier|Strategy|InstallConfig"
   ├─ 出现 StrategyService / launcherStrategy → 确认是校验链，往下走 ②
   └─ 没有 → 不是这条链。本工具的 adb / pm / tcast 三条都不在链上，
             这种错误更可能来自：商店/广告侧的校验开关、APK 签名方案、
             或这台机器上另一处 hook（尚未查清）
② 这个 APK 声明 HOME 吗？（界面上会显示「桌面类」标签）
   ├─ 是 → 大概率是 launcherStrategy 拦的，两条路：
   │         · 把包名加进电视白名单，见 8.6
   │         · 或改用 adb / tcast 通道（两条都不经安装器会话）
   └─ 否 → 不太可能是 launcherStrategy（它只对 HOME 包生效），
           查 blackListStrategy 是否被厂商下发了规则
```

⚠️ 常见误区有**两个方向**：以为 `tcast` 有什么「绕过技巧」（其实是它压根不建安装器会话），以及反过来以为 `tcast` 一定会被拦（本仓库上一版文档就是这么写的，依据只有一句推断，没有实测）。判断依据是 logcat 里的拒绝者，不是通道名字。

### 8.4 多屏互动通道（tcast）

| 现象 | 原因 | 处理 |
|---|---|---|
| `连不上 <ip>:6553` | 电视没这个服务，或已休眠 | 唤醒电视；非 TCL/雷鸟机型通常没有这条通道 |
| 一直停在 `confirm_pending` | 自动点「同意」没点中 | 关掉「自动点同意」，手动在电视上点 |
| `电视端拒绝了安装` | 电视上点了拒绝 | 重试 |
| 卡住不结束 | 电视端无响应 | 「取消」，或等 900 秒超时 |

tcast 通道需要本机 `8765` 端口空闲（它要起一个临时 HTTP 服务让电视下载 APK）。被占用时日志会说「本地 HTTP 服务启动失败」。

### 8.5 文件操作

| 现象 | 原因 |
|---|---|
| 目录列表为空 | 该路径不存在或没权限（如 `/data/data`） |
| 上传失败 | 目标目录不可写；或文件超过电视剩余空间 |
| 删除被拒 | 路径不在白名单里，属于设计限制 |

### 8.6 装桌面类 APK：写电视白名单（进阶）

只有在**「必须通过电视 UI / tcast 安装」+「APK 声明 HOME」+「签名与当前桌面不一致」**三个条件同时成立时才需要这一步。普通应用、以及走 `adb` 通道的安装，都不需要动电视配置。

**先确认当前状态**（不做假设）：

```bash
# 策略链是否真的加载生效？必须以 [ 开头
adb shell content query --uri content://com.tcl.providers.config/InstallConfig | head -c 120

# 白名单里现在有哪些包
adb shell content query --uri content://com.tcl.providers.config/InstallConfig | grep -o '"launcherStrategy"[^}]*}'

# 配置库版本，与出厂一致才不会被重建
adb shell content query --uri content://com.tcl.providers.config/Version
```

**写白名单**（用设备侧的 `tcast-push.py` 同目录下的 `tcl-cfg.sh`；**别用 `content update`，它写不了 JSON**）：

> `tcl-cfg.sh` 不在本仓库里——它属于「设备研究」那条线，随具体固件调整。拿不到脚本也可以只用下面的 `content query` 手工核对状态；真要写，按 [开发文档 §8.2 的格式要求](DEVELOPMENT.md#82-策略链)自己拼 JSON 也行，但务必先备份。

```bash
# 在存放 tcl-cfg.sh 的目录下执行
./tcl-cfg.sh --dump > ic.json            # 先导出备份
./tcl-cfg.sh --json whitelist.json       # 写入（脚本会自动把裸对象包成数组）
./tcl-cfg.sh --dump | head -c 40         # 回读，必须以 [ 开头
```

`whitelist.json` 就是完整的 `config_content` 数组，结构见 [开发文档 §8.2](DEVELOPMENT.md#82-策略链)。

**三条必须知道的边界**：

| 事项 | 说明 |
|---|---|
| 普通重启不会丢 | 实测连续 3 次重启保留 |
| 固件 OTA / 恢复出厂 / 清 `com.tcl.providers.config` 数据会丢 | 整库按出厂重建，白名单变回空 |
| 写错格式（少了外层 `[]`）比不写更糟 | 整份配置不加载，校验链静默失效，且**开机日志里看不到报错** |

判断「会不会被重建」只需比版本号：库里 `Version` 与 `/vendor/etc/default_feature_config.json` 里的 `Version` 相等即安全。

> 这一步涉及电视系统配置，属于**有风险的操作**。动手前先 `--dump` 备份，出问题原样写回。原理与踩坑见 [踩坑记录 P-02](PITFALLS.md)。

## 9. 升级与卸载

**升级**：拉新代码覆盖，`config.json` 会保留。

```bash
git pull
```

如果新版本改了配置结构，启动时的 `DEFAULTS` 会自动补上缺失字段，不需要手工迁移。

**卸载**：删掉整个目录即可。工具不写注册表、不装服务、不留系统级痕迹。

**清除个人配置**：删 `config.json`（下次启动会重新生成新的访问码）。

**清理上传缓存**：`uploads/` 目录里是网页上传的 APK 暂存，可以随时清空。

## 10. 安全注意事项

1. **访问码等于控制权**。开了局域网访问后，同一网络里拿到访问码的人可以卸载你电视上的应用。不要在公共 Wi-Fi 下开。
2. **不要做公网暴露**。这个服务是给局域网设计的，没有 HTTPS、没有防暴力破解。
3. **受保护包名单只防误操作**，不防恶意。真要动系统分区，adb 本身就够用了。
4. **第三方 APK 自行甄别**。工具只负责传输和安装，不对 APK 内容做任何安全检测。
