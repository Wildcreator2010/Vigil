# Vigil

你的 DeepSeek Harness 在跑什么，抬眼看一下任务栏就知道。

Vigil 把自己**塞进 Windows 任务栏里面**（不是飘在上面的一块小工具），实时显示每个 dsh
会话的状态，右边顺手报一下账户余额。它不说话、不弹窗催你，只在真的需要你点头的时候，
把问题原文写在栏上。

```
┌─ 任务栏 ─────────────────────────────────────────────────────────────┐
│ [● 正在回答 · FurBoard · T47/S10 · 任务 5/6           ¥12.40]  ⊞ ▷ … │
└──────────────────────────────────────────────────────────────────────┘
```

- **它读的是磁盘上的会话事件流**，不注入进程、不调 Harness 的本地接口 —— 所以你正常用
  Harness，Vigil 只是在旁边看着，稳态一轮扫描几毫秒。
- 10 种状态：正在思考 / 正在回答 / 正在执行工具 / 回答完成 / 待命 / 需要操作 / 出错了 /
  已中断 / 疑似卡住 / 未运行。多会话同时在跑时，它挑**最需要你的那一个**显示。
- 完全本地，零前置：不需要 .NET、不需要 Python、不需要管理员权限。

## 两个版本，一台机器都能装

| 代号 | 版本 | 状态引擎 | 下载体积 |
| --- | --- | --- | --- |
| **Vachellia farnesiana** | 1.0.0 | 随包的 CPython 跑 `dsh_state.py` | zip 82.6MB |
| **Lilium** | 1.1.0 | `vigil-engine.exe`（C++，zstd 解压编在自己怀里） | zip 70.5MB |

Lilium 是把引擎重写成一棵 C++ 单文件的结果：**载荷少 24MB，单帧快照从 2279ms 掉到
1751ms**。Vachellia 保留下来，因为它是行为基准 —— 那七条 C++↔Python 对照门禁
（`engine/compare_*.py`）就是拿它逐字段钉住 C++ 那一版的。

两份源码完全相同，差别只在发布参数上，所以任何一次数字变化都能归因到打包，而不是代码。

- 两版**可以同时装着**（各自的 `Programs\Vigil-<代号>`、各自的卸载项），但**同时只能开一版**：
  第二个开起来的会弹个窗口说清状况，主按钮直接把在跑那一版的面板叫出来。
- 设置和余额 Key 两版共用，换版本不用重录 Key。开机自启也只有一个位置，装第二版时
  向导会问自启交给谁 —— 而且这个开关随时能在面板里关掉。

## 装好它

去 Releases 下载对应版本的 zip，**整个文件夹解压出来**（别在压缩包里双击安装），
然后跑 `Vigil-Setup.exe`。

没做代码签名，所以 SmartScreen 会拦一次：「更多信息」→「仍要运行」。装完任务栏上就有条了，
托盘里还有个按状态变色的图标。只写当前用户（`%LOCALAPPDATA%` 与 `HKCU`），全程不弹 UAC。

系统要求：Win10 1607+ / Win11，x64。.NET 10 的 WPF 不支持 Win7/8，这条没得商量。

命令行装也支持，无人值守照样跑得动：

```bat
Vigil-Setup.exe /S /D=D:\Vigil                 :: 静默装到指定目录
Vigil-Setup.exe /S /NO-AUTOSTART /NO-START     :: 不写自启、装完不启动
Vigil-Setup.exe /UNINSTALL /S                  :: 静默卸载（默认留着你的设置与 Key）
```

## 怎么用

| 你对状态栏做什么 | 它干什么 |
| --- | --- |
| 左键点一下 | 把 DeepSeek Harness 窗口切到前台 |
| 右键点一下 | 出菜单（跟托盘那个是同一个） |
| 滚轮 | 在其他活跃/待处理会话之间轮着看，4 秒后回到主状态 |
| 鼠标悬停 | 栏身变宽，把完整的待处理问题正文摊给你看 |
| 再开一次 Vigil | 已经有面板要开了就静默叫出来；空手再开就弹「只能开一个」那个窗 |

想先看看各个状态长什么样，不用真的把自己搞出错：

```bat
Vigil.exe --demo needs_action
Vigil.exe --demo thinking
```

`--demo` 拿你机器上真实会话的数据伪造指定状态 —— 正文、项目名都是真的，只有状态是假的。

## 余额

Key 用当前 Windows 账户的 DPAPI 加密存在本地，不是明文（托盘菜单 →「设置余额 Key…」，
或面板的「余额」页）。**换电脑、换账户必然读不出来，重录一次就行** —— 这是 DPAPI 的设计，
不是 bug。余额默认缓存 5 分钟，菜单里「立即刷新余额」可以硬刷。

取 Key 的优先顺序：环境变量 `DEEPSEEK_BALANCE_KEY` → `DEEPSEEK_API_KEY` → DPAPI 存档 →
仓库根的 `balance.key`。报 401 就是这把 Key 没有余额查询权限或者已过期。

