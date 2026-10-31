# 踩坑记录

> 这里记录开发和真机联调过程中**真实踩过**的坑，不是假设、不是清单凑数。
>
> 每条按「现象 → 根因 → 解决 → 教训」组织。**教训比解决更重要**——同类问题会换一副皮再来，记住判断方法比记住命令有用。

## 目录

| 分组 | 编号 | 主题 |
|---|---|---|
| **A. 判定逻辑与安全** | P-01 | `aapt2` 缺失导致桌面类护栏静默失效 |
| | P-02 | `InstallConfig` 写成裸对象，整条校验链失效 |
| **B. 设备与协议** | P-03 | 把「adb 能装」归因于增量安装 |
| | P-04 | 用「系统身份」反推安装路径：`tcast` 判错两次 |
| | P-05 | 非 `directBootAware` 的桌面导致开机卡死 |
| | P-06 | 电视休眠时 `tcast` 下载超时 |
| | P-07 | `pm disable-user` 的整包与组件级差异 |
| | P-08 | 改完系统状态立刻重启，状态丢失 |
| | P-09 | `content update --bind` 写不了 JSON |
| **C. 文件与路径** | P-10 | `ls -la` 解析 `/sdcard` 得到 0 项 |
| | P-11 | MSYS 把 `/sdcard` 改写成 Windows 路径 |
| **D. 前端与工具链** | P-12 | 无头 Chrome 截图被 SIGTERM |
| | P-13 | flex 行内输入框塌缩 |
| | P-14 | `aapt2 dump badging` 的中文应用名取错 |
| | P-15 | SSE 用数组下标同步队列，状态错位 |
| | P-16 | 后台长驻服务被判为「命令失败」 |
| | P-17 | 端口被旧进程占用，改的是「另一个实例」 |
| **E. 曾经写错的结论** | P-18 | 三条被实测推翻的早期判断 |

---

## A. 判定逻辑与安全

### P-01 · `aapt2` 缺失导致桌面类护栏静默失效

| | |
|---|---|
| **现象** | 在没装 Android SDK 的机器上，拖入一个桌面类 APK，工具**直接装进去了**，界面上没有任何「这是桌面类应用」的提示。装完电视弹桌面选择框。 |
| **根因** | `core.inspect_apk()` 靠 `aapt2 dump badging` / `xmltree` 解析 `category.HOME`。没装 `aapt2` 时解析整体失败，函数返回默认值 `home=False`。护栏的判定是 `if meta["home"] and not allow_home: 拦截`，于是 `False` 让整个拦截分支被跳过。 |
| **解决** | 把 `aapt2` 写进前置条件（README 已强调），并在 `/api/state` 暴露 `aapt` 字段供自检。 |
| **现状** | ⚠️ **仍未彻底修复**。解析失败时依然 fail-open，只是文档上说明了。计划改成 fail-closed（解析不出元信息时提示「无法确认是否为桌面类，请手动确认」），见 [ROADMAP P1](ROADMAP.md)。 |
| **教训** | **安全判定在「信息缺失」时必须往严格一侧倒**。`False` 当默认值很自然，但它在这里意味着「放行」。凡是 `if 危险条件: 拦截` 的写法，都要问一句：条件取不到值时，走的是哪条分支？ |

### P-02 · `InstallConfig` 写成裸对象，整条校验链失效

