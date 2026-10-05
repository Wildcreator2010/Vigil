# Vigil 安装功能与可移植分发 —— 设计文档

日期：2026-10-05
状态：待评审
范围：把 Vigil 从"只能在这台开发机上 `dotnet build` 出来跑"变成"拷到任何 Windows 电脑都能装、能跑"，
并交付一个 GUI 安装向导（含升级与卸载）。

## 1. 背景与目标

Vigil 现在的运行前提是两件事同时成立，而它俩都只在开发机上成立：

1. **`.NET 10 Windows 桌面运行时`**：`build.cmd` 走 `dotnet build -c Release`，产物是**框架依赖**的
   6.7MB 输出（`Vigil.dll` + `Vigil.exe` + 2 个 Wpf.Ui dll），目标机没装运行时就打不开。
2. **`Python 3.14`**：引擎 `dsh_state.py:28` 无条件 `import compression.zstd`，会话文件
   `session.v4.jsonl.zstd` 逐帧解码全靠它（`:130` `zstd.ZstdFile`、`:159` `zstd.decompress`）。
   3.13 及更早没有这个模块。

更要命的是解释器发现逻辑 `bar/App.cs:704 ResolvePython()`：它在 `PATH` 里找**第一个**
`python.exe`/`python3.exe` 就返回，兜底写死 `C:\Python314`。本机 `py -0p` 显示除 3.14 外还有
uv 装的 `cpython-3.12.14`，所以它完全可能选中一个没有 `compression` 模块的解释器，
于是状态栏启动即弹「状态检测引擎不可用」（`App.cs:663` 的失败分支）。**这是一个现存 bug，
换电脑时必然暴露，本次一并修掉。**

目标：

1. 产物**零前置**：拷到任何 Win10 1607+ / Win11 x64 电脑，不需要先装 .NET、不需要先装 Python，
   不需要联网，不需要管理员。
2. 交付一个**中文 GUI 安装向导**：选目录、开机自启、快捷方式、升级/修复、卸载，
   并登记到系统「设置 → 应用」里可被正常卸载。
3. 一条命令从干净仓库复现整个分发物（`package.cmd`），并且冒烟测试能验证分发物本身可用。

非目标见 §10。

## 2. 已确认的决策

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 前置要求档位 | **零前置**（用户选定） | 目标机不装任何东西 |
| 安装形态 | **独立 GUI 安装向导**（用户选定） | 要正经的安装/卸载体验，不是解压即跑的绿软 |
| .NET 运行时 | `dotnet publish -r win-x64 --self-contained` | 实测可行，见 §3 |
| Python 运行时 | 随附 **CPython 3.14.7 embeddable** | 实测可行，见 §3；不选"引擎移植成 C#"，因为 `python dsh_state.py` 的 CLI 契约是产品功能（README「命令行用法」整节） |
| 安装向导技术栈 | **`net48` + 纯 C# 代码构建的 WPF（无 XAML）** | 实测可行，见 §3；避免自包含向导再送 161MB |
| 向导与载荷的关系 | 同目录发布，向导**不内嵌**载荷 | 95MB 资源流内嵌太脆，且无法增量替换 |
| 权限 | 全程 `asInvoker`，只写 `HKCU` 与 `%LOCALAPPDATA%` | 永不碰 UAC；`bar/app.manifest` 现状无 `requireAdministrator` |

## 3. 可行性实测（不是推断）

三条都是本机跑出来的数，写代码前必须知道的：

- **自包含 publish 成立**：`dotnet publish bar/Vigil.csproj -c Release -r win-x64 --self-contained true`
  成功，0 error 0 warning，产物 **161MB / 255 个文件**，压 zip 后 **86MB**。
  首次编译会从 nuget.org 拉 `Microsoft.WindowsDesktop.App.Runtime.win-x64` 等运行时包（本机可达，
  还原用时 54.67s）。产物里含 `createdump.exe`，无害，保留。
