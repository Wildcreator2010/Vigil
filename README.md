# Vigil

**Vigil** —— 作者 Wildcreator。同一条产品线上出**两个版本**，靠代号区分：

| 代号 | 版本号 | 状态引擎 | 载荷 |
| --- | --- | --- | --- |
| *Vachellia farnesiana* | 1.0.0 | 随包的 CPython 跑 `dsh_state.py` | 184MB / zip 82.6MB |
| *Lilium* | 1.1.0 | `vigil-engine.exe`（C++，自带 zstd 解压） | 161MB / zip 70.5MB |

两版**可以在同一台机器上同时装着**（各自 `Programs\Vigil-<代号>`、各自卸载项），但
**同时只能开一版**：第二个开起来的会弹一个说明窗，主按钮直接把在跑那一版的面板叫出来。
设置与余额 Key 两版共用（`%LOCALAPPDATA%\Vigil`），开机自启也只有一个位置。

嵌在 Windows 任务栏里的 dsh 会话守望条 + 控制台。

原名「dsh 任务栏状态栏 / dsh-status」，2026-10-04 改名 Vigil；数据目录与开机自启的注册表值名一起换，老数据首次启动自动搬迁。

把状态栏**嵌进 Windows 任务栏里面**（不是浮在上方），实时显示 **DeepSeek Harness Desktop**
在做什么，右侧显示 **DeepSeek 账户余额**；另有一个按状态着色的托盘图标。

```
┌─ 任务栏 ─────────────────────────────────────────────────────────────────────────┐
│ [● 待命 · FurBoard · T47/S10 · 任务 5/6            --]  ⊞  ▷ …   托盘 19:32   │
└──────────────────────────────────────────────────────────────────────────────────┘
```