| | |
|---|---|
| **现象** | 白名单明明写进 `InstallConfig.launcherStrategy.packages` 了，连续 3 次重启回读都还在，但装桌面类 APK 依旧畅通无阻。「白名单生效」这个结论维持了几天。 |
| **根因** | `com.tcl.providers.config` 里 `config_content` 的约定是 **JSON 数组**，`InstallConfig` 被写成了**裸对象**（少了外层 `[]`）。`Config:Grabber` 解析时抛 `JSONObject cannot be converted to JSONArray`，整份配置没被加载，`LauncherStrategy` 从未构建——白名单一直在空转。 |
| **解决** | 补上外层 `[]` 与出厂结构对齐；`tools/tcl-cfg.sh` 增加自动归一（传裸对象时自动包一层并提示）。 |
| **教训** | 这是本次最值钱的一条：**「写入成功」不等于「被读取生效」**。当时只验证了写进去 + 没被覆盖，没验证「有组件把它读出来按它行事」。验证一个配置项，必须验证到**消费端行为**，而不是停在中途。 |
| **延伸** | 这条日志还有个阴险之处：`InstallConfig` 不在开机加载的 project 列表里，是安装时按需读的，所以**开机日志永远看不到这条报错**，只有真的装一次东西才会出现。详细的机制见 [DEVELOPMENT §8.5](DEVELOPMENT.md#85-隐藏陷阱config_content-必须是-json-数组)。 |

---

## B. 设备与协议

### P-03 · 把「adb 能装」归因于增量安装

| | |
|---|---|
| **现象** | 早期 `adb install -r` 报 `INSTALL_FAILED_VERIFICATION_FAILURE`，加上 `--incremental` + `.idsig` 就成功了。于是结论写成「必须用 incfs 增量安装才能过校验」。 |
| **根因** | 真正变量是**那个 APK 声明了 HOME，而当时白名单是空的**，不是安装模式。后来白名单写好（或换成熟桌面同签名），普通 `install -r` 一样成功。增量安装只是恰好走了另一条路径。 |
| **解决** | 修正文档；`channels.py` 的 adb 通道默认用普通 `install -r`，增量安装作为可选。 |
| **教训** | **两个变量同时变化时不能归因**。当时同时改了「安装方式」和「白名单状态」，只留下了前者的因果印象。做对照实验要一次只动一个变量——后来验证 `launcherStrategy` 时就是用一个全新签名、声明 HOME、全新包名的测试包，单独控制安装入口，才把因果理清。 |

### P-04 · 用「系统身份」反推安装路径：`tcast` 判错两次

| | |
|---|---|
| **现象** | 同一个问题，文档先后写过两个相反的结论：先是「`tcast` 能绕过校验」，后来改成「`tcast` 走 `PackageInstaller` 会话，一定会被拦」。两次的理由都是「多屏互动是 system 应用」。 |
| **根因** | 用**调用者身份**推断**代码路径**。第一版把「装成功了」直接当成「绕过了校验」——当时 `config_content` 正坏着，整条链 fail-open，装成功什么也证明不了。第二版错得更隐蔽：从「system 应用」推出「必然走标准安装会话」，却从没看它实际怎么装。实际证据是：确认框的 resource-id 属于它自己（`com.tcl.MultiScreenInteraction_TV:id/btn_positive`）、点完「同意」没有第二道安装界面、推送前后 logcat 也没有 `Grabber:InstallConfig` 读取记录——三条都指向「自己调安装、不建会话」。 |
| **解决** | 三条通道统一按「不在校验链上」记载，`tcast` 标注为**强推断**（缺一次对照实验：格式修好后用签名不匹配的 HOME 包走 tcast）；排错改为先抓 logcat 看拒绝者，不再按通道名字判断。 |
| **教训** | **身份 ≠ 路径，路径 ≠ 结果**。判断一个通道经不经过某层检查，只有两条路：读它调的 API，或看日志里那层真的跑起来没有。「它是系统应用，所以……」这类推理写进文档时必须标成推断。同一个结论被推翻两次，缺的从来不是措辞，是实验。 |

### P-05 · 非 `directBootAware` 的桌面导致开机卡死

| | |
|---|---|
| **现象** | 换新版本桌面后重启，卡在开机动画。`sys.boot_completed` 永远为空，`dumpsys user` 停在 `State: BOOTING`，`system_server` 在跑、`pm` / `dumpsys` 都能用，就是 UI 起不来。 |
| **根因** | 新版本删掉了带 `directBootAware` 的占位 Activity，改由普通 `MainActivity` 声明 `HOME`。这块 TCL 板子在 `systemReady` 阶段（user 0 解锁**之前**约 4.6 秒）就 resolve HOME，此时 CE 存储不可用，非 `directBootAware` 组件对解析器不可见 → `No home screen found` → 卡在 boot phase 600。 |
| **解决** | 装回带占位 Activity 的版本即可，**不需要恢复出厂**：`adb install -r` 在卡开机动画时依然可用。 |
| **教训** | 装桌面类 APK 前必须确认 `directBootAware`。好消息是这类故障不会让你变成砖——adb 是独立于 UI 的，能远程救回来。 |

### P-06 · 电视休眠时 `tcast` 下载超时

| | |
|---|---|
| **现象** | 推送后一直停在 `downloading`，最后 timeout；此前的 adb 命令明明都正常。 |
| **根因** | 电视进屏保/休眠后网络栈被限流，下载器拉不动本地 HTTP 服务。 |
| **解决** | 推之前先 `adb shell input keyevent KEYCODE_WAKEUP`，或者先让电视亮屏。 |
| **教训** | 依赖电视主动发起网络请求的通道，**都受休眠影响**；依赖电脑主动推的（adb）不受。选通道时要连「电视醒着吗」一起考虑。 |

### P-07 · `pm disable-user` 的整包与组件级差异

| | |
|---|---|
| **现象** | `pm disable-user --user 0 com.tcl.cyberui` 成功且跨重启保持；但 `pm disable-user --user 0 com.tcl.cyberui/.MainActivity` 报 `SecurityException: Shell cannot change component state ... to 3`。 |
| **根因** | shell（uid 2000）能改**整包**启用状态，但不能改系统包的**组件**状态。 |
| **教训** | 「禁用」有粒度：整包 / 组件 / 挂起（`pm suspend`）三者行为不同。想只摘掉一个 Activity 而保留包，走不通。 |

### P-08 · 改完系统状态立刻重启，状态丢失

| | |
|---|---|
| **现象** | `pm disable-user` 后马上 `reboot`，重启回来又是启用状态，一度以为「TCL 开机会自动恢复」。 |
| **根因** | 状态还没落盘就断电重启。 |
| **解决** | `disable` 之后跟一个 `force-stop`，等几秒，**回读 `enabled=` 确认**再重启。脚本里已按这个顺序写。 |
| **教训** | 改系统状态后要「写 → 回读 → 再重启」。另外这条也提醒：**「重启后没保持」有两种可能**——被系统恢复了，或者根本没写进去。别急着归因给系统。 |

### P-09 · `content update --bind` 写不了 JSON

| | |
|---|---|
| **现象** | `adb shell content update --uri ... --bind 'config_content:s:{"enable":"true",...}'` 报 `[ERROR] Binding not well formed`。 |
| **根因** | `--bind` 的 value 格式是 `column:type:value` 三段式，JSON 里的冒号把这段结构拆坏了。 |
| **解决** | 放弃 CLI，改用 `app_process` + 反射拿 `IContentProvider`，以 shell 身份直接 `update()`。 |
| **教训** | **别用 `content` CLI 传结构化数据**，它只适合简单标量。JSON / 长文本一律走 provider 直连。 |

---

## C. 文件与路径

### P-10 · `ls -la` 解析 `/sdcard` 得到 0 项

| | |
|---|---|
| **现象** | 文件管理页打开 `/sdcard` 是空的，但 `adb shell ls /sdcard` 明明有一堆东西。 |
| **根因** | 两个问题叠加：① `/sdcard` 是软链接，`ls -la` 首行是 `lrwxrwxrwx ... sdcard -> /storage/self/primary`，字段数与普通行不同，正则匹配不上或误匹配；② Android 的 `ls` 在某些 toybox 版本下输出格式略有差异，`. ` / `..` 混在结果里。 |
| **解决** | 正则兼容符号链接行（字段数可变，取行尾名字），过滤 `.` 和 `..`，解析失败时回退到 `ls -1` 只取文件名。 |
| **教训** | 解析 `ls` 输出本身就很脆。**先保证「不崩」，再追求「信息全」**——拿不到 size/mtime 也比整个目录空白好。 |

### P-11 · MSYS 把 `/sdcard` 改写成 Windows 路径

| | |
|---|---|
| **现象** | 在 Git Bash 里执行 `adb shell ls /sdcard`，报路径不存在或行为诡异。 |
| **根因** | MSYS2 / Git Bash 会把以 `/` 开头的参数当成 Unix 路径，自动转换成 `C:/Program Files/Git/sdcard` 再传给 exe。 |
| **解决** | 命令前加 `MSYS_NO_PATHCONV=1`，或对路径加双斜杠转义。 |
| **教训** | Windows 下所有「把 POSIX 路径传给原生 exe」的场景都有这个坑，不只是 adb。写文档给 Windows 用户时，命令要按 cmd/PowerShell 写，别直接贴 bash 版。 |

---

## D. 前端与工具链

### P-12 · 无头 Chrome 截图被 SIGTERM

| | |
|---|---|
| **现象** | `chrome --headless=new --screenshot=...` 命令返回了，但目标文件不存在，进程被信号终止。 |
| **根因** | 新版 Chrome headless 在某些 Windows 环境下启动后被宿主杀掉（与 `--no-sandbox`、user-data-dir 权限都试过无关）。 |
| **解决** | 换 Edge：`msedge --headless=old --screenshot=...`，稳定可用。 |
| **教训** | 截图验证界面的自动化，**备一个备用浏览器**。Edge 与 Chrome 同内核，`--headless=old` 反而更稳。 |

### P-13 · flex 行内输入框塌缩

| | |
|---|---|
| **现象** | 截图里 IP 输入框和端口输入框挤成一条细线，按钮正常。 |
| **根因** | `.row { display:flex }` 的子项里，`<input>` 没有显式宽度策略，flex 会把它压到最小内容宽度。 |
| **解决** | 给 input 加 `flex:1; min-width:0`。`min-width:0` 是关键——没有它，input 的默认最小宽度会阻止收缩。 |
| **教训** | flex 容器里的表单控件必须显式声明 `flex` 与 `min-width`。**这类问题肉眼看代码看不出来，只有截图或真机才暴露**——所以界面改完要截图核对，别只看 DOM。 |

### P-14 · `aapt2 dump badging` 的中文应用名取错

| | |
|---|---|
| **现象** | 应用名解析出英文名，或者解析出奇怪的字符串。 |
| **根因** | `badging` 输出里同时有多行 `application-label`、`application-label-zh-CN`、`application-label-en` 等，简单取第一个匹配往往不是中文。 |
| **解决** | 优先匹配 `application-label-zh-CN` / `application-label-zh`，再回退到无后缀的 `application-label`。 |
| **教训** | 本地化字段要**按优先级取**，不能「第一个匹配就用」。同理适用于 `versionName` 等多值字段。 |

### P-15 · SSE 用数组下标同步队列，状态错位

| | |
|---|---|
| **现象** | 安装完了，界面上那一行还显示「安装中」；或者第二个包的状态显示到第一行。 |
| **根因** | 前端拿后端 `job.items` 的顺序去对应本地 `S.queue` 的下标。但队列在追加/跳过时顺序会变，下标对不上。 |
| **解决** | 改成按稳定标识（APK 路径 / 包名）匹配，而不是索引。 |
| **教训** | **跨端同步数据永远不要依赖数组下标**，除非能保证两端顺序严格一致。用 ID 匹配是唯一稳的做法。 |

### P-16 · 后台长驻服务被判为「命令失败」

| | |
|---|---|
| **现象** | 后台启动 `python server.py` 的任务被标记为 failed（exit code 1），但服务其实是好的、页面能打开。 |
| **根因** | 前台阻塞型进程被当作一次性命令管理，判定逻辑按「退出码」来，而它根本没有正常退出。 |
| **解决** | 忽略这个状态，改用 `curl /api/state` 判断服务是否真起来了。 |
| **教训** | 判断服务是否就绪，**看健康检查接口，别看进程退出码**。 |

### P-17 · 端口被旧进程占用，改的是「另一个实例」

| | |
|---|---|
| **现象** | 改完代码重启服务，页面行为没变；查了半天以为改动没生效。 |
| **根因** | 新实例因端口占用启动失败退出了，浏览器打开的其实是上一个还在跑的旧实例。 |
| **解决** | 启动前先查端口占用并停掉旧进程；`start.bat` 里加了端口占用提示。 |
| **教训** | 调试时先确认**你连的是哪个实例**（`/api/state` 里带版本或启动时间）。「改了没效果」十有八九是根本没连上新的。 |

---

## E. 曾经写错的结论

这一节单独列出来，因为**错误的结论比错误的代码更危险**——代码会报错，结论只会静默地把人带偏。

| 早期结论 | 实际情况 | 纠正依据 |
|---|---|---|
| 「TCL 封死了 `adb install`，必须用增量安装」 | `adb install` 不经过 verifier，普通 `-r` 就能装；早期失败是「HOME 包 + 空白名单」的组合 | 用全新签名 + 声明 HOME + 全新包名的测试包，单独控制安装入口，`adb install -r` 直接成功，日志无 verifier 拒绝 |
| 「白名单写入成功且跨重启保持 = 白名单生效」 | 格式写成裸对象，整份配置没被加载，白名单一直空转 | `Config:Grabber` 抛 `JSONObject cannot be converted to JSONArray` |
| 「换成新桌面后开机会被 TCL 重新启用 cyberui」 | 整包 `disable-user` 跨重启保持；被还原的是**组件级**状态和 `pm uninstall --user 0` | 多次重启后 `pm list packages -d` 仍有 cyberui，`enabled=3` |
| 「`tcast` 走 `PackageInstaller` 会话，所以一定会被校验链拦」 | 多屏互动自己画确认框、自己完成安装，不建会话 → 不在校验链上 | 确认框资源 id 属于多屏互动自己；点完「同意」无第二道安装界面；推送前后无 `InstallConfig` 读取日志（P-04） |
| 「等 OTA 后配置格式会恢复正确、校验链重新生效」 | 因果反了：格式是现在就已经失效；OTA 会**重建整库**，导致白名单丢失，与格式无关 | 版本号比对（库 V1.134 = 出厂 V1.134，不触发重建） |

**共同点**：五条都犯了同一个毛病——**验证了中间环节，就当成验证了最终结果**。写进去了 ≠ 被读到了；重启后还在 ≠ 它起了作用；命令成功了 ≠ 走的是你以为的那条路径。

以后验证任何「绕过 / 生效 / 拦截」类结论，标准动作是：

1. 造一个**只在该路径上会失败**的样本（控制变量）
2. 找一个**只在该路径上会出现**的证据（日志、状态、副作用）
3. 把另一条路径**堵死或旁证排除**

三条都齐了再下结论。