## 给改代码的人

```bat
engine\build.cmd   :: 编 C++ 引擎（要 MSVC；不编就自动退回 Python 那一级）
build.cmd          :: 开发期编译，6.7MB，要求本机装了 .NET 10 运行时
start-bar.bat      :: 起一个状态栏试试
package.cmd both   :: 出两版零前置安装包（cpp / python / both）
```

改完跑冒烟，别裸提交：

```bat
python smoke_test.py           :: 引擎与 CLI 契约，无副作用
python smoke_test.py --all     :: 全量：编译零警告 + 安装/卸载 + 产物校验 + 任务栏真停靠
python smoke_test.py --coexist :: 两版并存 / 遗留迁移 / 告知窗（要先有 dist/ 产物）
```

冒烟跑起来是 480 条断言：README 里写的每条命令都真被执行一遍，10 个状态逐个 `--demo`，
安装器真装真卸到 `%TEMP%`，两版产物各自按自己的形状校验，最后还会往你的任务栏上挂一次
状态栏（结束时强杀，不留实例）。编译警告按错误处理：`/W4 /WX`，第三方 vendored 代码除外。

引擎想单拎出来用也随你，它不依赖任何界面：

```bat
vigil-engine.exe --pretty      :: 详细视图
vigil-engine.exe --json        :: 一行机器可读快照
vigil-engine.exe --watch       :: 每 2 秒一行 NDJSON（状态栏就是这么吃数据的）
```

## 它是怎么塞进任务栏的

三个条件缺一个都只会得到「命中看得到、画面看不到」那种诡异现象（都踩过）：

1. 窗口必须分层（`WS_EX_LAYERED`），WPF 里由 `AllowsTransparency = true` 给。普通 GDI 窗口
   就算停靠成功，像素也不会合成到任务栏的 XAML island 之上。
2. **先改样式再换父窗口**：去掉 `WS_POPUP`、加上 `WS_CHILD`，然后才 `SetParent(hwnd, Shell_TrayWnd)`。
   反过来的顺序会留下一个半吊子状态。
3. 坐标要换算成父窗口客户区（`ScreenToClient`），直接塞屏幕绝对坐标会把窗口推到屏幕外面。

水平位置由 `Native.FreeRange()` 现算：读开始按钮、应用图标组、托盘三块矩形，能塞就塞在开始
按钮左边，塞不下就退到图标组和托盘之间 —— 所以它不会盖住开始按钮、图标或时钟。每 2 秒重算一次，
换分辨率、把任务栏挪边、甚至 explorer 重启，它都会自己重新停靠回来。

顺便一条实测结论：Win11 已经不给第三方 AppBar 保留工作区了（`ABM_QUERYPOS` 还答你，
但 `SPI_GETWORKAREA` 不收缩）。这里用不上空间保留 —— 窗口本来就是任务栏的儿子。

## 里面都是什么

| 位置 | 干什么 |
| --- | --- |
| `engine/vigil_engine.cpp` | C++ 状态引擎，一棵单文件，含 vendored 的 zstd 解压子集 |
| `dsh_state.py` | Python 引擎：C++ 的行为基准，也是回退级 |
| `bar/` | WPF 状态栏 + 托盘 + 控制台面板（概览/通知/外观/运行/余额/关于六页） |
| `bar/Native.cs` | Win32 互操作与停靠、落位、避让、光标轮询 |
| `setup/` | 安装向导：net48 纯代码 WPF，一个 26KB 的 exe |
| `package.cmd` + `tools/` | 出包、按 flavor 校验产物、收第三方许可证原文 |
| `smoke_test.py` | 那 480 条断言 |
| `engine/compare_*.py` | C++↔Python 逐字段对照，每条都做过变异验证 |

运行时数据都在 `%LOCALAPPDATA%\Vigil\`：`settings.json`、`bar.log`、`balance.protected`。
状态栏没出来就先看 `bar.log`。要停掉它用 `taskkill /f /im Vigil.exe` —— **不带 `/f` 没用**：
窗口是 `Shell_TrayWnd` 的子窗口，`taskkill` 枚举不到它，会客气地返回成功而进程照跑。

## 开源协议

MIT（见 [`LICENSE`](LICENSE)）。第三方组件、字体与设计参照的归属声明在
[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)，凡随安装包分发的那几项（.NET 运行时
MIT、vendored zstd BSD-3、以及 Vachellia 那份 embeddable CPython 的 PSF）都附了许可证原文，
由 `tools/collect_licenses.py` 按**包里实际有什么**自动收齐，缺一份就出不了包。

## 致谢

- [AF-Media-Bar](https://github.com/Fervent-Tempo/AF-Media-Bar) —— 停靠方式与面板布局的**设计参照**，没有复制它一行代码
- [WPF-UI](https://github.com/lepoco/wpfui)（lepo.co，MIT）—— 控制台的 Fluent 外壳与表单控件