- 状态：正在思考 / 正在回答 / 正在执行工具 / 回答完成 / 待命 / 需要操作 / 出错了 / 已中断 / 疑似卡住 / 未运行
- 「需要操作」把问题正文直接写在栏上，并弹系统通知，处理前按间隔重复提醒
- 余额走官方接口 `GET https://api.deepseek.com/user/balance`
- 实现栈：C# WPF（.NET 10 桌面运行时，本机已装，首次编译需要联网还原 NuGet 包）+ C++ 状态引擎（`engine/vigil_engine.cpp`，自带 zstd 解压）；`dsh_state.py` 留作对照基准与回退级
- 交互与停靠方式参考 [AF-Media-Bar](https://github.com/Fervent-Tempo/AF-Media-Bar)

## 快速开始

```bat
engine\build.cmd :: 编 C++ 状态引擎（要 MSVC；不编的话开发期自动退到 python 那一级）
build.cmd        :: dotnet build -c Release（开发期：框架依赖，只在装了 .NET 10 SDK 的机器上跑）
start-bar.bat    :: 启动 bar\bin\Release\net10.0-windows\Vigil.exe
package.cmd      :: 出可分发到任何电脑的零前置安装包（默认 Lilium 那版；python / both 见下）
```

`build.cmd` 和 `package.cmd` 是两条路，别混：前者 6.7MB、秒编、要求本机有运行时；
后者 161MB 自包含 + 0.5MB 引擎、要跑几分钟、产物什么都不要求。
（工程文件里的默认身份是**当前分支**那一版：`Lilium` / 1.1.0。出另一版不用切代码，
`package.cmd python` 只是把发布参数换成 Vachellia 那一套 —— 产品代码一行不动。）
（开发期没跑 `engine\build.cmd` 时，`Vigil.exe --engine-probe` 会报 `kind=python` —— 那
是回退级在工作，不是坏，但冒烟里那七条 C++↔Python 对照会红，因为引擎没编出来。）

启动前可以先验证引擎能读懂你机器上的 dsh 会话：

```bat
python test_dsh_state.py --live
```

改动后跑冒烟（61 项断言，覆盖 README 里每条命令、11 个状态的 `--demo`、余额 Key 存取、
编译零警告，以及任务栏停靠与像素合成）：

```bat
python smoke_test.py           :: 引擎与 CLI 契约，无副作用
python smoke_test.py --setup   :: 编译安装向导 + 开一次窗口读控件 + 静默装到 %TEMP% 再卸载
python smoke_test.py --package :: 校验 dist/ 分发产物（每一版按自己的形状验、两个 exe 版本对齐）
python smoke_test.py --coexist :: 两版并存 / 遗留单版迁移 / 第二实例的告知窗（要 dist/ 真产物）
python smoke_test.py --all     :: 上面全部 + Release 编译 + 状态栏 GUI 冒烟
```

`--gui` 会真的往任务栏里挂一次状态栏，结束时强杀掉，不会留下实例。

## 装到任何电脑

前提：**Win10 1607+ / Win11，x64**。不需要预装 .NET，不需要预装 Python，不需要管理员权限；
装到目标机的全过程不碰网络。注意区分：**打包**那台机器要联网（首次拉 NuGet 的运行时包），
目标机什么都不用下。支持面到此为止 —— .NET 10 的 WPF
不支持 Win7/8。

```bat
package.cmd cpp                 :: 默认：Lilium 那一版 → dist\Vigil-1.1.0-lilium-win-x64\ + 同名 zip
package.cmd python              :: Vachellia 那一版 → dist\Vigil-1.0.0-vachellia-farnesiana-win-x64\
package.cmd both                :: 两版各出一份（先 python 后 cpp）
package.cmd --verify-only       :: 只校验已有产物（冒烟走这条，不重新 publish）
```

版本参数（版本号 / 代号 / slug）集中在 `tools/flavors.py`，打包与产物校验读的是同一张表；
出包前会先校 SHA-256 再解压那份 embeddable 解释器，不会把没核对过的下载物发给别人。

把 zip 拷到目标机 → **整个文件夹解压出来**（别在压缩包里双击）→ 双击 `Vigil-Setup.exe`。
没代码签名，SmartScreen 会拦一次，点「更多信息 → 仍要运行」。装到
`%LOCALAPPDATA%\Programs\Vigil-<slug>`（两版各占一个目录），只写 `HKCU`，装完在「设置 → 应用」
里能看到 `Vigil Lilium` / `Vigil Vachellia farnesiana` 两条，各自能卸载。
分版本之前那套单版安装（`Programs\Vigil`）在装任何一版之前都会被自动迁到 Vachellia 的身份上，
注册表与快捷方式跟着改名，设置与余额 Key 一个字节都不动。

命令行 / 无人值守：

```bat
Vigil-Setup.exe /S /D=D:\Vigil                :: 静默安装
Vigil-Setup.exe /UNINSTALL /S                 :: 静默卸载（默认保留设置与余额 Key）
Vigil-Setup.exe /S /NO-AUTOSTART /NO-START    :: 不写开机自启、装完不启动
```

里面装了什么：自包含的 .NET 10 运行时（259 个文件）+ 状态引擎 + `dsh_state.py` + 安装向导。
引擎那一格按版本不同：**Lilium** 是 `vigil-engine.exe`（0.5MB 的 C++，zstd 解压子集编在它
自己怀里，载荷 161MB），**Vachellia** 是 `app\runtime\python\` 那份 embeddable CPython
（24MB，载荷 184MB），`dsh_state.py` 在前者是回退级、在后者就是引擎本体。
第三方许可证原文随附在 `licenses\`，收哪一份看**这个目录里实际有什么**，
见 [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) 第 4、5 节。

想单独用引擎，不必装任何东西（装在系统里的那份在 `Programs\Vigil-Lilium\`）：

```bat
%LOCALAPPDATA%\Programs\Vigil-Lilium\vigil-engine.exe --pretty    :: 详细视图
%LOCALAPPDATA%\Programs\Vigil-Lilium\vigil-engine.exe --json      :: 一行机器可读快照
%LOCALAPPDATA%\Programs\Vigil-Lilium\vigil-engine.exe --states    :: 状态图例
```

## 怎么嵌进任务栏的

关键在 `bar/Native.cs` 的 `Dock()` / `Place()`，三条缺一不可：

1. **窗口必须是分层窗口**（带 `WS_EX_LAYERED`）。WPF 里由 `AllowsTransparency = true` 提供。
   普通 GDI 窗口（WinForms，或裸 `HwndSource`）即使停靠成功、`WindowFromPoint` 命中自己，
   像素也不会合成到任务栏的 XAML island 之上 —— 这正是之前几轮「命中看得到、画面看不到」的原因。
2. **先改样式，再换父窗口**：
   `SetWindowLong(GWL_STYLE, (style & ~WS_POPUP) | WS_CHILD)` → `SetParent(hwnd, Shell_TrayWnd)`。
   顺序反过来（先 `SetParent` 再加 `WS_CHILD`）会留下半吊子状态。
3. **坐标换算成父窗口客户区**：`ScreenToClient(Shell_TrayWnd, pt)` 之后
   `SetWindowPos(..., NOZORDER|NOACTIVATE|SHOWWINDOW)`。直接塞屏幕绝对坐标会把窗口推到屏幕外。

水平位置由 `Native.FreeRange()` 算：读 `Start` / `MSTaskSwWClass` / `TrayNotifyWnd` 三个子窗口矩形，
优先放在开始按钮左侧，放不下再退到应用图标组与托盘之间，因此不会盖住开始按钮、图标或时钟。
每 2 秒的 watchdog 重算一次：换分辨率、任务栏移动、explorer 重启（`Shell_TrayWnd` 句柄变化）都会自动重新停靠。

> 附带结论：Win11 已不为第三方 AppBar 保留工作区（`ABM_NEW` 成功、`ABM_QUERYPOS` 能回位置，
> 但 `SPI_GETWORKAREA` 不收缩）。这里不需要空间保留，因为窗口本身就是任务栏的子窗口。

## 视觉

跟随系统深浅色（`Theme.cs` 读 `SystemUsesLightTheme`）：深色任务栏上是半透明深色胶囊 + 白字，
浅色任务栏上是白胶囊 + 深字，不会在深色栏上顶出一块突兀的白盒。

- 左侧 3px 状态色条 + 状态圆点（字母徽标，字色按底色亮度自动取黑/白）
- 活动态（思考 / 回答 / 执行工具）圆点换成 4 根跳动的活动条，静止态恢复圆点
- 需要操作 / 出错时圆点外圈呼吸光，正文用告警色
- 待办进度是一条细进度条（完成数 / 总数），不再是密密麻麻的方块
- 正文过长按可用宽度截断并显示省略号；悬停时展开补间出更多空间显示完整问题正文
- 悬停展开靠轮询光标位置实现：挂在 explorer 任务栏下的非活动窗口收不到 `MouseEnter`

## 各状态视觉自检

```bat
Vigil.exe --demo needs_action
Vigil.exe --demo thinking
python dsh_state.py --demo error --json
```

`--demo <state>` 用你机器上真实会话的数据伪造指定状态（正文/项目都是真的，只有状态是假的），
用来肉眼核对配色、圆点、活动条、告警色，不用等真的出错。

## 交互

| 操作 | 效果 |
| --- | --- |
| 左键单击状态栏 | 切到 DeepSeek Harness 窗口 |
| 右键单击状态栏 | 打开菜单（与托盘图标同一个） |
| 滚轮 | 在当前会话与其他活跃/待处理会话间循环查看，4 秒后回到主状态 |
| 鼠标悬停 | 展开显示完整待处理正文 / 最近工具名 |
| 托盘双击 | 切到 Harness；托盘右键 = 菜单 |

菜单：状态 / 项目 / 余额 / 详情 / 立即刷新余额 / 设置余额 Key… / 切到 DeepSeek Harness /
重启检测进程 / 开机自动启动 / 打开日志 / 退出。

## 状态含义

| 图标 | 状态码 | 含义 | 判定依据（会话事件流） |
| --- | --- | --- | --- |
| `!` 红 | `needs_action` | **需要操作**：等你授权或回答问题 | `approval/asked` 无配对 `approval/decided`；`ask_user_question` / `exit_plan_mode` 的 `tool/call` 无 `tool/result`；或 `turn/end` 的 `reason.kind = blocked` |
| `T` 橙 | `thinking` | 正在思考 | 轮次未结束，最后事件是 `step/start` / `tool/result` / `approval/decided` |
| `A` 蓝 | `answering` | 正在回答 | 轮次未结束，最后一条 `assistant/message` 只含文本块 |
| `W` 青 | `tool_running` | 正在执行工具 | 最后一条 `assistant/message` 带 `tool-call`，或 `tool/call` 尚无结果 |
| `D` 绿 | `done` | 回答完成 | `turn/end = completed` 且 25 秒内 |
| `I` 灰 | `idle` | 待命 | 轮次已结束且超过 25 秒 |
| `X` 玫红 | `error` | 出错了 | `turn/end = error`（附错误摘要） |
| `K` 灰蓝 | `aborted` | 已中断 | `turn/end = aborted` |
| `S` 紫 | `stalled` | 疑似卡住 | 轮次开着但 2 分钟没有写入 |
| `O` 浅灰 | `offline` | 未运行 | 没有 `DeepSeek Harness.exe` 进程 |

多会话同时在跑时按「需要操作 > 出错 > 卡住 > 执行中 > 完成 > 待命」取最需要你的那个，其余列在「详情」里。

## 余额

优先级从高到低：

1. 环境变量 `DEEPSEEK_BALANCE_KEY`，其次 `DEEPSEEK_API_KEY`
2. DPAPI 加密保存（推荐）：菜单「设置余额 Key…」（打开控制面板的「余额」页），或命令行
   ```bat
   python dsh_state.py --init-balance      :: 交互式录入，输入不回显
   python dsh_state.py --clear-balance-key
   python dsh_state.py --balance           :: 只查余额
   ```
3. 明文文件 `仓库根/balance.key`（改名前是 dsh-status/，按目录实际名字找）

Key 存在 `%LOCALAPPDATA%\Vigil\balance.protected`，用当前 Windows 账户的 DPAPI 加密，不是明文；
换电脑或换账户要重新录入。余额默认缓存 5 分钟，菜单「立即刷新余额」写 `refresh.token` 让引擎强制重查。

## 文件

| 文件 | 作用 |
| --- | --- |
| `dsh_state.py` | 状态引擎：读会话、判状态、查余额。纯标准库，可独立用，也是 WPF 端的数据源 |
| `bar/Native.cs` | Win32 互操作 + 任务栏停靠/落位/避让/光标轮询/解除停靠 |
| `bar/Theme.cs` | 深浅色探测与配色（胶囊、文字、分隔线、进度槽） |
| `bar/BarWindow.cs` | 状态栏窗口（分层 WPF Window）与视觉树、悬停/滚轮/点击 |
| `bar/StateClient.cs` | 常驻子进程 `dsh_state.py --watch`，独立线程读 NDJSON，退出自动重启 |
| `bar/App.cs` | 入口、托盘图标、通知、watchdog、菜单、开机自启、日志 |
| `bar/Settings.cs` | 设置持久化（`settings.json`：轮询间隔、通知开关、重复间隔、主题、是否显示状态条） |
| `bar/Panel/` | 控制面板（控制台）：左侧导航 + 概览/通知/外观/运行/余额/关于六页，余额 Key 在「余额」页录入 |
| `test_dsh_state.py` | 16 个状态用例 + 端到端临时会话目录断言，`--live` 加真实数据冒烟 |
| `smoke_test.py` | 冒烟工具：CLI 契约 + `--demo` 全状态 + 余额 Key 存取，`--build`/`--gui` 加编译与停靠校验，`--package`/`--coexist` 加产物与两版并存校验 |
| `build.cmd` `start-bar.bat` | 开发期编译 / 启动 |
| `setup/` | 安装向导（net48 纯代码 WPF，26KB；`Installer.cs` 干活、`Ui.cs` 画界面、`Program.cs` 分参数） |
| `package.cmd` | 出零前置分发物：`[cpp\|python\|both]` 两版各一套 —— 自包含 publish + 随包引擎（C++ exe 或 embeddable CPython）+ 收许可证原文 + 按 flavor 校验 + 压 zip |
| `tools/` | 常设脚本：`flavors.py`（两个版本的版本号/代号/slug 唯一出处）、`verify_package.py`（产物校验，含"这一版得自认是哪一版"）、`collect_licenses.py`（按载荷实际内容收第三方许可证原文）、`read_version.py`、`write_readme_install.py`（按 flavor 生成安装说明）、`shot_window.py`（把真窗口的「Vigil 控制台」抓到前台截屏，专查离屏 `--panel-shot` 看不见的 DWM 背衬与标题栏） |
| `LICENSE` | 本项目自身的 MIT 许可证全文 |
| `THIRD-PARTY-NOTICES.md` | 第三方组件与素材的归属声明（含 WPF-UI 及其传递依赖的许可证原文） |

运行时数据在 `%LOCALAPPDATA%\Vigil\`：`settings.json`、`bar.log`、`balance.protected`、`refresh.token`。

## 命令行用法

```bat
python dsh_state.py                     :: 人类可读快照
python dsh_state.py --json              :: 单行 JSON
python dsh_state.py --pretty            :: 详细视图
python dsh_state.py --state-only        :: 只要状态码
python dsh_state.py --watch --interval 2 :: NDJSON 持续输出（状态栏就是这么用的）
python dsh_state.py --states            :: 状态图例，每行一个 JSON
```

## 工作原理与边界

DeepSeek Harness 把每个会话的完整事件流写在
`~/.dsh/sessions/<项目>/<会话>/session.v4.jsonl.zstd`，每条 JSONL 记录单独压成一个 zstd 帧。
引擎只读这些文件（不注入进程、不调它的本地接口），按 (文件大小, mtime) 缓存解析结果，
稳态单轮扫描几毫秒，不干扰 Harness。

因为 dsh 在**一条消息完成时**才落盘、没有逐 token 流，「正在思考」和「正在回答」的区分是推断性的：
轮次未结束且最后一条是纯文本 `assistant/message` 时报「正在回答」，其余等模型的情形报「正在思考」。
阈值都在 `dsh_state.py` 顶部（`ACTIVE_WINDOW`、`DONE_WINDOW`、`STALL_WINDOW`、`WAIT_PROMOTE_WINDOW`）。

## 常见问题

- **状态栏没出现**：看 `%LOCALAPPDATA%\Vigil\bar.log` 有没有「引擎已启动」和异常。
  任务栏若在左右两侧，`FreeRange` 不适用，会退回工作区底部。
- **托盘显示「未运行」但 Harness 开着**：按进程名 `DeepSeek Harness.exe` 判断，改了安装名就改 `APP_EXES`。
- **状态一直「待命」**：`python dsh_state.py --pretty` 会显示最后事件与静默秒数，确认 `~/.dsh/sessions/` 在动。
- **余额报 401**：Key 无余额查询权限或已失效，到平台控制台 API Keys 页确认。
- **`No module named 'compression'`**：那是 Python 低于 3.14。用 `package.cmd` 出的安装包
  不会遇到 —— 随包的 `vigil-engine.exe` 自带解压能力。只有你**手动**拿系统 Python
  跑回退级 `dsh_state.py` 时才需要 3.14；不确定它认的是哪一级就 `Vigil.exe --engine-probe`，
  正常答案是 `kind=native` 加上安装目录里那份 `vigil-engine.exe`。
- **换电脑后余额不显示**：Key 用当前 Windows 账户的 DPAPI 加密，换机器/换账户必然读不出来，
  重新录一次即可（这是 DPAPI 的设计，不是 bug）。
- **开机自启**：菜单勾选即可（写 `HKCU\...\Run` 的 `Vigil` 值指向 `Vigil.exe`），取消勾选删除。
- **停掉状态栏**：托盘菜单「退出」；命令行用 `taskkill /f /im Vigil.exe`（引擎子进程靠 stdout 管道
  断裂随后自行退出，不会留孤儿）。不带 `/f` 的优雅关闭**无效**：窗口已经是 `Shell_TrayWnd` 的子窗口，
  `taskkill` 枚举不到它，会静默返回成功而进程照跑；`ShutdownMode.OnExplicitShutdown` 也让关窗口不停 app。

## 开源协议

本项目以 **MIT 许可证**开源（见 [`LICENSE`](LICENSE)）；所有第三方组件、字体与设计参照的归属声明见
[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)——随产物分发的每一条都附了许可证原文。
零前置安装包会把 .NET 运行时二进制与 vendored 的 zstd 解压子集（编在 `vigil-engine.exe` 里）
一起带走，因此这两者的
许可证原文也随包分发在 `licenses\`（`.NET` 是 MIT、`zstd` 是 BSD-3-Clause），
由 `tools/collect_licenses.py` 自动收集、`tools/verify_package.py` 断言齐全。

## 致谢

- [AF-Media-Bar](https://github.com/Fervent-Tempo/AF-Media-Bar) —— 任务栏停靠方式与控制台分组/行布局的**设计参照**，本项目未复制其任何源代码
- [WPF-UI](https://github.com/lepoco/wpfui)（lepo.co，MIT）—— 控制台面板的 Fluent 外壳与表单控件，其内含的 5 项传递依赖一并记录在 `THIRD-PARTY-NOTICES.md`
