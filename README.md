# dsh 任务栏状态栏（DeepSeek Harness Desktop 状态检测）

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
- 实现栈：C# WPF（.NET 10 桌面运行时，本机已装，首次编译需要联网还原 NuGet 包）+ Python 3.14 标准库做状态引擎
- 交互与停靠方式参考 [AF-Media-Bar](https://github.com/Fervent-Tempo/AF-Media-Bar)

## 快速开始

```bat
build.cmd        :: dotnet build -c Release
start-bar.bat    :: 启动 bar\bin\Release\net10.0-windows\DshBar.exe
```

启动前可以先验证引擎能读懂你机器上的 dsh 会话：

```bat
python test_dsh_state.py --live
```

改动后跑冒烟（61 项断言，覆盖 README 里每条命令、11 个状态的 `--demo`、余额 Key 存取、
编译零警告，以及任务栏停靠与像素合成）：

```bat
python smoke_test.py           :: 引擎与 CLI 契约，无副作用
python smoke_test.py --all     :: 加上 Release 编译和状态栏 GUI 冒烟
```

`--gui` 会真的往任务栏里挂一次状态栏，结束时强杀掉，不会留下实例。

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
DshBar.exe --demo needs_action
DshBar.exe --demo thinking
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
3. 明文文件 `dsh-status/balance.key`

Key 存在 `%LOCALAPPDATA%\dsh-status\balance.protected`，用当前 Windows 账户的 DPAPI 加密，不是明文；
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
| `smoke_test.py` | 冒烟工具：CLI 契约 + `--demo` 全状态 + 余额 Key 存取，`--build`/`--gui` 加编译与停靠校验 |
| `build.cmd` `start-bar.bat` | 编译 / 启动 |
| `LICENSE` | 本项目自身的 MIT 许可证全文 |
| `THIRD-PARTY-NOTICES.md` | 第三方组件与素材的归属声明（含 WPF-UI 及其传递依赖的许可证原文） |

运行时数据在 `%LOCALAPPDATA%\dsh-status\`：`settings.json`、`bar.log`、`balance.protected`、`refresh.token`。

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

- **状态栏没出现**：看 `%LOCALAPPDATA%\dsh-status\bar.log` 有没有「引擎已启动」和异常。
  任务栏若在左右两侧，`FreeRange` 不适用，会退回工作区底部。
- **托盘显示「未运行」但 Harness 开着**：按进程名 `DeepSeek Harness.exe` 判断，改了安装名就改 `APP_EXES`。
- **状态一直「待命」**：`python dsh_state.py --pretty` 会显示最后事件与静默秒数，确认 `~/.dsh/sessions/` 在动。
- **余额报 401**：Key 无余额查询权限或已失效，到平台控制台 API Keys 页确认。
- **`No module named 'compression'`**：Python 低于 3.14，升级或用 `py -3.14`。
- **开机自启**：菜单勾选即可（写 `HKCU\...\Run` 的 `dsh-status` 值指向 `DshBar.exe`），取消勾选删除。
- **停掉状态栏**：托盘菜单「退出」；命令行用 `taskkill /f /im DshBar.exe`（引擎子进程靠 stdout 管道
  断裂随后自行退出，不会留孤儿）。不带 `/f` 的优雅关闭**无效**：窗口已经是 `Shell_TrayWnd` 的子窗口，
  `taskkill` 枚举不到它，会静默返回成功而进程照跑；`ShutdownMode.OnExplicitShutdown` 也让关窗口不停 app。

## 开源协议

本项目以 **MIT 许可证**开源（见 [`LICENSE`](LICENSE)）；所有第三方组件、字体与设计参照的归属声明见
[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)——随产物分发的每一条都附了许可证原文，
运行时依赖（.NET、Python 标准库）不随附其代码，只写用途与版本下限。

## 致谢

- [AF-Media-Bar](https://github.com/Fervent-Tempo/AF-Media-Bar) —— 任务栏停靠方式与控制台分组/行布局的**设计参照**，本项目未复制其任何源代码
- [WPF-UI](https://github.com/lepoco/wpfui)（lepo.co，MIT）—— 控制台面板的 Fluent 外壳与表单控件，其内含的 5 项传递依赖一并记录在 `THIRD-PARTY-NOTICES.md`