- **embeddable CPython 够用**：`python-3.14.7-embed-amd64.zip`（python.org 可达，HEAD 200，
  12673227 字节，`SHA-256 = d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15`）
  解压后 37 个文件 / 约 24MB，**含 `_zstd.pyd`（503520B）、`_ssl.pyd`、`_ctypes.pyd`**，标准库在
  `python314.zip` 里。用它跑真引擎通过：
  `python.exe -X utf8 dsh_state.py --json` → `{"ok": true, ..., "state": "needs_action",
  "detail": "需要操作 · chat · T6/S8 · --"}`，`--state-only` → `needs_action`。
  注意 `python314._pth` 使解释器进 isolated 模式：`sys.path` 只有 `[zip, 目录]`、不加脚本所在目录、
  忽略 `PYTHONPATH`。引擎是单文件纯标准库，实测不受影响；**今后若给引擎拆多文件，这条会咬人**。
- **net48 WPF 向导成立**：SDK 式项目 `<TargetFramework>net48</TargetFramework>` +
  `<OutputType>WinExe</OutputType>`，显式 `<Reference>` 到 `PresentationFramework`/`PresentationCore`/
  `WindowsBase`/`System.Xaml`，配 `<PackageReference Include="Microsoft.NETFramework.ReferenceAssemblies">`，
  用 `dotnet build -c Release` 编译成功，**0 警告 0 错误，产物 `Vigil-Setup.exe` 5632 字节**，
  实跑弹窗并 800ms 后自行关闭，退出码 0。**前提是代码里不出现 `.xaml`**——XAML 标记编译
  （`PresentationBuildTasks`）不在 dotnet SDK 里，只有装了 VS/.NET Framework SDK 才编得动。
  本机 `Framework64` 下有 `v4.8`，且 Win10 1607+/Win11 一律自带 .NET Framework 4.8+。

体积合计：**载荷 185MB 解压 / zip 约 95MB**。若向导也做成自包含 `net10.0-windows` WPF，
要再加约 155MB，这是选 net48 的全部理由。

## 4. 分发物布局

```
dist/
├─ Vigil-1.0.0-win-x64/                 整个文件夹就是分发单元
│  ├─ Vigil-Setup.exe                   安装向导（net48，6KB）——双击这个
│  ├─ app/                              自包含载荷（185MB）
│  │  ├─ Vigil.exe  Vigil.dll  *.json   主程序（publish 产物原样）
│  │  ├─ Wpf.Ui.dll  Wpf.Ui.Abstractions.dll
│  │  ├─ dsh_state.py                   csproj 已 Link + PreserveNewest，publish 自动带上
│  │  └─ runtime/python/                CPython 3.14.7 embeddable，37 个文件
│  ├─ LICENSE.txt                       本项目 MIT（与仓库 LICENSE 同一份内容）
│  ├─ THIRD-PARTY-NOTICES.md            含新增的 Python PSF 条目，见 §9
│  └─ 安装说明.txt                       三步：解压 → 双击 Vigil-Setup.exe → 按提示
└─ Vigil-1.0.0-win-x64.zip              上面文件夹的压缩包
```

安装完成后的目录（默认 `%LOCALAPPDATA%\Programs\Vigil`）：`app/` 的内容被**提升一层**到根，
即 `Vigil.exe`、`dsh_state.py`、`runtime\python\python.exe` 三者同级，
外加一份 `Vigil-Setup.exe`（卸载要靠它，见 §6）。`runtime\python\LICENSE.txt` 随附不删。

## 5. 安装向导：界面与流程

项目 `setup/Vigil.Setup.csproj`，源文件 `setup/Program.cs`、`setup/Installer.cs`、`setup/Ui.cs`。
单窗口、浅色主题（白底黑字，与 §README 的界面口径一致）、文案全中文、不引入 WPF-UI
（net48 拿不到那个包的 net48 目标）。

### 5.1 安装模式（默认）

一页表单 + 底部进度条 + 可展开的日志框：

- **安装目录**：文本框，默认 `%LOCALAPPDATA%\Programs\Vigil`，「浏览…」按钮。
  校验：绝对路径、不在 `app\` 自身内部、所在盘可用空间 ≥ 400MB（载荷 185MB + 复制时瞬时双份 + 余量）。
- **开机自动启动**（默认勾）：写 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 的 `Vigil` 值，
  数据格式必须与 `App.cs:1393` 完全一致：`"<exe 全路径>"`（带引号）。这样面板里那个既有的
  「开机自动启动」勾选框（`AutostartEnabled()` 只判断值是否存在）装完就是正确的勾态。
- **创建开始菜单快捷方式**（默认勾）/ **创建桌面快捷方式**（默认不勾）。
- **主按钮**：`检测到未安装 → 安装`；`同版本已在 → 修复`；`版本较低 → 升级到 1.0.x`；`版本较高 → 提示并允许降级覆盖`。
- 结尾：勾选「安装完成后启动 Vigil」。

### 5.2 执行步骤（失败即停，见 §7）

1. 关掉正在跑的旧实例：`taskkill /f /im Vigil.exe`（**必须带 `/f`**；README 常见问题已确认
   窗口是 `Shell_TrayWnd` 的子窗口，不带 `/f` 的优雅关闭会静默返回成功而进程照跑）。
   轮询确认进程真的没了才继续，否则复制到一半会因文件占用失败。
2. 把 `<向导所在目录>\app\**` 复制到安装目录——**手写递归复制**（`File.Copy`/`Directory.CreateDirectory`
   遍历），不用 `Robocopy`：它的退出码 0–7 都算成功，冒烟断言里很容易误判成失败或反过来假绿。
   先复制到 `<安装目录>.new` 兄弟目录再整目录换名，避免中途失败留下半吊子目录。
3. 复制 `Vigil-Setup.exe` 进安装目录。
4. 建快捷方式（§5.4）。
5. 写 Run 值（若勾选）。
6. 写卸载注册项（§5.3）。
7. 启动 `Vigil.exe`（若勾选）。

### 5.3 卸载注册项

`HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Vigil`：
`DisplayName=Vigil`、`DisplayVersion`（打包时从 csproj 的 `<Version>` 传下来的同一个值）、
`Publisher=Wildcreator`、`DisplayIcon=<安装目录>\Vigil.exe,0`（exe 图标来自 `<ApplicationIcon>assets\dsh.ico`）、
`InstallLocation`、`UninstallString="<安装目录>\Vigil-Setup.exe" /UNINSTALL`、`NoRepair=1`。
于是系统「设置 → 应用 → 已安装的应用」里能看到并卸载，这就是"正经安装"的凭据。

### 5.4 快捷方式

`net48` 里不引 `IWshRuntimeLibrary` 互操作程序集，走 **late-bound COM**：
`Type.GetTypeFromProgID("WScript.Shell")` → `Activator.CreateInstance` → `dynamic` 调
`CreateShortcut(path)`，设 `TargetPath`/`Arguments`/`WorkingDirectory`/`IconLocation`/`Description` 后 `Save()`。
需要 `<Reference Include="Microsoft.CSharp" />`（`dynamic` 的运行期支撑）。
开始菜单位置用 `Environment.SpecialFolder.StartMenu`（即 `%APPDATA%\Microsoft\Windows\Start Menu\Programs`，
用户级、免管理员）。

### 5.5 卸载模式 `/UNINSTALL`

确认页：列出安装目录、占用量、将要删除的东西，加一个默认**不勾**的
「同时删除设置与余额 Key（`%LOCALAPPDATA%\Vigil`）」。

1. `taskkill /f /im Vigil.exe`；
2. 删 Run 值、删 `Uninstall\Vigil` 项、删快捷方式；
3. 删安装目录；
4. 按勾选决定是否删 `%LOCALAPPDATA%\Vigil\`（`settings.json`/`bar.log`/`balance.protected`/`refresh.token`）。

**删除自己正在执行的文件**是这里唯一的技术钉子：卸载器进程就在被删的目录里。做法是把
`Vigil-Setup.exe` 先复制到 `%TEMP%\Vigil-Setup-<pid>.exe`，用参数重启那份副本，父进程立即退出，
副本等父句柄释放后再删目录。冒烟测试会覆盖这条（§9）。

卸载**不碰** `~/.dsh/sessions/`（那是 DeepSeek Harness 的数据，不是 Vigil 的）。

### 5.6 静默模式（脚本与企业部署用，也是测试抓手）

```
Vigil-Setup.exe /S [/D=<安装目录>] [/NO-AUTOSTART] [/NO-START] [/UNINSTALL] [/NO-PURGE-DATA]
```

无窗口、不弹任何消息框，进度与错误写 `%LOCALAPPDATA%\Vigil\setup.log`，退出码 0 成功、非 0 失败。
`/UNINSTALL` 隐含 `/S` 语义时用 `/NO-PURGE-DATA` 明确保留数据（默认保留，与 GUI 一致）。

## 6. 主程序侧改动

- **`ResolvePython()` 重写**（`App.cs:704`）。现在这版"PATH 里第一个 python"是错的。新顺序：
  1. `Path.Combine(AppContext.BaseDirectory, "runtime", "python", "python.exe")` —— 随附解释器，安装目录里必然在；
  2. 候选 `python`、`python3` 按 `PATH` 逐个试，再加 `py -3.14`（`py` 启动器解析出的实路径）；
  3. **每个候选都要过能力探测**：`candidate -c "import compression.zstd"`，退出码 0 才算数。
     这一步同时解决 3.12（无该模块）和"PATH 上有 python 但坏掉"两类情况；
  4. 全灭才返回 `null`，走既有的「状态检测引擎不可用」提示（`App.cs:663`、`:1203`），
     并把提示文案改成随附优先的说法。
  探测结果进程内缓存一次，别每轮询都开一个 python。
- **引擎路径**不变：`AppContext.BaseDirectory\dsh_state.py` 在安装目录成立；
  那个 `..\..\..\..\dsh_state.py` 的仓库开发期兜底（`App.cs:509`/`:658`/`:1201`）保留。
- **`Vigil.csproj`**：加 `<RuntimeIdentifier>win-x64</RuntimeIdentifier>`? **不加**——
  `build.cmd` 的开发期快速编译不该变成自包含（慢且 161MB），RID 只在 `package.cmd` 里给。
  需要新增的是把 `setup` 项目纳入同一个解决方案/构建脚本，以及 `<Version>` 作为版本单一来源。
- **托盘菜单**加一项「卸载…」：`Process.Start` 安装目录里的 `Vigil-Setup.exe /UNINSTALL`
  （开发期该文件不存在时置灰，别抛异常）。
- **面板「关于」页**加两行：安装位置（`AppContext.BaseDirectory`）与版本（程序集 `<Version>`）。
  开发期跑 `bin\Release\...` 时如实显示该路径，不伪装成安装目录。

## 7. 错误处理

- **失败即停 + 如实报告**：任何一步失败，把已完成步骤、失败步骤、原始异常写进 `setup.log`
  并在界面上红字显示；不静默继续下一步。
- **原子性**：载荷先复制到 `<安装目录>.new`，全部成功后 `Directory.Move` 换名
  （旧目录先改名成 `<安装目录>.old`，新目录顶上，再删 `.old`）。中途崩溃最多留下 `.new`/`.old`
  垃圾目录，绝不会留下"半个 Vigil 打不开"。启动时清理残留的 `.new`/`.old`。
- **文件被占用**：`taskkill /f` 后仍失败，重试 3 次、间隔 300ms，再失败就报"请关闭 Vigil 后重试"。
- **磁盘不足**：复制前预检（`DriveInfo.AvailableFreeSpace`），不够就在点安装时拦住，不等复制到一半炸。
- **注册表写失败**（HKCU 一般不该失败）：记录并继续还是回滚？——**记录并明确失败**，
  因为 Run 值/卸载项写不进去意味着"装了一半"，必须让用户看到。目录已换名的情况下不回滚目录，
  而是提示手动运行 `Vigil-Setup.exe /UNINSTALL` 清干净（卸载器能容忍注册项缺失）。
- **取消**：复制开始前可取消；复制中取消 = 丢弃 `.new`、保留原安装，界面回到初始态。
- **不允许**"向导从安装目录内部运行安装"（自己覆盖自己）：检测到 `AppContext.BaseDirectory`
  在安装目录或其父目录里时，先把自己复制到 `%TEMP%` 再从副本执行。

## 8. `package.cmd`

```
package.cmd [win-x64|win-arm64]
```

步骤（任一步非 0 立即整体非 0 退出，不做"看起来成功了"）：

1. 读版本：从 `bar/Vigil.csproj` 的 `<Version>` 取（**单一来源**），拼出 `Vigil-<ver>-<rid>`。
   `setup` 项目自己不写 `<Version>`，由脚本用 `-p:Version=<ver>` 同时传给两次 publish，
   这样向导写入的 `DisplayVersion` 与主程序既有的 `App.VersionText`（`bar/App.cs:38`，
   取 `Assembly.GetName().Version`；旁边还有 `VersionCodename = "Vachellia farnesiana"`）必然同值。
2. `dotnet publish bar/Vigil.csproj -c Release -r <rid> --self-contained true -o dist/<name>/app`。
3. **Python 随附包**：钉死 `3.14.7`；URL 与 SHA256 常量写进脚本；
   缓存在 `vendor/python-3.14.7-embed-amd64.zip`（`vendor/` 进 `.gitignore`，12MB 不进版本库），
   缺了才 `curl` 下载；**校验和不符直接失败退出**，不尝试"凑合用"；
   解压到 `dist/<name>/app/runtime/python`（解压前先清目录，避免上一次遗留文件混进产物）。
4. `dotnet publish setup/Vigil.Setup.csproj -c Release` → 拷 `Vigil-Setup.exe` 到
   `dist/<name>/`（给双击）**和** `dist/<name>/app/`（给卸载）。
5. 拷 `LICENSE` → `dist/<name>/LICENSE.txt`、`THIRD-PARTY-NOTICES.md`、生成 `安装说明.txt`。
6. **产物完整性断言**（缺任何一项即失败）：`app/Vigil.exe`、`app/dsh_state.py`、
   `app/runtime/python/python.exe`、`app/runtime/python/_zstd.pyd`、`app/runtime/python/LICENSE.txt`、
   `app/Wpf.Ui.dll`、`<name>/Vigil-Setup.exe`、`<name>/THIRD-PARTY-NOTICES.md`。
7. 用产物里那份 `app/runtime/python/python.exe` 跑 `app/dsh_state.py --json`，断言 `"ok": true`
   —— 这一步是"随附解释器真的能干活"的硬证，不是目录存在就完事。
8. 压 zip：`tar -a -cf <name>.zip <name>/`（Win10 1803+ 自带 bsdtar，`-a` 按扩展名选 zip 格式）。
   不用 PowerShell 的 `Compress-Archive`：它对 4GB 以下没事，但 185MB 里那 255 个文件的路径分隔符
   与 `runtime/python` 下的同名文件处理不如 bsdtar 直白，而且从 `cmd` 里起 PowerShell 又要多背一层
   执行策略。
9. 打印路径与体积。

`package.cmd --verify-only`：跳过第 2、4 步（不重新 publish），只对**已存在的 `dist/`** 跑第 3 的
校验和、第 6 的完整性断言、第 7 的随附解释器跑引擎。`smoke_test.py --package` 走这条，
免得每次冒烟都要重编 161MB。

`build.cmd` / `start-bar.bat` 保持原样（开发期还要用），README 里把三者分工写清楚。

## 9. 测试门禁（`smoke_test.py`）

沿用仓库既有的 `check(name, cond, detail)` 断言口径（当前 61 项），新增：

- `--setup`：编译 `setup/Vigil.Setup.csproj`，断言 0 警告 0 错误；断言产物 exe 存在且
  `net48` 目录里没有 `PresentationFramework.dll` 之类（证明真的靠系统运行时）。
- `--package`：调 `package.cmd --verify-only`（§8 末），断言 `dist/` 完整性清单 +
  随附解释器能跑引擎。**注意构建输出不要接管道**（`| tail` 会吞掉退出码，仓库既有口径）。
- **端到端安装演练**（`--setup` 与 `--package` 都跑）：
  1. `Vigil-Setup.exe /S /D=%TEMP%\vigil-e2e /NO-START` → 退出码 0；
  2. 断言：`%TEMP%\vigil-e2e\Vigil.exe`、`runtime\python\python.exe`、`Vigil-Setup.exe` 都在；
     `HKCU\...\Run` 的 `Vigil` 值等于该 exe 路径（带引号格式）；`Uninstall\Vigil` 的
     `UninstallString` 指向安装目录里那份向导；开始菜单 `.lnk` 存在且 `TargetPath` 正确
     （用 `WScript.Shell` 读回来验，别只验文件在）；
  3. `%TEMP%\vigil-e2e\Vigil-Setup.exe /UNINSTALL /S /NO-PURGE-DATA` → 退出码 0；
  4. 断言：安装目录已消失、Run 值已删、`Uninstall\Vigil` 已删、`.lnk` 已删、
     **`%TEMP%\vigil-e2e` 之外的 `%LOCALAPPDATA%\Vigil\` 未被删**（数据保留承诺）。
  5. 全程用 `try/finally` 清残留，测完机器上不留 `Vigil` 的 Run 值。
  演练必须能在"本机已经装了 Vigil"的情况下跑：测试写入的就是 `HKCU` 那两个键，
  因此**测试前后要保存/恢复用户原有的 `Vigil` Run 值**，不能把人家开机自启搞没了。
- `THIRD-PARTY-NOTICES.md`：**§4.2「Python 3.14 标准库」现在写的是"不随附其代码"，这次反转了**
  —— embeddable 解释器随附分发。必须改写该节并新增 PSF 许可证条目 + 随附 `LICENSE.txt` 的指引；
  同时确认 `check_licenses()` 的 `LICENSE_MUST_CONTAIN` 关键词清单和
  "≥6 段 MIT 方块"计数仍然成立（PSF 许可证不是 MIT，不会被 `MIT_PROBE` 计进去，
  但 Python 条目**必须**进 `LICENSE_MUST_CONTAIN`，否则等于合规声明漏了一项）。
- README：新增「装到任何电脑」一节（前置=无、支持范围=Win10 1607+/Win11 x64、
  安装/升级/卸载三步、体积 95MB），并把 §常见问题 里
  「`No module named 'compression'` → 升级 Python」那条改成"随附解释器已解决；
  仅在你手动指定系统 Python 时才需要关心 3.14"。

## 10. 非目标

- **不支持 Win7/8**：`.NET 10 WPF` 官方只到 Win10+，绕不过去（要支持就是换技术栈重写，另案）。
- **不做在线更新**：`package.cmd` 出的 zip 人工分发；没有更新检查、没有下载更新的代码。
- **不做 MSIX / Microsoft Store**：要签名与沙箱化，和"拷进去就能跑"是两条路。
- **不做 MSI / Inno Setup / WiX**：都要额外装工具链，明确排除。安装/卸载体验靠 HKCU 卸载注册项达成。
- **不把引擎移植成 C#**（本次）：Python 随附的代价是 24MB，换来 CLI 契约与引擎代码不动。
- **不做多用户/机器级安装**：只装到当前用户（`%LOCALAPPDATA%` + `HKCU`）。
- **不做代码签名**：本机产物无签名，SmartScreen 可能拦（README 安装说明里写清楚"更多信息 → 仍要运行"）。

## 11. 风险与开放问题

| 风险 | 影响 | 处置 |
| --- | --- | --- |
| 无签名 exe 被 SmartScreen / 杀软拦 | 首次双击装不上 | 安装说明写明放行步骤；不做签名（§10） |
| 目标机 `PATH` 上的 python 挡在前面 | 若随附解释器缺失就会选到 3.12 | §6 能力探测已解决；随附解释器排在第一优先级 |
| embeddable zip 版本与 Harness 的 zstd 帧版本不匹配 | 解不出会话 | 3.14.7 已在本机真数据验证；`--package` 第 7 步跑真引擎兜住 |
| `python314._pth` 的 isolated 模式 | 引擎拆多文件即失效 | 设计约束记进本文，未来拆文件要同时改 `._pth` |
| 卸载删不掉自身 | 留下垃圾目录 | §5.5 的 temp 副本重启法 + 端到端演练断言 |
| net48 向导在极端精简的 Windows 上无 .NET Framework | 向导起不来 | Win10 1607+ 一律自带 4.8+；真遇到就退回静默脚本（不在本次范围） |

---

## 12. 实现期间对设计的修正（2026-10-05，全部实测得出）

设计里写对了的部分不再重复；这一节只记**设计没料到、实现时改掉**的事。

| # | 设计原样 | 实现改成 | 由头 |
| --- | --- | --- | --- |
| 1 | §5.6 只有 `/D=` 一个目录参数 | 卸载时若没给 `/D=`，**认向导自己所在目录**为安装目录 | 注册表里的 `UninstallString` 是 `"<目录>\Vigil-Setup.exe" /UNINSTALL`，不带 `/D=`；照原设计会拿默认路径去删一个不存在的地方，表现是「卸载成功但文件全在」 |
| 2 | §5.5 用字符串前缀判断"我是不是在被删的目录里" | 两边先 `GetLongPathNameW` 还原本地长路径再比 | `AppDomain.BaseDirectory` 给 8.3 短形式（`C:\Users\WILDCR~1\...`），`MainModule.FileName` 给长形式，`StartsWith` 永远不等 → 走"直接删"分支，撞在正在运行的 exe 上抛 `UnauthorizedAccessException` |
| 3 | §5.5 父进程 `SelfDelete` 临时副本 | 改成**副本自己**排延后自删 | 副本还在运行时，父进程替它 `del` 必然失败（镜像被锁） |
| 4 | §8 的 `package.cmd` 内含中文注释与中文文件名 | 批处理**纯 ASCII**；中文文件名由 `tools/write_readme_install.py` 自己拼 | zh-CN 代码页下，`rem` 行里的 UTF-8 中文会吞掉行尾，注释尾巴被当成命令执行，前面几步被整段跳过（实测：直接跳到 `[5/6]`） |
| 5 | §8 第 8 步 `tar -a -cf` | 一律写 `%SystemRoot%\System32\tar.exe` | 开发机 PATH 上 Git Bash 的 GNU tar 排在前头，它读不了 zip：`This does not look like a tar archive` |
| 6 | §4 分发物只有 `LICENSE.txt` + `THIRD-PARTY-NOTICES.md` | 新增 `licenses\`：`.NET` 两份 MIT 原文 + `python-PSF.txt` + 索引，由 `tools/collect_licenses.py` 从 NuGet 缓存与 embeddable 包原样收集 | `--self-contained` 是在**分发 .NET 运行时的二进制**，MIT 要求版权声明随副本保留；只在仓库声明文件里写一句"人家是 MIT"不满足条件 |
| 7 | §9 的 `--package` 只跑 `verify_package.py` | 再加一条：把 `PATH` 削到只剩 `System32` 后跑产物里的 `Vigil.exe --engine-probe` | 原设计那条在宿主 PATH 下跑，挡不住"其实靠的是宿主 python"——而"零前置"要证的恰恰是这件事 |
8 | §7 "复制中可取消 = 丢弃 `.new`" | 未实现中止复制，复制期间禁用主按钮，`.new` 由下次安装的 `Sweep()` 清 | 几十秒的复制要真中止就得等句柄释放，收益不值；原子性承诺（不留半个装不开的安装）不受影响 |

### 验收补记

- 真产物（184MB）静默安装耗时 2s；装好后 `PATH` 只剩 `System32` 时 `--engine-probe` 仍指向
  `runtime\python\python.exe`、`zstd=ok`；启动后 `find_bar_windows()` 找到 1 个状态栏窗口，
  矩形 `(6,915)-(205,957)`；`/UNINSTALL /S` 退出码 0、目录删净、`%LOCALAPPDATA%\Vigil` 保留。
- **未做**：真正的第二台干净机器验证（本机已装 .NET/Python，只能靠削 `PATH` 逼近）。
  无签名产物在别的机器上被 SmartScreen / 杀软拦住的体验仍未验证，spec §11 那条风险仍然挂着。
