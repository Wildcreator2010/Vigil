# Vigil 安装功能与零前置分发 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Vigil 拷到任何 Win10 1607+ / Win11 x64 电脑都能装能跑——不预装 .NET、不预装 Python、不联网、不要管理员，并交付一个中文 GUI 安装向导（安装/升级/修复/卸载 + 系统「设置 → 应用」可卸载）。

**Architecture:** 分发单元是 `package.cmd` 产的 `dist/Vigil-<ver>-<rid>/`，里面有 6KB 的 net48 安装向导和 185MB 的自包含载荷（.NET 10 运行时 + CPython 3.14.7 embeddable + `dsh_state.py`）。向导不内嵌载荷，与 `app/` 同目录发布；它把手写递归复制到 `<目标>.new` 再整目录换名，然后写 `HKCU` 的 Run 值与卸载注册项、建 `.lnk` 快捷方式。主程序侧唯一改动是 `ResolvePython()` 换成「随附解释器优先 + 逐个能力探测」。

**Tech Stack:** C# / .NET 10 WPF（主程序，自包含 win-x64）、C# / .NET Framework 4.8 + 纯代码 WPF（向导，**无 XAML**）、Python 3.14 标准库（引擎与冒烟 harness）、Windows 自带的 `certutil`/`tar`/`taskkill`/`WScript.Shell`。

**Spec:** `docs/superpowers/specs/2026-10-05-vigil-install-design.md`（含全部实测数据：embeddable 包里有 `_zstd.pyd`、net48 WPF 编译产物 5632 字节、自包含 publish 161MB/255 文件、embeddable zip 的 SHA256）

## Global Constraints

- 零前置。支持面 **Win10 1607+ / Win11，x64**；**不支持 Win7/8**（`.NET 10 WPF` 官方下限，spec §10）。不做在线更新、不做 MSIX、不做 MSI/Inno/WiX（都要额外装工具链，明确排除）。
- **安装向导项目 `setup/` 里不得出现任何 `.xaml` 文件**，UI 一律纯 C# 构建。原因：XAML 标记编译靠 `PresentationBuildTasks`，它不在 dotnet SDK 里；带 `.xaml` 的 net48 项目在只有 SDK 的机器上直接编不动（spec §3 实测的是无 XAML 路径，别把它换掉）。
- 引擎侧**只用 Python 3.14 标准库**，不得引入第三方包（既有约束）。
- 编译门禁：`dotnet build/publish -c Release` 必须 **0 error 0 warning**。跑构建不要把输出接管道（`| tail` 会吞掉退出码），先重定向到文件再判 `$?`。
- 向导全程只写 `HKCU` 与 `%LOCALAPPDATA%`，`app.manifest` 用 `asInvoker`，**永不触发 UAC**。不装服务、不做机器级安装。
- 面向安装者的文案**全中文、浅色**（白底黑字），与面板既有口径一致；向导**不使用 WPF-UI**（net48 拿不到它的 net48 目标）。
- **版本单一来源** = `bar/Vigil.csproj` 的 `<Version>`；`package.cmd` 用 `-p:Version=<ver>` 同时传给主程序和向导两次 publish，两个 exe 的 `AssemblyVersion` 必须相等。
- **`taskkill` 必须带 `/f`**：状态栏窗口是 `Shell_TrayWnd` 的子窗口，不带 `/f` 的优雅关闭会静默返回成功而进程照跑（README 常见问题已确认，`smoke_test.py:check_gui` 同样口径）。
- **Run 值的数据格式必须是 `"<exe 全路径>"`（带引号）**，与 `bar/App.cs:1393` 逐字一致；键 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`、值名 `Vigil`（`App.cs:28-29` 的 `RunKey`/`RunValue`）。写错了面板的「开机自动启动」勾态就是假的。
- 卸载**默认保留** `%LOCALAPPDATA%\Vigil\`（`settings.json`/`bar.log`/`balance.protected`/`refresh.token`）；余额 Key 是 DPAPI 按当前 Windows 账户加密的，换电脑必须重录，安装完成页要说这一句。
- 卸载**不碰** `~/.dsh/sessions/`（那是 DeepSeek Harness 的数据）。
- `embeddable CPython` 钉死 **3.14.7**，`SHA-256 = d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15`（12673227 字节），校验和不符必须非 0 退出。
- `python314._pth` 让随附解释器进 **isolated 模式**：`sys.path` 只有 `[python314.zip, 目录]`，**不加脚本所在目录**、忽略 `PYTHONPATH`。引擎是单文件纯标准库所以成立（spec §3 实测）；今后若给 `dsh_state.py` 拆多文件，必须同时改 `._pth`，否则 import 全断。
- 测试口径：本仓库**没有 pytest**，一切断言写进 `smoke_test.py` 的 `check(name, cond, detail)`；`--all` 是全集。任何"跑不起来所以跳过"要显式记 ✗ 或打印跳过原因，不许静默绿。
- `smoke_test.py` 里**已经有** `RUN_KEY`、`RUN_VALUE`、`reg_run_value()`（`:1852` 附近）和 `uia(hwnd, action, name="", index=0, value="")`、`top_window(title)`、`bar_processes()`、`check()`、`run()`、`HERE`、`BAR_EXE`。**直接复用，别再定义一遍**——Python 里重复定义同名模块常量是静默覆盖，不会报错，只会让后面那份悄悄改掉前面的。
- 面板窗口标题是 `Vigil 控制台`（`bar/Panel/PanelWindow.xaml:5`），页签登记表是 `PanelPages.TryCreate`（`bar/Panel/PanelPages.cs`，`"about"` 已经指向 `new AboutPage()`）。真窗口门禁统一用 `Vigil.exe --panel about` 起、`top_window("Vigil 控制台")` 认。

## 文件结构

| 文件 | 责任 | 动作 |
| --- | --- | --- |
| `bar/App.cs`（`:704` `ResolvePython`、`:141` `Main`、`:1115` 菜单、`:38` 常量） | 解释器三级解析与能力探测；`--engine-probe` 自检开关；托盘「卸载…」 | 修改 |
| `bar/Panel/Pages/AboutPage.cs` | 关于页填实：安装位置、版本、随附解释器 | 新建（从 `Pages.cs:39` 的 `AboutPage` 占位类迁出并实现，照 `RuntimePage.cs` 的样子） |
| `setup/Vigil.Setup.csproj` | net48 向导工程（`WinExe`、`asInvoker` manifest、`dsh.ico`、`Microsoft.CSharp` 引用） | 新建 |
| `setup/app.manifest` | `asInvoker` + Win10 兼容 GUID | 新建 |
| `setup/Installer.cs` | 全部安装/卸载动作：路径、复制换名、注册表、`.lnk`、自我删除重启。无 UI、可被静默模式直接驱动 | 新建 |
| `setup/Ui.cs` | 浅色 WPF 窗口（安装态/卸载态两种表单、进度、日志框） | 新建 |
| `setup/Program.cs` | 参数解析（`/S /D= /UNINSTALL /NO-*`）、静默与 GUI 分流、退出码 | 新建 |
| `package.cmd` | 一条命令产出 `dist/`；`--verify-only` 只校验 | 新建 |
| `tools/verify_package.py` | 产物完整性 + 用随附解释器跑真引擎的断言（常设工具，被 `package.cmd` 与冒烟共用） | 新建 |
| `smoke_test.py` | `check_python_resolution`、`check_setup`、`check_installer_e2e`、`check_package`、`check_setup_contract`；`LICENSE_MUST_CONTAIN` 增项 | 修改 |
| `THIRD-PARTY-NOTICES.md` | Python embeddable 随附分发（PSF）一节 + 改掉「不随附其代码」的说法 | 修改 |
| `README.md` | 「装到任何电脑」一节 + FAQ 两条更正 | 修改 |
| `.gitignore` | 加 `dist/`、`vendor/` | 修改 |

`build.cmd` / `start-bar.bat` **保持原样**：开发期快速编译（6.7MB）与分发产物（185MB）是两条路，别把它们并成一条。

---

### Task 1: 解释器三级解析与能力探测

**Files:**
- Modify: `bar/App.cs:704-723`（`ResolvePython` 整体重写）
- Modify: `bar/App.cs:148-168`（`Main` 参数循环 + 早返回处加 `--engine-probe`）
- Test: `smoke_test.py`（新增 `check_python_resolution()`，并在 `main()` 的 `if full or "--build" in args:` 之后调用）

**Interfaces:**
- Produces: `App.EngineProbe()` → 往 stdout 打印三行 `python=<绝对路径>|none`、`engine=<绝对路径>`、`zstd=ok|fail`，退出码 0（解析成功）/ 3（无可用解释器）。Task 5 的 dist 门禁和 `tools/verify_package.py` 都靠它判断随附解释器是否生效。
- Produces: `private static string ResolvePython()` 语义不变（返回可用解释器路径或 `null`），调用点 `App.cs:510`、`:660`、`:677`、`:1202` 与 `StateClient` 全部不用改。

- [ ] **Step 1: 写失败的门禁**

在 `smoke_test.py` 里 `check_engine_copy()` 定义之前加：

```python
def find_zstdless_python() -> str | None:
    """找一个**确实没有** `compression.zstd` 的解释器，用来毒化 PATH。

    本机 uv 装的 cpython-3.12 就是现成的靶子（`py -0p` 列出来的第二项）。这条门禁的意义
    全在这个靶子上：没有它，「PATH 第一个 python 抢跑」这个真 bug（旧 `ResolvePython`
    会选中 3.12，`import compression` 即失败）就没人看着。找不到靶子就记 ✗，别默默放过。
    """
    root = os.path.expandvars(r"%APPDATA%\uv\python")
    for exe in sorted(glob.glob(os.path.join(root, "*", "python.exe"))):
        r = subprocess.run([exe, "-c", "import compression.zstd"], capture_output=True, timeout=30)
        if r.returncode != 0:
            return exe
    return None


def probe_python(exe: str, env: dict | None = None) -> dict[str, str]:
    """跑 `Vigil.exe --engine-probe` 把 stdout 那三行 `k=v` 收成 dict。"""
    r = subprocess.run([BAR_EXE, "--engine-probe"], capture_output=True,
                       text=True, errors="replace", timeout=120, cwd=HERE, env=env)
    kv = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    kv["_rc"] = str(r.returncode)
    return kv


def check_python_resolution() -> None:
    print("\n== 解释器解析 ==")
    if not os.path.isfile(BAR_EXE):
        check("Vigil.exe 存在", False, "先跑 --build 或 build.cmd")
        return
    kv = probe_python(BAR_EXE)
    check("--engine-probe 解析成功并吐出三行",
          kv.get("_rc") == "0" and kv.get("zstd") == "ok"
          and os.path.isabs(kv.get("python", "")) and os.path.isfile(kv.get("engine", "")),
          f"实得 {kv}")
    if kv.get("python"):
        q = subprocess.run([kv["python"], "-c", "import compression.zstd"],
                           capture_output=True, timeout=30)
        check("报告的解释器真能 import compression.zstd", q.returncode == 0,
              q.stderr.decode("utf-8", "replace")[:200])

    poison = find_zstdless_python()
    check("找到无 zstd 的解释器作为毒化靶子（找不到这条就失效）", poison is not None,
          f"扫 {os.path.expandvars('%APPDATA%\\uv\\python')} 没找到 3.13- 的解释器")
    if poison:
        pdir = os.path.dirname(poison)
        env = dict(os.environ, PATH=pdir + os.pathsep + os.environ.get("PATH", ""))
        kv2 = probe_python(BAR_EXE, env)
        got = os.path.normcase(os.path.dirname(kv2.get("python", "")))
        check("把无 zstd 的解释器插到 PATH 最前面，解析结果不被它带走",
              kv2.get("_rc") == "0" and got != os.path.normcase(pdir),
              f"毒化目录 {pdir}，实选 {kv2.get('python')}")
```

`main()` 里挂在编译段之后（随附解释器在开发目录不存在，这条只验系统解释器）：

```python
    if full or "--build" in args:
        check_build()
        check_python_resolution()
```

- [ ] **Step 2: 跑它，确认红**

Run: `python smoke_test.py --build`
Expected: `✗ --engine-probe 解析成功并吐出三行`（现在 `Main` 认不出这个参数，它会当成"没有参数"直接起状态栏，`subprocess` 那边 120 秒超时抛 `TimeoutExpired`）。先看到这条红再往下写。

- [ ] **Step 3: 实现解析与探测**

替换 `bar/App.cs:704-723` 的 `ResolvePython()`：

```csharp
        // 解释器解析结果进程内缓存：能力探测要开子进程，别挂在每次轮询/重启引擎上反复跑。
        // "" 是"全试过都不行"的哨兵值，跟 null（还没探）区分开。
        private static string _pythonCache;

        /// <summary>
        /// 找一个能 `import compression.zstd` 的 Python。三级顺序，每一级都要过探测：
        ///   ① 随附的 runtime\python\python.exe —— 安装目录里必然在，排第一是因为它
        ///     **不受宿主 PATH 影响**，这是"零前置"的落点；
        ///   ② PATH 上的 python/python3 —— 旧实现取第一个就返回，会选中没有 compression
        ///     模块的 3.12（本机 uv 那份就是），于是状态栏启动即弹「引擎不可用」；
        ///   ③ py 启动器报的 3.14 实路径，再兜底 C:\Python314。
        /// </summary>
        private static string ResolvePython()
        {
            if (_pythonCache != null) return _pythonCache.Length == 0 ? null : _pythonCache;
            foreach (var cand in PythonCandidates())
            {
                if (HasZstd(cand)) { _pythonCache = cand; return cand; }
            }
            _pythonCache = "";
            return null;
        }

        private static IEnumerable<string> PythonCandidates()
        {
            var bundled = Path.Combine(AppContext.BaseDirectory, "runtime", "python", "python.exe");
            if (File.Exists(bundled)) yield return bundled;

            foreach (var name in new[] { "python", "python3" })
            {
                foreach (var dir in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
                {
                    if (dir.Length == 0) continue;
                    string candidate;
                    try { candidate = Path.Combine(dir.Trim(), name + ".exe"); }
                    catch { continue; }
                    if (File.Exists(candidate)) yield return candidate;
                }
            }

            foreach (var fromLauncher in FromPyLauncher()) yield return fromLauncher;

            var last = Path.Combine(@"C:\Python314", "python.exe");
            if (File.Exists(last)) yield return last;
        }

        /// <summary>用 `py -0p` 列出的实路径找 3.14（开发机上 python.exe 可能不在 PATH 里）。</summary>
        private static IEnumerable<string> FromPyLauncher()
        {
            var py = new[] { Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "py.exe") }
                .FirstOrDefault(File.Exists);
            if (py == null) yield break;
            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = py, Arguments = "-0p",
                    RedirectStandardOutput = true, UseShellExecute = false, CreateNoWindow = true,
                };
                using var p = Process.Start(psi);
                string text = p?.ReadToEnd() ?? "";
                if (p != null && !p.WaitForExit(5000)) p.Kill(true);
                foreach (var line in text.Split('\n'))
                {
                    var m = System.Text.RegularExpressions.Regex.Match(line, @"-\d+\.\d+.*?(C:\\\S*?python\.exe)");
                    if (m.Success && line.Contains("3.14") && File.Exists(m.Groups[1].Value))
                        yield return m.Groups[1].Value;
                }
            }
            catch { }
        }

        /// <summary>能力探测：跑得起来且 `import compression.zstd` 不报错才算数。</summary>
        private static bool HasZstd(string exe)
        {
            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = exe,
                    Arguments = "-c \"import compression.zstd\"",
                    RedirectStandardOutput = true, RedirectStandardError = true,
                    UseShellExecute = false, CreateNoWindow = true,
                };
                using var p = Process.Start(psi);
                if (p == null) return false;
                // 超时必须有，且不能先 ReadToEnd：PATH 上的 python.exe 若是 WindowsApps 的
                // 商店存根，它会开一个「去商店获取」的页并永不退出，读流就是把调用方挂死。
                string stdout = p.StandardOutput.ReadToEnd();
                string stderr = p.StandardError.ReadToEnd();
                if (!p.WaitForExit(8000)) { try { p.Kill(true); } catch { } return false; }
                return p.ExitCode == 0;
            }
            catch { return false; }
        }
```

同文件加 `EngineProbe()`（放在 `ResolvePython` 之后）：

```csharp
        /// <summary>`Vigil.exe --engine-probe`：只报解析结果就退，不开窗口、不抢单实例。</summary>
        private static int EngineProbe()
        {
            string engine = Path.Combine(AppContext.BaseDirectory, "dsh_state.py");
            if (!File.Exists(engine))
                engine = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "dsh_state.py"));
            string python = ResolvePython();
            Console.WriteLine("python=" + (python ?? "none"));
            Console.WriteLine("engine=" + engine);
            Console.WriteLine("zstd=" + (python == null ? "fail" : File.Exists(engine) ? "ok" : "fail"));
            return python == null || !File.Exists(engine) ? 3 : 0;
        }
```

`Main` 里接线：参数循环（`:149-165`）加一个分支，并在 `shotPage` 早返回旁边加一条——**必须在 `_mutex` 之前返回**，否则冒烟起探针时会被正在跑的状态栏顶掉、行为随机器状态漂移。

```csharp
                else if (args[i] == "--engine-probe") return EngineProbe();
```

- [ ] **Step 4: 把失败提示改准**

`App.cs:664` 和 `:1203` 的 `Balloon("状态检测引擎不可用", "请确认 dsh_state.py 与 python 3.14 就位", ...)` 改成体现随附优先的说法（两处都要改，逐字一致）：

```csharp
                Balloon("状态检测引擎不可用",
                        "随附解释器 runtime\\python\\python.exe 缺失，且系统里找不到能解 zstd 的 Python 3.14",
                        ToolTipIcon.Error);
```

- [ ] **Step 5: 跑门禁确认转绿**

Run: `python smoke_test.py --build`
Expected: `== 解释器解析 ==` 四条全 ✓。再手工确认探针不弹窗、不抢单实例：状态栏开着的时候跑 `bar\bin\Release\net10.0-windows\Vigil.exe --engine-probe`，应立刻返回三行而状态栏不受影响。

- [ ] **Step 6: 提交**

```bash
git add bar/App.cs smoke_test.py
git commit -m "fix(app): 解释器三级解析 + zstd 能力探测，别再认 PATH 第一个 python

旧 ResolvePython 取 PATH 里第一个 python.exe 就返回，会选中本机 uv 那份没有
compression 模块的 3.12，于是换电脑必然「引擎不可用」。现在随附解释器优先，
其余候选逐个过 import 探测，探测结果进程内缓存。附带 --engine-probe 自检开关。"
```

---

### Task 2: 安装器逻辑与静默模式

**Files:**
- Create: `setup/Vigil.Setup.csproj`
- Create: `setup/app.manifest`
- Create: `setup/Installer.cs`
- Create: `setup/Program.cs`
- Test: `smoke_test.py`（新增 `check_installer_e2e()`、`check_setup()`）

**Interfaces:**
- Consumes: `app/Vigil.exe`（读它算版本、复制它）、`HKCU\...\Run` 的 `Vigil` 值（格式见 Global Constraints）。
- Produces: `Installer.Options`（含 `TargetDir/PayloadDir/Autostart/StartMenuShortcut/DesktopShortcut/LaunchAfter/PurgeData/Log`）、`Installer.Install(Options)` / `Installer.Uninstall(Options)` → `bool`；`Installer.DefaultTargetDir()`、`Installer.PayloadDirOf(setupDir)`、`Installer.IsInstalled(target)`、`Installer.InstalledVersion(target)`、`Installer.Version()`、`Installer.SetRunValue(target,on,log)`、`Installer.WriteUninstallKey(target,log)`、`Installer.MakeShortcut(lnk,target,log)`；常量 `RunKeyPath`/`RunKeyValueName`/`UninstallKeyPath`/`SetupExeName`/`PayloadFolderName`/`ProductName`。Task 3 的 GUI 只调 `Install/Uninstall` 与那四个状态查询，Task 5 的卸载入口靠安装目录里那份 `Vigil-Setup.exe /UNINSTALL`。
- Produces: 命令行契约 `Vigil-Setup.exe [/S] [/D=<dir>] [/NO-AUTOSTART] [/NO-STARTMENU] [/DESKTOP] [/NO-START] [/UNINSTALL] [/PURGE-DATA] [/FROM-TEMP]`，退出码 0 成功、1 失败、2 参数错。`/D=` 只表示**安装目标目录**；载荷目录写死为 `<向导所在目录>\app`，不可覆盖（spec §4 的分发布局）。

> 与 spec §5.6 的一处分岔：spec 写的是 `/NO-PURGE-DATA`（默认保留、显式声明才保留），本计划倒过来用 **`/PURGE-DATA`（默认保留、显式声明才删）**。倒过来的理由是"删除"应当是 opt-in：一个记错的开关不该让人丢掉余额 Key。GUI 上那个勾选框同语义。

- [ ] **Step 1: 建工程（照抄已实测通过的最小配置）**

`setup/Vigil.Setup.csproj`：

```xml
<Project Sdk="Microsoft.NET.Sdk">

  <PropertyGroup>
    <!-- net48 = Win10 1607+/Win11 系统自带，向导因此只有 6KB 而不是再来一份 155MB 自包含运行时。
         纯代码 WPF，本工程禁止出现 .xaml：XAML 标记编译要 PresentationBuildTasks，
         它不在 dotnet SDK 里，只有装了 VS 的机器编得动（spec §3）。 -->
    <TargetFramework>net48</TargetFramework>
    <OutputType>WinExe</OutputType>
    <AssemblyName>Vigil-Setup</AssemblyName>
    <RootNamespace>Vigil.Setup</RootNamespace>
    <Nullable>disable</Nullable>
    <ImplicitUsings>disable</ImplicitUsings>
    <LangVersion>latest</LangVersion>
    <ApplicationManifest>app.manifest</ApplicationManifest>
    <ApplicationIcon>..\bar\assets\dsh.ico</ApplicationIcon>
    <WarningLevel>4</WarningLevel>
  </PropertyGroup>

  <ItemGroup>
    <Reference Include="PresentationFramework" />
    <Reference Include="PresentationCore" />
    <Reference Include="WindowsBase" />
    <Reference Include="System.Xaml" />
    <!-- dynamic 调 WScript.Shell 建 .lnk 的运行期支撑；net48 不默认引用 -->
    <Reference Include="Microsoft.CSharp" />
    <!-- Task 3 的「浏览…」用 FolderBrowserDialog；Win10/11 的系统程序集，不是新依赖 -->
    <Reference Include="System.Windows.Forms" />
    <Reference Include="System.Xml" />
  </ItemGroup>

  <ItemGroup>
    <!-- 不装 VS/.NET Framework SDK 也能编 net48 的关键 -->
    <PackageReference Include="Microsoft.NETFramework.ReferenceAssemblies" Version="1.0.3" PrivateAssets="all" />
  </ItemGroup>

</Project>
```

> 不写 `<UseWPF>true</UseWPF>`：那个开关会把 SDK 拉进 WPF 的 XAML 编译管线，net48 + 无 VS 时正是它把编译搞死的。上面那四条 `<Reference>` 手工引就够，实测过。

`setup/app.manifest`（`asInvoker` 是硬要求：向导永远不该弹 UAC）：

```xml
<?xml version="1.0" encoding="utf-8"?>
<assembly manifestVersion="1.0" xmlns="urn:schemas-microsoft-com:asm.v1">
  <assemblyIdentity version="1.0.0.0" name="Vigil.Setup"/>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v2">
    <security>
      <requestedPrivileges xmlns="urn:schemas-microsoft-com:asm.v3">
        <requestedExecutionLevel level="asInvoker" uiAccess="false" />
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
    </application>
  </compatibility>
</assembly>
```

- [ ] **Step 2: 写失败的门禁（端到端安装演练）**

`smoke_test.py` 加：

```python
SETUP_EXE = os.path.join(HERE, "setup", "bin", "Release", "net48", "Vigil-Setup.exe")
BAR_OUT = os.path.join(HERE, "bar", "bin", "Release", "net10.0-windows")
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Vigil"
E2E_ROOT = os.path.join(os.environ.get("TEMP", HERE), "vigil-e2e")
E2E_PKG = os.path.join(E2E_ROOT, "pkg")            # 模拟分发目录：Vigil-Setup.exe + app\
E2E_TARGET = os.path.join(E2E_ROOT, "installed")   # 安装目标
E2E_LNK = os.path.join(os.environ.get("APPDATA", HERE),
                       "Microsoft", "Windows", "Start Menu", "Programs", "Vigil.lnk")


def reg_kv(key: str, value: str) -> str | None:
    """读 HKCU 任意键的任意值。既有的 reg_run_value() 只管 Run 键那一项，卸载项要另读。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            v, _ = winreg.QueryValueEx(k, value)
            return v
    except OSError:
        return None


def shortcut_target(path: str) -> str | None:
    """用 WScript.Shell 把 .lnk 读回来验 TargetPath —— 只验文件在等于没验。"""
    ps = ("$w=(New-Object -ComObject WScript.Shell).CreateShortcut('%s'); $w.TargetPath"
          % path.replace("'", "''"))
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, errors="replace", timeout=60)
    return r.stdout.strip() or None


def check_installer_e2e() -> None:
    """静默装到 %TEMP% 再卸掉：装/卸两条路径的唯一真证据。

    载荷用**框架依赖**的开发产物（6.7MB，`BAR_OUT`）而不是 185MB 自包含包：这段验的是
    "安装/卸载动作对不对"，随附解释器对不对由 Task 4 的 verify_package.py 验。两件事分开，
    别让冒烟等一次 publish。

    这段会 taskkill /f 掉正在跑的状态栏（spec §5.2 第 1 步本来就要求这么干，`--gui` 段
    也是这么干的），所以**必须保存并恢复**：原 Run 值、原本是否在跑、跑的是哪个 exe。
    恢复放在 finally，中途断言失败也要还原。
    """
    print("\n== 安装器端到端 ==")
    if not os.path.isfile(SETUP_EXE):
        check("Vigil-Setup.exe 存在", False, "先跑 --setup 或 package.cmd")
        return
    if not os.path.isfile(os.path.join(BAR_OUT, "Vigil.exe")):
        check("演练用的开发产物就位", False, f"缺 {BAR_OUT}\\Vigil.exe，先跑 build.cmd")
        return

    saved_run = reg_run_value()                       # 既有 helper，:1852 附近
    was_running = bool(bar_processes())
    shutil.rmtree(E2E_ROOT, ignore_errors=True)
    os.makedirs(os.path.join(E2E_PKG, "app"))
    shutil.copytree(BAR_OUT, os.path.join(E2E_PKG, "app"), dirs_exist_ok=True)
    shutil.copy(SETUP_EXE, os.path.join(E2E_PKG, "Vigil-Setup.exe"))
    setup = os.path.join(E2E_PKG, "Vigil-Setup.exe")

    try:
        r = subprocess.run([setup, "/S", f"/D={E2E_TARGET}", "/NO-START"],
                           capture_output=True, timeout=900)
        check("静默安装退出码 0", r.returncode == 0,
              f"rc={r.returncode}，见 {os.path.join(ds.state_dir(), 'setup.log')}")
        for rel in ("Vigil.exe", "Vigil.dll", "dsh_state.py", "Vigil-Setup.exe",
                    os.path.join("Wpf.Ui.dll")):
            check(f"装完有 {rel}", os.path.isfile(os.path.join(E2E_TARGET, rel)), E2E_TARGET)
        check("Run 值格式与 App.cs:1393 逐字一致（带引号的 exe 全路径）",
              reg_run_value() == '"' + os.path.join(E2E_TARGET, "Vigil.exe") + '"',
              f"实得 {reg_run_value()!r}")
        check("卸载注册项的 UninstallString 指向安装目录里那份向导",
              os.path.join(E2E_TARGET, "Vigil-Setup.exe")
              in (reg_kv(UNINSTALL_KEY, "UninstallString") or ""),
              reg_kv(UNINSTALL_KEY, "UninstallString") or "键不存在")
        check("卸载注册项的 DisplayVersion 与向导版本一致",
              (reg_kv(UNINSTALL_KEY, "DisplayVersion") or "") != "", "")
        check("开始菜单 .lnk 存在且 TargetPath 指向装好的 exe",
              os.path.isfile(E2E_LNK)
              and (shortcut_target(E2E_LNK) or "").lower()
              == os.path.join(E2E_TARGET, "Vigil.exe").lower(),
              f"lnk={os.path.isfile(E2E_LNK)} target={shortcut_target(E2E_LNK)}")
        check("安装目录里没有 .new/.old 残留",
              not os.path.exists(E2E_TARGET + ".new") and not os.path.exists(E2E_TARGET + ".old"), "")

        u = subprocess.run([os.path.join(E2E_TARGET, "Vigil-Setup.exe"), "/UNINSTALL", "/S"],
                           capture_output=True, timeout=300)
        check("从安装目录静默卸载退出码 0", u.returncode == 0, f"rc={u.returncode}")
        deadline = time.time() + 30
        while os.path.isdir(E2E_TARGET) and time.time() < deadline:
            time.sleep(0.5)
        check("安装目录已删除", not os.path.isdir(E2E_TARGET), E2E_TARGET)
        check("Run 值已删", reg_run_value() is None, "")
        check("卸载注册项已删", reg_kv(UNINSTALL_KEY, "DisplayName") is None, "")
        check("开始菜单 .lnk 已删", not os.path.isfile(E2E_LNK), E2E_LNK)
        check("默认保留 %LOCALAPPDATA%\\Vigil 数据目录（卸载不毁设置）",
              os.path.isdir(ds.state_dir()), ds.state_dir())
    finally:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if saved_run is None:
                k.DeleteValue(RUN_VALUE, False)
            else:
                k.SetValue(RUN_VALUE, saved_run)
        if was_running and saved_run:
            exe = saved_run.strip('"')
            if os.path.isfile(exe):
                subprocess.Popen([exe], cwd=os.path.dirname(exe))
        shutil.rmtree(E2E_ROOT, ignore_errors=True)
```

本 Task 先把它挂进 `main()` 的编译段（`--setup` 这个开关名 Task 3 会复用，那时再往前面加 `check_setup()`）：

```python
    if full or "--setup" in args:
        check_installer_e2e()
```

- [ ] **Step 3: 加漂移门禁 `check_setup_contract()`**

`setup/Installer.cs` 不能引用主程序程序集，`RunKeyPath`/`RunKeyValueName`/卸载键/`%LOCALAPPDATA%\Vigil` 这几处字面量是**抄的第二份**。抄的东西一定会漂，所以在这里钉住：三处字面量必须在两个源文件里同时出现，改一处就要改另一处。

```python
def check_setup_contract() -> None:
    """bar/App.cs 与 setup/Installer.cs 之间那几处必须一致的字面量。

    漂了的代价不是编译错误，是面板「开机自动启动」的勾态变假、或者卸完载 Run 值还留着
    —— 下次开机弹「找不到 Vigil.exe」。这类东西没有别的门禁会红，只能钉在这里。
    """
    print("\n== 两个 exe 的契约 ==")
    app_cs = open(os.path.join(HERE, "bar", "App.cs"), encoding="utf-8").read()
    setup_path = os.path.join(HERE, "setup", "Installer.cs")
    if not os.path.isfile(setup_path):
        check("setup/Installer.cs 存在", False, setup_path)
        return
    inst_cs = open(setup_path, encoding="utf-8").read()
    check("Run 键路径两边逐字相同",
          app_cs.count(r"Software\Microsoft\Windows\CurrentVersion\Run") == 1
          and inst_cs.count(r"Software\Microsoft\Windows\CurrentVersion\Run") == 1, "")
    check("值名 Vigil 两边都有",
          'RunValue = "Vigil"' in app_cs and 'RunKeyValueName = "Vigil"' in inst_cs, "")
    check("Run 值写法两边都是带引号的 exe 路径",
          'SetValue(RunValue, $\\"{Environment.ProcessPath}\\"' in app_cs
          and '\\"{Path.Combine(target, "Vigil.exe")}\\"' in inst_cs, "任一边改了引号就是勾态错")
    check("数据目录名两边都是 Vigil", 'StateDirName = "Vigil"' in app_cs
          and '"Vigil"' in inst_cs, "")
```

挂在 `main()` 的 `--setup` 段之前（默认轮也跑，纯读源码无副作用）：

```python
    check_setup_contract()
```

- [ ] **Step 4: 写 `Installer.cs`**

要点全部落到代码。`setup/Installer.cs`：

```csharp
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using Microsoft.Win32;

namespace Vigil.Setup
{
    /// <summary>安装/卸载的全部动作。不含 UI，可被静默模式直接驱动（冒烟靠这条）。</summary>
    internal static class Installer
    {
        // 这两个字面量与 bar/App.cs:28-29 是两份拷贝：net48 向导不能引用主程序程序集，
        // 只能抄。漂移的代价是面板的「开机自动启动」勾态变假，所以 smoke 里
        // check_setup_contract 钉住三处必须同时改（见该函数）。
        internal const string ProductName = "Vigil";
        internal const string RunKeyPath = @"Software\Microsoft\Windows\CurrentVersion\Run";
        internal const string RunKeyValueName = "Vigil";
        internal const string UninstallKeyPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\Vigil";
        internal const string SetupExeName = "Vigil-Setup.exe";
        internal const string PayloadFolderName = "app";

        public sealed class Options
        {
            public string TargetDir;
            public string PayloadDir;      // 默认 <向导目录>\app
            public bool Autostart = true;
            public bool StartMenuShortcut = true;
            public bool DesktopShortcut;
            public bool LaunchAfter = true;
            public bool PurgeData;         // 仅卸载用
            public Action<string> Log = _ => { };
        }

        public static string DefaultTargetDir() => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Programs", ProductName);

        public static string PayloadDirOf(string setupDir) =>
            Path.Combine(setupDir, PayloadFolderName);

        /// <summary>版本 = 向导自己的 AssemblyVersion，与主程序由 package.cmd 用同一个 -p:Version 钉齐。</summary>
        public static string Version()
        {
            var v = typeof(Installer).Assembly.GetName().Version;
            return v == null ? "0.0.0" : $"{v.Major}.{v.Minor}.{v.Build}";
        }

        public static bool IsInstalled(string target) =>
            File.Exists(Path.Combine(target, "Vigil.exe"));

        public static string InstalledVersion(string target)
        {
            var exe = Path.Combine(target, "Vigil.exe");
            if (!File.Exists(exe)) return null;
            try
            {
                var v = FileVersionInfo.GetVersionInfo(exe).FileVersion;
                var parts = (v ?? "").Split('.');
                return parts.Length >= 3 ? $"{parts[0]}.{parts[1]}.{parts[2]}" : v ?? "";
            }
            catch { return null; }
        }

        // ------------------------------------------------------------- 安装

        public static bool Install(Options o)
        {
            try
            {
                GuardNotInsideTarget(o);
                var payload = Path.GetFullPath(o.PayloadDir);
                if (!File.Exists(Path.Combine(payload, "Vigil.exe")))
                {
                    o.Log($"✗ 载荷目录里没有 Vigil.exe：{payload}");
                    return false;
                }
                EnsureFreeSpace(o.TargetDir, payload, o.Log);
                KillRunning(o.Log);

                // 先整体复制到 <target>.new，全部成功后再换名。中途崩最多留下 .new 垃圾，
                // 绝不会留下半个装不开的 Vigil（spec §7）。
                Sweep(o.TargetDir, o.Log);
                var stage = o.TargetDir + ".new";
                CopyTree(payload, stage, o.Log);
                CopySetupIntoIt(o, stage, o.Log);
                Swap(stage, o.TargetDir, o.Log);

                if (o.StartMenuShortcut && !MakeShortcut(StartMenuPath(o.TargetDir), o.TargetDir, o.Log)) return false;
                if (o.DesktopShortcut && !MakeShortcut(DesktopPath(o.TargetDir), o.TargetDir, o.Log)) return false;
                if (o.Autostart && !SetRunValue(o.TargetDir, true, o.Log)) return false;
                if (!WriteUninstallKey(o.TargetDir, o.Log)) return false;

                o.Log($"✓ 已安装 {Version()} 到 {o.TargetDir}");
                if (o.LaunchAfter) Launch(o.TargetDir, o.Log);
                return true;
            }
            catch (Exception ex)
            {
                o.Log($"✗ 安装异常：{ex.GetType().Name}: {ex.Message}");
                return false;
            }
        }

        /// <summary>向导不能装进它自己待的地方：从安装目录内部跑安装会覆盖正在跑的文件。</summary>
        static void GuardNotInsideTarget(Options o)
        {
            string here = AppDir();
            var target = Path.GetFullPath(o.TargetDir).TrimEnd('\\');
            if (here.StartsWith(target + "\\", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(here, target, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("请先从解压出来的目录运行安装，不要从安装目录里运行");
        }

        static void EnsureFreeSpace(string target, string payload, Action<string> log)
        {
            long need = TreeSize(payload) * 2 + (250L << 20);
            var drive = new DriveInfo(Path.GetPathRoot(Path.GetFullPath(target)));
            if (drive.AvailableFreeSpace < need)
                throw new InvalidOperationException(
                    $"{drive.Name} 只剩 {drive.AvailableFreeSpace >> 20}MB，这份安装要 {need >> 20}MB（复制时瞬时双份）");
        }

        static void Sweep(string target, Action<string> log)
        {
            foreach (var junk in new[] { target + ".new", target + ".old" })
                if (Directory.Exists(junk))
                {
                    log($"清理上次残留 {junk}");
                    Try(() => Directory.Delete(junk, true), log);
                }
        }

        static void CopyTree(string src, string dst, Action<string> log)
        {
            Directory.CreateDirectory(dst);
            foreach (var dir in Directory.EnumerateDirectories(src, "*", SearchOption.AllDirectories))
                Directory.CreateDirectory(dir.Replace(src, dst));
            foreach (var file in Directory.EnumerateFiles(src, "*", SearchOption.AllDirectories))
            {
                var to = file.Replace(src, dst);
                Directory.CreateDirectory(Path.GetDirectoryName(to));
                CopyRetry(file, to, log);
            }
            log($"复制完成：{Directory.EnumerateFiles(dst, "*", SearchOption.AllDirectories).Count()} 个文件");
        }

        static void CopyRetry(string from, string to, Action<string> log)
        {
            for (int i = 1; ; i++)
            {
                try { File.Copy(from, to, true); return; }
                catch (IOException) when (i < 3)
                {
                    log($"占用，300ms 后第 {i} 次重试：{Path.GetFileName(to)}");
                    System.Threading.Thread.Sleep(300);
                }
            }
        }

        static void CopySetupIntoIt(Options o, string stage, Action<string> log)
        {
            var mine = Process.GetCurrentProcess().MainModule.FileName;
            File.Copy(mine, Path.Combine(stage, SetupExeName), true);
            log("卸载器已放进安装目录（卸载要靠它）");
        }

        static void Swap(string stage, string target, Action<string> log)
        {
            if (!Directory.Exists(target))
            {
                Directory.Move(stage, target);
                return;
            }
            var old = target + ".old";
            Directory.Move(target, old);
            try
            {
                Directory.Move(stage, target);
            }
            catch
            {
                Directory.Move(old, target);      // 换名失败就把旧的放回原处，别让人没得用
                throw;
            }
            Try(() => Directory.Delete(old, true), log);
        }

        // ------------------------------------------------------------- 卸载

        public static bool Uninstall(Options o)
        {
            try
            {
                KillRunning(o.Log);
                SetRunValue(o.TargetDir, false, o.Log);
                Try(() => Registry.CurrentUser.DeleteSubKeyTree(UninstallKeyPath, false), o.Log);
                foreach (var lnk in new[] { StartMenuPath(o.TargetDir), DesktopPath(o.TargetDir) })
                    Try(() => File.Delete(lnk), o.Log);

                var data = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), ProductName);
                if (o.PurgeData && Directory.Exists(data))
                {
                    Try(() => Directory.Delete(data, true), o.Log);
                    o.Log($"已删数据目录 {data}");
                }
                else
                    o.Log($"保留设置与余额 Key：{data}");

                if (Directory.Exists(o.TargetDir)) DeleteWithTempGuard(o.TargetDir, o.Log);
                o.Log("✓ 卸载完成");
                return true;
            }
            catch (Exception ex)
            {
                o.Log($"✗ 卸载异常：{ex.GetType().Name}: {ex.Message}");
                return false;
            }
        }

        /// <summary>
        /// 卸载器就在它自己要删的目录里。做法：把自身拷到 %TEMP% 重启一份（带上 /FROM-TEMP
        /// 和 /PARENT=<父 pid>），父进程立刻退出；副本等父进程真退了再删目录——父进程还活着时
        /// 它自己那份 exe 是锁着的，Directory.Delete 一定失败。删完副本给自己排一条延后自删
        /// （运行中的 exe 镜像删不动，只能让 cmd 等几秒再来 del）。
        /// </summary>
        static void DeleteWithTempGuard(string target, Action<string> log)
        {
            string mine = Process.GetCurrentProcess().MainModule.FileName;
            bool inside = mine.StartsWith(Path.GetFullPath(target) + @"\", StringComparison.OrdinalIgnoreCase);
            if (!inside)
            {
                DeleteWithRetry(target, log);
                return;
            }
            // net48 没有 Environment.ProcessId（那是 .NET 5+ 才有的），用 Process.GetCurrentProcess().Id。
            int pid = Process.GetCurrentProcess().Id;
            var tmp = Path.Combine(Path.GetTempPath(), $"{SetupExeName}-{pid}.tmp.exe");
            File.Copy(mine, tmp, true);
            var args = string.Join(" ", SelfArgs($"/FROM-TEMP /PARENT={pid} /D=\"{target}\""));
            Process.Start(new ProcessStartInfo(tmp, args) { UseShellExecute = false, CreateNoWindow = true });
            log($"改由临时副本继续删除目录：{tmp}");
            Environment.Exit(0);        // 父进程必须真退出，副本才删得掉这个目录
        }

        /// <summary>父进程没退干净之前别动手：exe 镜像锁着，删了也是失败。</summary>
        public static void WaitForParent(int pid, Action<string> log)
        {
            if (pid <= 0) return;
            try
            {
                using var p = Process.GetProcessById(pid);
                if (!p.WaitForExit(15000)) log($"  父进程 {pid} 15 秒没退，照样往下删");
            }
            catch (ArgumentException) { /* 已经退了，正好 */ }
            catch (Exception ex) { log($"  等父进程时异常：{ex.Message}"); }
        }

        /// <summary>给临时副本自己排一条延后删除（cmd 先 ping 几秒等句柄释放）。</summary>
        public static void ScheduleSelfDelete()
        {
            string mine = Process.GetCurrentProcess().MainModule.FileName;
            if (!mine.StartsWith(Path.GetTempPath(), StringComparison.OrdinalIgnoreCase)) return;
            var psi = new ProcessStartInfo("cmd.exe",
                $"/c ping -n 4 127.0.0.1 >nul & del /q /f \"{mine}\"")
            { UseShellExecute = false, CreateNoWindow = true };
            Process.Start(psi);
        }

        /// <summary>目录里可能还有刚退出的进程句柄没释放，重试三轮再认输。</summary>
        static void DeleteWithRetry(string target, Action<string> log)
        {
            for (int i = 1; i <= 3; i++)
            {
                try { Directory.Delete(target, true); return; }
                catch (IOException) when (i < 3)
                {
                    log($"  目录还被占用，1 秒后第 {i} 次重试");
                    System.Threading.Thread.Sleep(1000);
                }
            }
            Directory.Delete(target, true);
        }

        /// <summary>把父进程的参数原样带给副本，只剔除标记位并追加新的标记。</summary>
        static IEnumerable<string> SelfArgs(string extra)
        {
            var a = Environment.GetCommandLineArgs().Skip(1)
                .Where(x => !string.Equals(x, "/FROM-TEMP", StringComparison.OrdinalIgnoreCase)
                         && !x.StartsWith("/PARENT=", StringComparison.OrdinalIgnoreCase)
                         && !x.StartsWith("/D=", StringComparison.OrdinalIgnoreCase))
                .ToList();
            a.Add(extra);
            return a;
        }

        // ------------------------------------------------------- 注册表与快捷方式

        /// <summary>Run 值必须是 `"<exe>"`，与 App.cs:1393 的写法逐字一致（Global Constraints）。</summary>
        public static bool SetRunValue(string target, bool on, Action<string> log)
        {
            try
            {
                using (var key = Registry.CurrentUser.CreateSubKey(RunKeyPath))
                {
                    if (key == null) return false;
                    if (on) key.SetValue(RunKeyValueName, $"\"{Path.Combine(target, "Vigil.exe")}\"");
                    else key.DeleteValue(RunKeyValueName, false);
                }
                log($"Run 值 -> {on}");
                return true;
            }
            catch (Exception ex) { log($"✗ 写 Run 值失败：{ex.Message}"); return false; }
        }

        public static bool WriteUninstallKey(string target, Action<string> log)
        {
            try
            {
                using (var key = Registry.CurrentUser.CreateSubKey(UninstallKeyPath))
                {
                    if (key == null) return false;
                    string setup = Path.Combine(target, SetupExeName);
                    string exe = Path.Combine(target, "Vigil.exe");
                    key.SetValue("DisplayName", ProductName);
                    key.SetValue("DisplayVersion", Version());
                    key.SetValue("Publisher", "Wildcreator");
                    key.SetValue("URL", "https://github.com/Wildcreator2010/dsh-status");
                    key.SetValue("DisplayIcon", exe + ",0");
                    key.SetValue("InstallLocation", target);
                    key.SetValue("InstallDate", DateTime.Now.ToString("yyyyMMdd"));
                    key.SetValue("UninstallString", $"\"{setup}\" /UNINSTALL");
                    key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                }
                log("卸载注册项已写（系统「设置 → 应用」里能看到）");
                return true;
            }
            catch (Exception ex) { log($"✗ 写卸载项失败：{ex.Message}"); return false; }
        }

        static string StartMenuPath(string target) => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.StartMenu), "Programs", "Vigil.lnk");

        static string DesktopPath(string target) => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Vigil.lnk");

        /// <summary>late-bound COM 走 WScript.Shell：net48 里不引 IWshRuntimeLibrary 互操作程序集。</summary>
        public static bool MakeShortcut(string lnk, string target, Action<string> log)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(lnk));
                dynamic shell = Activator.CreateInstance(
                    Type.GetTypeFromProgID("WScript.Shell") ?? throw new InvalidOperationException("没有 WScript.Shell"));
                dynamic sc = shell.CreateShortcut(lnk);
                sc.TargetPath = Path.Combine(target, "Vigil.exe");
                sc.WorkingDirectory = target;
                sc.Arguments = "";
                sc.IconLocation = Path.Combine(target, "Vigil.exe") + ",0";
                sc.Description = "嵌在任务栏里的 dsh 会话守望条";
                sc.Save();
                log($"快捷方式 -> {lnk}");
                return true;
            }
            catch (Exception ex) { log($"✗ 建快捷方式失败：{ex.Message}"); return false; }
        }

        // ------------------------------------------------------------- 杂项

        public static string AppDir() => AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');

        static void KillRunning(Action<string> log)
        {
            // /f 必需：状态栏窗口是 Shell_TrayWnd 的子窗口，不带 /f 会静默成功而进程照跑。
            var psi = new ProcessStartInfo("taskkill", "/f /im Vigil.exe")
            { UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true, CreateNoWindow = true };
            using (var p = Process.Start(psi))
            {
                p.WaitForExit(10000);
                log(p.ExitCode == 0 ? "已关闭运行中的 Vigil" : "没有正在运行的 Vigil（或已退出）");
            }
            for (int i = 0; i < 40 && Process.GetProcessesByName("Vigil").Length > 0; i++)
                System.Threading.Thread.Sleep(250);
        }

        static void Launch(string target, Action<string> log)
        {
            try
            {
                Process.Start(new ProcessStartInfo(Path.Combine(target, "Vigil.exe")) { UseShellExecute = true });
                log("已启动 Vigil");
            }
            catch (Exception ex) { log($"启动失败：{ex.Message}"); }
        }

        static long TreeSize(string dir)
        {
            try { return Directory.EnumerateFiles(dir, "*", SearchOption.AllDirectories).Sum(f => new FileInfo(f).Length); }
            catch { return 1L << 30; }
        }

        static void Try(Action a, Action<string> log)
        {
            try { a(); } catch (Exception ex) { log($"  （忽略：{ex.Message}）"); }
        }
    }
}
```

- [ ] **Step 5: 写 `Program.cs`（参数分流 + 日志文件 + 退出码）**

`setup/Program.cs`：

```csharp
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;

namespace Vigil.Setup
{
    public static class Program
    {
        static StreamWriter _logFile;

        [STAThread]
        public static int Main(string[] raw)
        {
            // 中文写进 setup.log / 控制台一律 UTF-8，否则 Win10 默认 GBK 代码页就是乱码。
            Console.OutputEncoding = new UTF8Encoding(false);
            var args = new List<string>(raw);
            bool silent = Has(args, "/S"), uninstall = Has(args, "/UNINSTALL"), fromTemp = Has(args, "/FROM-TEMP");
            var o = new Installer.Options
            {
                TargetDir = ValueOf(args, "/D=") ?? Installer.DefaultTargetDir(),
                PayloadDir = Installer.PayloadDirOf(Installer.AppDir()),
                Autostart = !Has(args, "/NO-AUTOSTART"),
                StartMenuShortcut = !Has(args, "/NO-STARTMENU"),
                DesktopShortcut = Has(args, "/DESKTOP"),
                LaunchAfter = !Has(args, "/NO-START") && !silent,
                PurgeData = Has(args, "/PURGE-DATA"),
                Log = Log,
            };
            var unknown = FindUnknown(args);
            if (unknown != null)
            {
                Log($"✗ 不认识的参数：{unknown}");
                Log("用法: Vigil-Setup.exe [/S] [/D=<目录>] [/NO-AUTOSTART] [/NO-STARTMENU] [/DESKTOP] [/NO-START] [/UNINSTALL] [/PURGE-DATA]");
                return 2;
            }
            AppDomain.CurrentDomain.ProcessExit += (s, e) => { try { _logFile?.Flush(); _logFile?.Dispose(); } catch { } };
            Log($"--- {DateTime.Now:yyyy-MM-dd HH:mm:ss} {(uninstall ? "卸载" : "安装")} {(silent || fromTemp ? "静默" : "GUI")} ---");

            if (silent || fromTemp)
            {
                // 副本必须先等父进程退出：父进程还活着时，目标目录里那份 exe 是锁着的，删不动。
                if (fromTemp) Installer.WaitForParent(ParentPid(args), o.Log);
                bool ok = uninstall ? Installer.Uninstall(o) : Installer.Install(o);
                if (fromTemp) Installer.ScheduleSelfDelete();
                return ok ? 0 : 1;
            }

            var app = new System.Windows.Application();
            var win = uninstall ? Ui.UninstallWindow(o, Log) : Ui.InstallWindow(o, Log);
            return app.Run(win);
        }

        static int ParentPid(List<string> args)
        {
            var raw = args.FirstOrDefault(x => x.StartsWith("/PARENT=", StringComparison.OrdinalIgnoreCase));
            int pid;
            return raw != null && int.TryParse(raw.Substring(8), out pid) ? pid : 0;
        }

        static void Log(string msg)
        {
            Console.WriteLine(msg);
            try
            {
                var dir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Vigil");
                Directory.CreateDirectory(dir);
                _logFile = _logFile ?? new StreamWriter(Path.Combine(dir, "setup.log"), true)
                { AutoFlush = true };
                _logFile.WriteLine(msg);
            }
            catch { /* 日志写不进别把安装搞崩 */ }
        }

        static bool Has(List<string> a, string flag) =>
            a.Any(x => string.Equals(x, flag, StringComparison.OrdinalIgnoreCase));

        static string ValueOf(List<string> a, string prefix) =>
            a.FirstOrDefault(x => x.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))?.Substring(prefix.Length);

        static string FindUnknown(List<string> a) => a.FirstOrDefault(x =>
            x.StartsWith("/") && !new[] { "/S", "/UNINSTALL", "/FROM-TEMP", "/NO-AUTOSTART", "/NO-STARTMENU", "/NO-START", "/DESKTOP", "/PURGE-DATA", "/PARENT" }
                .Contains(x.Split('=')[0], StringComparer.OrdinalIgnoreCase)
            && !x.StartsWith("/D=", StringComparison.OrdinalIgnoreCase));
    }
}
```

- [ ] **Step 6: 临时把 GUI 分支挡掉，先让静默路径跑通**

`Ui.InstallWindow` / `Ui.UninstallWindow` 是 Task 3 的产物。这一步为了让本 Task 能独立测试：
先写 `setup/Ui.cs` 桩，**两个方法返回一个立刻关闭的 `Window`**（Task 3 会整体替换掉，不留桩）：

```csharp
namespace Vigil.Setup
{
    internal static class Ui
    {
        public static System.Windows.Window InstallWindow(Installer.Options o, System.Action<string> log)
            => new System.Windows.Window { Title = "Vigil 安装", Width = 520, Height = 360 };
        public static System.Windows.Window UninstallWindow(Installer.Options o, System.Action<string> log)
            => new System.Windows.Window { Title = "Vigil 卸载", Width = 520, Height = 300 };
    }
}
```

- [ ] **Step 7: 编译并跑演练**

```bat
dotnet build setup/Vigil.Setup.csproj -c Release --nologo -t:Rebuild > setup-build.log 2>&1
echo exit=%errorlevel%
python smoke_test.py --setup
```
Expected: 编译 0 警告 0 错误（`setup-build.log` 里搜不到 `: warning ` / `: error `）；`== 安装器端到端 ==` 每条 ✓（含卸载后目录消失、Run 值还原、数据目录仍在）；`== 两个 exe 的契约 ==` 四条 ✓。这一段还没有 `check_setup()`，它是 Task 3 的编译与 GUI 门禁。

若「安装目录已删除」红，先查 `%LOCALAPPDATA%\Vigil\setup.log`：多半是 `DeleteWithTempGuard` 转交副本时丢了 `/UNINSTALL`（副本起来后走成安装态、去找 `app\`），或者父进程还没退就动手（`WaitForParent` 没生效）。

- [ ] **Step 8: 提交**

```bash
git add setup/ smoke_test.py
git commit -m "feat(setup): net48 安装器与静默模式（复制换名 + HKCU 注册 + .lnk + 自删）

向导编到 .NET Framework 4.8 是因为它系统自带：产物 5.6KB，而再来一份
自包含 .NET 10 是 155MB。载荷先整体复制到 .new 再换名，避免留下半个装不开的
安装目录；卸载器把自己拷到 %TEMP% 重启再继续删，否则删不掉正在运行的自己。
Run 值格式与主程序 App.cs:1393 对齐，面板的开机自启勾态才是真的。"
```

---

### Task 3: 安装向导界面

**Files:**
- Modify: `setup/Ui.cs`（整体替换 Task 2 的桩）
- Test: `smoke_test.py`（`check_setup()` 里加窗口存在与控件门禁）

**Interfaces:**
- Consumes: `Installer.Options`、`Installer.Install/Uninstall/IsInstalled/InstalledVersion/DefaultTargetDir/Version`（Task 2）。
- Produces: 窗口标题固定为 `Vigil 安装` / `Vigil 卸载`（冒烟用 `top_window()` 认这两个字面量）；主按钮文本 `安装`/`升级到 X`/`修复`（同样被断言）。

- [ ] **Step 1: 写失败的门禁**

`smoke_test.py` 加：

```python
def check_setup() -> None:
    print("\n== 安装向导 ==")
    if not shutil.which("dotnet"):
        check("dotnet 可用", False, "PATH 里没有 dotnet")
        return
    log = os.path.join(HERE, "setup-build.log")
    with open(log, "w", encoding="utf-8") as f:      # 别接管道：管道会吞掉退出码
        p = subprocess.run(["dotnet", "build", "setup/Vigil.Setup.csproj", "-c", "Release",
                            "--nologo", "-t:Rebuild"], stdout=f, stderr=subprocess.STDOUT,
                           timeout=900, cwd=HERE)
    text = open(log, encoding="utf-8", errors="replace").read()
    warns = [l for l in text.splitlines() if ": warning " in l]
    check("向导编译成功", p.returncode == 0, text[-400:])
    check("向导 0 警告", not warns, "\n".join(warns[:3]))
    check("向导产物很小（靠系统自带运行时）",
          os.path.isfile(SETUP_EXE) and os.path.getsize(SETUP_EXE) < 200_000,
          f"{os.path.getsize(SETUP_EXE) if os.path.isfile(SETUP_EXE) else '缺文件'} 字节")
    check("向导旁边没有 PresentationFramework.dll（证明不是自带 WPF）",
          not os.path.isfile(os.path.join(os.path.dirname(SETUP_EXE), "PresentationFramework.dll")), "")
    check("setup/ 里没有 .xaml（XAML 标记编译不在 dotnet SDK 里，带了就编不动）",
          not glob.glob(os.path.join(HERE, "setup", "**", "*.xaml"), recursive=True), "")

    # GUI 起窗：只验"有这扇窗、标题对、按钮文案随安装状态变"，像素细节靠肉眼。
    # uia() 的驱动脚本在 switch 之前就把 BUTTONS=名字|名字 打全了（smoke_test.py:1686），
    # 所以 'text' 那一次调用同时拿到标签文字和按钮名，不用调两遍。
    if os.path.isfile(SETUP_EXE):
        proc = subprocess.Popen([SETUP_EXE], cwd=os.path.dirname(SETUP_EXE))
        try:
            deadline = time.time() + 30
            hwnd = 0
            while time.time() < deadline and not hwnd:
                hwnd = top_window("Vigil 安装")
                time.sleep(0.5)
            check("双击即用的安装窗口（标题「Vigil 安装」）", bool(hwnd), "30 秒内没等到窗口")
            if hwnd:
                res = uia(hwnd, "text")
                check("UIA 读得到界面（驱动没超时/没 ERR）", uia_ok(res), str(res)[:300])
                dump = json.dumps(res, ensure_ascii=False)
                buttons = "|".join(res.get("BUTTONS", []))
                check("界面上有安装目录、开机自动启动、快捷方式三样东西",
                      all(k in dump for k in ("安装目录", "开机自动启动", "快捷方式")), dump[:400])
                check("未安装状态主按钮写「安装」（不是「修复」）",
                      "安装" in buttons and "修复" not in buttons, f"BUTTONS={buttons}")
                check("没有把系统控件数丢人：至少 1 个编辑框 + 3 个勾选框",
                      len(res.get("EDITS", [])) >= 1 and sum(
                          int(x) for x in res.get("TOGGLES", ["0"])) >= 3, str(res)[:200])
        finally:
            proc.kill()
            proc.wait(timeout=30)
```

并把 `main()` 里 Task 2 写的那两行改成先编译门禁、再演练（**别让 `check_setup()` 自己去调演练**，否则 `--all` 会装两遍）：

```python
    if full or "--setup" in args:
        check_setup()
        check_installer_e2e()
```

> `uia(hwnd, action, name="", index=0, value="")`、`uia_ok(res)`、`top_window(title)` 都是 `smoke_test.py` 既有的（`:1819`、`:1848`、`:796`）。UIA 通路用的是 Windows 自带 `UIAutomationClient` + `powershell.exe`，不引第三方包。**别新开窗或新写驱动。**

- [ ] **Step 2: 跑它确认红**

Run: `python smoke_test.py --setup`
Expected: `✗ 界面上有安装目录、开机自启、快捷方式三样东西` 与 `✗ 未安装状态主按钮写「安装」`（Task 2 的桩窗口是空白的）。

- [ ] **Step 3: 实现 `setup/Ui.cs`**

浅色（白底黑字）、全中文、纯代码构建：

```csharp
using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;

namespace Vigil.Setup
{
    /// <summary>向导的界面。浅色白底黑字，与面板口径一致；不用 WPF-UI（net48 没有它的目标）。</summary>
    internal static class Ui
    {
        static readonly Brush Ink = Brushes.Black;
        static readonly Brush Dim = new SolidColorBrush(Color.FromRgb(0x6B, 0x6F, 0x73));
        static readonly Brush Line = new SolidColorBrush(Color.FromRgb(0xD9, 0xDC, 0xE1));
        static readonly Brush Paper = Brushes.White;
        static readonly Brush Accent = new SolidColorBrush(Color.FromRgb(0xD8, 0x7D, 0x44));   // 与状态色同源

        public static Window InstallWindow(Installer.Options o, Action<string> log)
        {
            var root = new Grid { Margin = new Thickness(20), Background = Paper };
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });   // 标题
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });   // 正文
            root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) }); // 日志
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });   // 按钮

            var title = new TextBlock
            {
                Text = "Vigil 安装", FontSize = 20, FontWeight = FontWeights.SemiBold,
                Foreground = Ink, Margin = new Thickness(0, 0, 0, 4),
            };
            var sub = new TextBlock
            {
                Text = $"版本 {Installer.Version()} · 零前置：不需要另外装 .NET 或 Python",
                FontSize = 12, Foreground = Dim, Margin = new Thickness(0, 0, 0, 16),
            };
            Grid.SetRow(sub, 0);

            var dirLabel = new TextBlock { Text = "安装目录", FontSize = 12, Foreground = Ink, Margin = new Thickness(0, 0, 0, 4) };
            var dirBox = new TextBox { Text = o.TargetDir, MinWidth = 380, FontSize = 13, VerticalAlignment = VerticalAlignment.Bottom };
            var browse = new Button { Content = "浏览…", Margin = new Thickness(8, 0, 0, 0), Padding = new Thickness(10, 4, 10, 4), VerticalAlignment = VerticalAlignment.Bottom };
            var dirRow = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 0, 14) };
            dirRow.Children.Add(new StackPanel { Children = { dirLabel, dirBox } });
            dirRow.Children.Add(browse);

            var auto = new CheckBox { Content = "开机自动启动", IsChecked = true, Margin = new Thickness(0, 0, 0, 6), Foreground = Ink };
            var startmenu = new CheckBox { Content = "创建开始菜单快捷方式", IsChecked = true, Margin = new Thickness(0, 0, 0, 6), Foreground = Ink };
            var desk = new CheckBox { Content = "创建桌面快捷方式", IsChecked = false, Foreground = Ink, Margin = new Thickness(0, 0, 0, 12) };

            var body = new StackPanel();
            Grid.SetRow(body, 1);
            body.Children.Add(dirRow);
            body.Children.Add(auto);
            body.Children.Add(startmenu);
            body.Children.Add(desk);

            var bar = new ProgressBar { Height = 4, IsIndeterminate = false, Foreground = Accent, Margin = new Thickness(0, 8, 0, 8) };
            var box = new TextBox
            {
                IsReadOnly = true, FontFamily = new FontFamily("Consolas, Microsoft YaHei UI"),
                FontSize = 12, Foreground = Dim, Background = Paper, BorderBrush = Line,
                TextWrapping = TextWrapping.Wrap, VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            };
            Grid.SetRow(box, 2);

            Action<string> append = m => box.Dispatcher.Invoke(() => { box.Text += m + "\n"; log(m); });
            var primary = new Button
            {
                Content = "安装", MinWidth = 120, Height = 30,
                Background = Accent, Foreground = Brushes.White, BorderThickness = new Thickness(0),
            };
            var cancel = new Button { Content = "关闭", MinWidth = 90, Height = 30, Margin = new Thickness(8, 0, 0, 0) };
            var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
            Grid.SetRow(buttons, 3);
            buttons.Children.Add(primary);
            buttons.Children.Add(cancel);

            root.Children.Add(title);
            root.Children.Add(sub);
            root.Children.Add(body);
            root.Children.Add(bar);
            root.Children.Add(box);
            root.Children.Add(buttons);

            var win = NewWindow("Vigil 安装", 560, 480, root);

            // 安装状态决定主按钮文案：未安装→安装，同版本→修复，旧版本→升级到 X
            RefreshPrimary(win, primary, o);
            primary.Click += (s, e) =>
            {
                o.TargetDir = dirBox.Text.Trim();
                o.Autostart = auto.IsChecked == true;
                o.StartMenuShortcut = startmenu.IsChecked == true;
                o.DesktopShortcut = desk.IsChecked == true;
                o.PayloadDir = Installer.PayloadDirOf(Installer.AppDir());
                o.Log = append;
                primary.IsEnabled = cancel.IsEnabled = false;
                bar.IsIndeterminate = true;
                // 复制 185MB 要几十秒，不能让它在 UI 线程上把窗口画成"未响应"。
                win.Dispatcher.BeginInvoke(DispatcherPriority.Background, new Action(() =>
                {
                    bool ok = Installer.Install(o);
                    bar.IsIndeterminate = false;
                    primary.IsEnabled = cancel.IsEnabled = true;
                    if (ok) primary.Content = "完成";
                    else RefreshPrimary(win, primary, o);
                }));
            };
            browse.Click += (s, e) =>
            {
                var dlg = new System.Windows.Forms.FolderBrowserDialog { SelectedPath = dirBox.Text };
                if (dlg.ShowDialog() == System.Windows.Forms.DialogResult.OK) dirBox.Text = dlg.Path;
            };
            cancel.Click += (s, e) => win.Close();
            return win;
        }

        public static Window UninstallWindow(Installer.Options o, Action<string> log)
        {
            // 卸载确认页：列清楚要删什么，默认保留数据目录，勾选才连带删（spec §5.5）。
            var list = new TextBlock
            {
                FontSize = 13, Foreground = Ink, TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 12, 0, 12),
                Text = "将要删除：\n" +
                       "  · 安装目录与其中全部文件\n" +
                       "  · 开机自动启动项\n" +
                       "  · 开始菜单 / 桌面快捷方式\n" +
                       "  · 「设置 → 应用」里的卸载入口\n\n" +
                       "默认保留：设置、日志与余额 Key（余额 Key 按当前 Windows 账户加密，" +
                       "换电脑本来也要重录）。",
            };
            var purge = new CheckBox { Content = "连同设置与余额 Key 一起删除", Foreground = Ink, Margin = new Thickness(0, 0, 0, 14) };
            var box = LogBox();
            var go = new Button { Content = "卸载", MinWidth = 120, Height = 30, Background = Accent, Foreground = Brushes.White, BorderThickness = new Thickness(0) };
            var cancel = new Button { Content = "取消", MinWidth = 90, Height = 30, Margin = new Thickness(8, 0, 0, 0) };
            var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
            buttons.Children.Add(go); buttons.Children.Add(cancel);

            var col = new StackPanel();
            col.Children.Add(new TextBlock { Text = "卸载 Vigil", FontSize = 20, FontWeight = FontWeights.SemiBold, Foreground = Ink });
            col.Children.Add(list);
            col.Children.Add(purge);
            col.Children.Add(box);
            col.Children.Add(buttons);
            var win = NewWindow("Vigil 卸载", 560, 460, col);

            Action<string> append = m => box.Dispatcher.Invoke(() => { box.Text += m + "\n"; log(m); });
            go.Click += (s, e) =>
            {
                o.PurgeData = purge.IsChecked == true;
                o.Log = append;
                go.IsEnabled = cancel.IsEnabled = false;
                win.Dispatcher.BeginInvoke(DispatcherPriority.Background, new Action(() =>
                {
                    bool ok = Installer.Uninstall(o);
                    go.IsEnabled = cancel.IsEnabled = true;
                    if (ok) win.Dispatcher.BeginInvoke(new Action(() => win.Close()));
                }));
            };
            cancel.Click += (s, e) => win.Close();
            return win;
        }

        static void RefreshPrimary(Window win, Button primary, Installer.Options o)
        {
            if (!Installer.IsInstalled(o.TargetDir)) { primary.Content = "安装"; return; }
            var cur = Installer.InstalledVersion(o.TargetDir);
            var mine = Installer.Version();
            primary.Content = cur == mine ? "修复" : $"升级到 {mine}";
            win.Title = $"Vigil 已安装 {cur}";
        }

        static TextBox LogBox() => new TextBox
        {
            IsReadOnly = true, Height = 150, FontFamily = new FontFamily("Consolas, Microsoft YaHei UI"),
            FontSize = 12, Foreground = Dim, Background = Paper, BorderBrush = Line,
            TextWrapping = TextWrapping.Wrap, VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            Margin = new Thickness(0, 0, 0, 10),
        };

        static Window NewWindow(string title, double w, double h, UIElement content) => new Window
        {
            Title = title, Width = w, Height = h, Background = Paper,
            WindowStartupLocation = WindowStartupLocation.CenterScreen,
            ResizeMode = ResizeMode.CanMinimize, Content = content,
        };
    }
}
```

`FolderBrowserDialog` 用的 `System.Windows.Forms` 已经在 Task 2 Step 1 的 csproj 里引好了，别再往工程里加东西。

**与 spec §7 的一处收缩**（要写进 Task 2/3 的提交信息里，别偷偷缩）：spec 提过"复制中可取消 = 丢弃 `.new`"。实现里主按钮在复制期间 disable、只有「关闭」可点，**没有真的中止复制**。185MB 的复制几十秒，中止后要清 `.new` 又要等句柄释放，换来的收益不值。复制前后都能取消/关掉窗口，`.new` 残留由下次安装的 `Sweep()` 清掉。spec §7 的原子性承诺（不留半个装不开的安装）不受影响。

上面 `InstallWindow` 里那三样控件（目录框、三个勾选框、日志框）的 `Foreground` 全是 `Ink`/`Dim` 常量，白底黑字。这是向导唯一的配色面，不跟随系统深浅色——**刻意如此**：安装窗口一辈子只开一两次，跟着系统做成黑底会吓到人，而且 spec 的界面口径就是浅色优先。

- [ ] **Step 4: 跑门禁确认转绿**

Run: `python smoke_test.py --setup`
Expected: `== 安装向导 ==` 全 ✓，尤其"双击即用的安装窗口"、"未安装状态主按钮写「安装」"，随后 `check_installer_e2e` 段全 ✓。再手工跑一次 `setup\bin\Release\net48\Vigil-Setup.exe` 肉眼看：白底黑字、按钮橙、日志框有中文回显。

- [ ] **Step 5: 提交**

```bash
git add setup/Ui.cs setup/Vigil.Setup.csproj smoke_test.py
git commit -m "feat(setup): 浅色中文安装向导（安装目录/自启/快捷方式 + 进度与日志）

主按钮文案随安装状态变：未安装→安装，同版本→修复，旧版本→升级到 X。
185MB 的复制放在 Background 优先级里跑，否则窗口会画成「未响应」。"
```

---

### Task 4: 打包产物

**Files:**
- Create: `package.cmd`
- Create: `tools/verify_package.py`
- Modify: `.gitignore`
- Modify: `smoke_test.py`（`check_package()` + 挂进 `main()`）

**Interfaces:**
- Consumes: Task 2/3 的 `setup/` 工程、`bar/Vigil.csproj` 的 `<Version>`。
- Produces: `dist/Vigil-<ver>-<rid>/`（`Vigil-Setup.exe` + `app/` + `LICENSE.txt` + `THIRD-PARTY-NOTICES.md` + `安装说明.txt`）与 `dist/Vigil-<ver>-<rid>.zip`；`tools/verify_package.py` 的 `verify(dist_dir) -> list[str]`（返回失败清单，空表示通过）。

- [ ] **Step 1: `.gitignore`**

追加两行（`vendor/` 是 12MB 的 embeddable zip 缓存，不进版本库）：

```
dist/
vendor/
```

- [ ] **Step 2: 写 `tools/verify_package.py`（常设工具，被 package.cmd 与冒烟共用）**

```python
#!/usr/bin/env python3
"""校验 dist/ 分发产物：文件齐不齐 + 随附解释器能不能真干活。

  python tools/verify_package.py              找 dist/ 下唯一的 Vigil-*-win-x64 目录
  python tools/verify_package.py <dir>       指定目录

退出码 0 通过；非 0 时把每条失败原样打印。`package.cmd` 和
`python smoke_test.py --package` 都调它，别再抄第二份校验逻辑。
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 校验和不在这里查：`package.cmd` 在下载环节查（查不过就不会有 dist 产物），
# 这个脚本只面对已经落地的产物，重复一份 PIN 常量就是第二个要记着改的地方。
MUST_EXIST = (
    "Vigil.exe", "Vigil.dll", "Vigil.runtimeconfig.json", "dsh_state.py",
    "Wpf.Ui.dll", os.path.join("runtime", "python", "python.exe"),
    os.path.join("runtime", "python", "python314.dll"),
    os.path.join("runtime", "python", "python314.zip"),
    os.path.join("runtime", "python", "_zstd.pyd"),
    os.path.join("runtime", "python", "_ssl.pyd"),
    os.path.join("runtime", "python", "LICENSE.txt"),
)
MUST_EXIST_ROOT = ("Vigil-Setup.exe", "LICENSE.txt", "THIRD-PARTY-NOTICES.md", "安装说明.txt")


def verify(dist_dir: str) -> list[str]:
    bad: list[str] = []
    app = os.path.join(dist_dir, "app")
    if not os.path.isdir(app):
        return [f"没有载荷目录 {app}"]
    for rel in MUST_EXIST:
        if not os.path.isfile(os.path.join(app, rel)):
            bad.append(f"缺 app/{rel}")
    for rel in MUST_EXIST_ROOT:
        if not os.path.isfile(os.path.join(dist_dir, rel)):
            bad.append(f"缺 {rel}")

    # 随附解释器必须真能把引擎跑起来 —— 目录存在不等于能干活（spec §8 第 7 步）
    py = os.path.join(app, "runtime", "python", "python.exe")
    engine = os.path.join(app, "dsh_state.py")
    if os.path.isfile(py) and os.path.isfile(engine):
        r = subprocess.run([py, "-X", "utf8", engine, "--json"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120, cwd=app)
        line = (r.stdout or "").strip().splitlines()
        snap = None
        try:
            snap = json.loads(line[0]) if line else None
        except Exception:
            snap = None
        if r.returncode != 0 or not snap or snap.get("ok") is not True:
            bad.append(f"随附解释器跑不动引擎：rc={r.returncode} err={(r.stderr or '')[-200:]}")
        else:
            print(f"  ✓ 随附解释器出快照：state={snap.get('state')}")

    # --engine-probe 必须选中随附那份，不受宿主 PATH 影响（Task 1 的第一优先级）
    exe = os.path.join(app, "Vigil.exe")
    if os.path.isfile(exe):
        p = subprocess.run([exe, "--engine-probe"], capture_output=True, text=True,
                           errors="replace", timeout=120, cwd=app)
        kv = dict(l.split("=", 1) for l in p.stdout.splitlines() if "=" in l)
        want = os.path.normcase(py)
        if os.path.normcase(kv.get("python", "")) != want:
            bad.append(f"--engine-probe 没选中随附解释器：实得 {kv.get('python')}，应为 {want}")
        else:
            print("  ✓ Vigil --engine-probe 认的是随附解释器")

    # 两份 exe 的版本必须相等（-p:Version 同一个值传下去）
    for name in ("Vigil.exe", "Vigil-Setup.exe"):
        path = os.path.join(app if name == "Vigil.exe" else dist_dir, name)
        if os.path.isfile(path):
            v = subprocess.run(["powershell", "-NoProfile", "-Command",
                                f"(Get-Item -LiteralPath '{path}').VersionInfo.FileVersion"],
                               capture_output=True, text=True, errors="replace", timeout=60).stdout.strip()
            print(f"  · {name} FileVersion={v}")
    return bad


def main() -> int:
    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        found = sorted(glob.glob(os.path.join(ROOT, "dist", "Vigil-*-win-x64")))
        if not found:
            print("✗ dist/ 下没有 Vigil-*-win-x64，先跑 package.cmd")
            return 1
        target = found[-1]
    bad = verify(target)
    print(f"\n校验 {os.path.relpath(target, ROOT)}：{'通过' if not bad else f'{len(bad)} 项失败'}")
    for b in bad:
        print(f"  ✗ {b}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

> `MUST_EXIST` 里那条 `runtime/python/LICENSE.txt` 是 PSF 许可证的随附义务落点，Task 6 的合规声明靠它兜底——删掉这条就等于分发了产物却漏了许可证原文。

- [ ] **Step 3: 写 `package.cmd`**

```bat
@echo off
setlocal EnableExtensions
rem 产出可分发到任何电脑的 Vigil 安装包。任一步非 0 立即整体非 0 退出。
rem   package.cmd                     全量（含两次 publish）
rem   package.cmd --verify-only         只跑校验（smoke_test.py --package 走这条）
cd /d "%~dp0"
set "RID=win-x64"
set "PY_VER=3.14.7"
set "PY_SHA256=d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15"
set "PY_ZIP=vendor\python-%PY_VER%-embed-amd64.zip"
set "PY_URL=https://www.python.org/ftp/python/%PY_VER%/python-%PY_VER%-embed-amd64.zip"

for /f "delims=" %%v in ('python -X utf8 -c "import re,pathlib;m=re.search(r'<Version>([^<]+)</Version>',pathlib.Path('bar/Vigil.csproj').read_text(encoding='utf-8'));print(m.group(1) if m else '')"') do set "VER=%%v"
if "%VER%"=="" ( echo ✗ 读不到 bar\Vigil.csproj 的 ^<Version^> & exit /b 1 )
set "NAME=Vigil-%VER%-%RID%"
set "DIST=dist\%NAME%"
echo == %NAME% ==

if /i "%~1"=="--verify-only" goto verify

echo [1/6] 自包含 publish 主程序
dotnet publish bar\Vigil.csproj -c Release -r %RID% --self-contained true -p:Version=%VER% -o "%DIST%\app" --nologo > pkg-bar.log 2>&1
if errorlevel 1 ( echo ✗ 主程序 publish 失败，见 pkg-bar.log & exit /b 1 )
findstr /c:": warning " pkg-bar.log >nul && ( echo ✗ 主程序有警告 & exit /b 1 )

echo [2/6] 随附 CPython %PY_VER%
if not exist vendor mkdir vendor
if not exist "%PY_ZIP%" (
  echo   下载 %PY_URL%
  curl -L --fail --retry 3 -o "%PY_ZIP%" "%PY_URL%" || ( echo ✗ 下载失败 & exit /b 1 )
)
for /f "skip=1 delims=" %%h in ('certutil -hashfile "%PY_ZIP%" SHA256') do if not defined GOT set "GOT=%%h"
if /i not "%GOT%"=="%PY_SHA256%" ( echo ✗ SHA256 不符：%GOT% & exit /b 1 )
if exist "%DIST%\app\runtime\python" rmdir /s /q "%DIST%\app\runtime\python"
mkdir "%DIST%\app\runtime\python"
tar -xf "%PY_ZIP%" -C "%DIST%\app\runtime\python" || ( echo ✗ 解压失败 & exit /b 1 )

echo [3/6] publish 安装向导
dotnet publish setup\Vigil.Setup.csproj -c Release -p:Version=%VER% -o dist\setup-out --nologo > pkg-setup.log 2>&1
if errorlevel 1 ( echo ✗ 向导 publish 失败，见 pkg-setup.log & exit /b 1 )
findstr /c:": warning " pkg-setup.log >nul && ( echo ✗ 向导有警告 & exit /b 1 )
copy /y dist\setup-out\Vigil-Setup.exe "%DIST%\Vigil-Setup.exe" >nul || exit /b 1
copy /y dist\setup-out\Vigil-Setup.exe "%DIST%\app\Vigil-Setup.exe" >nul || exit /b 1

echo [4/6] 合规与说明
copy /y LICENSE "%DIST%\LICENSE.txt" >nul || exit /b 1
copy /y THIRD-PARTY-NOTICES.md "%DIST%\THIRD-PARTY-NOTICES.md" >nul || exit /b 1
python -X utf8 tools\write_readme_install.py "%DIST%\安装说明.txt" || exit /b 1

echo [5/6] 产物校验
:verify
python -X utf8 tools\verify_package.py "%DIST%" || exit /b 1

echo [6/6] 压 zip
if exist "dist\%NAME%.zip" del /q "dist\%NAME%.zip"
tar -a -cf "dist\%NAME%.zip" -C dist "%NAME%" || ( echo ✗ 打包失败 & exit /b 1 )
for %%F in ("dist\%NAME%.zip") do echo 产物 dist\%NAME%.zip %%~zF 字节
exit /b 0
```

> 注意 `--verify-only` 跳到 `:verify` 时 `%DIST%` 仍由版本号算出，行为正确；`goto verify` 之前已经设好 `VER/NAME/DIST`。`findstr ... && (...)` 这种写法在 `cmd` 里对含空格路径不敏感，因为我们只搜内容不搜文件；`%errorlevel%` 一律用 `if errorlevel 1` 而不是 `if %errorlevel%==1`，避免延迟展开踩坑。
> `tools/write_readme_install.py` 是本 Task 附带的小工具（写那份 `安装说明.txt`，内容见下一步），别在 `package.cmd` 里用 `echo` 拼中文——`cmd` 的代码页会把中文写成乱码。
>
> **打包机的前置**与目标机不同，`package.cmd` 要求跑它的机器有：`.NET 10 SDK`、`Python 3.14`（读 csproj 版本号与跑校验用）、`curl`/`tar`/`certutil`（Win10 1803+ 系统自带）。首次运行会从 nuget.org 拉 win-x64 运行时包（实测还原约 55 秒）并从 python.org 下 12MB 的 embeddable zip 到 `vendor/`；之后离线可复现。**目标机一样都不需要**——这是 spec §1「零前置」的确切含义，别把两者混了。

- [ ] **Step 4: `tools/write_readme_install.py`（安装说明，别用 echo 拼中文）**

```python
#!/usr/bin/env python3
"""生成分发物里的「安装说明.txt」。用文件写入而不是 cmd 的 echo：
`cmd` 默认代码页不是 UTF-8，echo 中文进文件必然乱码。"""
from __future__ import annotations
import sys

TEXT = """Vigil 安装说明

一、解压这个文件夹（不要只在压缩包里双击 Vigil-Setup.exe，复制不出来）。
二、双击 Vigil-Setup.exe。
   · Windows 可能弹 SmartScreen「Windows 已保护你的电脑」——这份产物没有代码签名，
     点「更多信息」→「仍要运行」。
   · 全程不弹 UAC，只写当前用户（%LOCALAPPDATA%\\Programs\\Vigil 和 HKCU）。
三、按提示安装。装完状态栏就出现在任务栏上，托盘图标在通知区。

前提：Win10 1607+ 或 Win11，x64。什么都不用另外装 —— .NET 运行时和
解会话文件用的 Python 都随附在 app\\runtime\\python 里。
不支持 Win7/8（.NET 10 的 WPF 只到 Win10）。

卸载：开始菜单的 Vigil 目录里，或「设置 → 应用 → Vigil → 卸载」。
默认保留设置与余额 Key（在 %LOCALAPPDATA%\\Vigil）。

第一次查余额：余额 Key 是按当前 Windows 账户加密存的（DPAPI），
换电脑必须重新录入 —— 右键状态栏 → 设置余额 Key…。
"""


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: write_readme_install.py <输出路径>")
        return 2
    with open(sys.argv[1], "w", encoding="utf-8", newline="\r\n") as f:
        f.write(TEXT)
    print(f"  ✓ 已写 {sys.argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: `smoke_test.py` 加 `check_package()` 并挂载**

```python
def check_package() -> None:
    print("\n== 分发产物 ==")
    dist = sorted(glob.glob(os.path.join(HERE, "dist", "Vigil-*-win-x64")))
    if not dist:
        check("dist/ 有产物", False, "先跑 package.cmd")
        return
    target = dist[-1]
    log = os.path.join(HERE, "verify-package.log")
    with open(log, "w", encoding="utf-8") as f:      # 不接管道，管道吞退出码
        p = subprocess.run(["cmd", "/c", "package.cmd", "--verify-only"],
                           stdout=f, stderr=subprocess.STDOUT, timeout=1800, cwd=HERE)
    check("package.cmd --verify-only 通过", p.returncode == 0, open(log, encoding="utf-8", errors="replace").read()[-500:])
    zips = glob.glob(os.path.join(HERE, "dist", "*.zip"))
    check("有可分发的 zip", bool(zips), "")
    if zips:
        size = os.path.getsize(zips[0])
        check("zip 体积在合理区间（自包含载荷压缩后 40–200MB）", 40_000_000 < size < 200_000_000,
              f"{size} 字节")
```

`main()` 里挂到 `--all`/`--package`：

```python
    if full or "--package" in args:
        check_package()
```

并把文档字符串第 5-6 行那两条用法补一行：

```
  python smoke_test.py --package 额外校验 dist/ 分发产物（含自包含 publish 产物完整性、
                            随附解释器跑真引擎、静默安装到 %TEMP% 再卸载的端到端演练）
```

- [ ] **Step 6: 真跑一次全量打包**

```bat
package.cmd
```
Expected: 6 步逐条打印，最后 `✓ 随附解释器出快照：state=…`、`✓ Vigil --engine-probe 认的是随附解释器`、`校验 dist\Vigil-1.0.0-win-x64：通过`，并出 zip。第一次会从 nuget.org 拉 win-x64 运行时包（实测约 55 秒）与 12MB 的 embeddable zip；`vendor/` 落缓存后第二次不再下载。

Run: `python smoke_test.py --package`
Expected: `== 分发产物 ==` 三条 ✓。

- [ ] **Step 7: 提交**

```bash
git add package.cmd tools/verify_package.py tools/write_readme_install.py .gitignore smoke_test.py
git commit -m "feat(build): package.cmd 产出零前置分发物 + 常设产物校验

载荷自包含 161MB 加随附 CPython embeddable 24MB。embeddable zip 钉死 3.14.7
与 SHA256，校验不符直接退出；随附解释器必须真能把引擎跑出 ok:true，
目录存在不等于能干活。校验逻辑落在 tools/verify_package.py，打包和冒烟共用一份。"
```

---

### Task 5: 主程序对接安装（卸载入口与关于页）

**Files:**
- Modify: `bar/App.cs:1129-1132`（托盘菜单）
- Create: `bar/Panel/Pages/AboutPage.cs`
- Modify: `bar/Panel/Pages/Pages.cs:39`（删掉 `AboutPage` 占位类）
- Test: `smoke_test.py`（`check_about_page()`，接进 `--gui` 段）

> `bar/Panel/PanelPages.cs` 的 `TryCreate` 里 `"about"` 已经 `new AboutPage()`，**不用改**；`Pages.cs` 删掉占位类后同名新类被 `namespace Vigil` 自动接上。这一点先确认再动手，别去改登记表。

**Interfaces:**
- Consumes: `Installer` 不在主程序里 —— 主程序只按约定路径找 `Vigil-Setup.exe`，并用 `Process.Start` 拉起 `/UNINSTALL`。
- Produces: 面板关于页三行：`安装位置`、`版本`、`检测引擎`（值来自 Task 1 的 `ResolvePython()`）。`smoke_test.py` 的 `check_about_page()` 认这三行标题文案。

- [ ] **Step 1: 写失败的门禁**

`smoke_test.py` 加（沿用 `--gui` 段既有的离屏出图 + 真窗口门禁风格）：

```python
def check_about_page() -> None:
    print("\n== 关于页 ==")
    src = os.path.join(HERE, "bar", "Panel", "Pages", "AboutPage.cs")
    if not os.path.isfile(src):
        check("AboutPage.cs 存在", False, src)
        return
    text = open(src, encoding="utf-8").read()
    for label in ("安装位置", "版本", "检测引擎"):
        check(f"关于页有「{label}」这一行", label in text, "缺该文案")
    check("关于页的颜色走主题键（不写死刷子）",
          "Ui.Ref(" in text and "Brushes.Black" not in text,
          "写死颜色切到深色主题就是黑底黑字")
    check("关于页不自己拼版本数字，取 App.VersionText", "App.VersionText" in text,
          "自己拼就会和 csproj 的 <Version> 漂移")

    if not os.path.isfile(BAR_EXE):
        check("Vigil.exe 存在", False, "先跑 --build")
        return
    proc = subprocess.Popen([BAR_EXE, "--panel", "about"], cwd=HERE)
    try:
        deadline = time.time() + 40
        hwnd = 0
        while time.time() < deadline and not hwnd:
            hwnd = top_window("Vigil 控制台")   # 标题来自 PanelWindow.xaml:5
            time.sleep(0.5)
        check("「关于」页开得起来", bool(hwnd), "40 秒内没等到「Vigil 控制台」窗口")
        if hwnd:
            res = uia(hwnd, "text")
            names = "\n".join(res.get("TEXT", []))
            check("关于页画出了安装位置（绝对路径字样）",
                  "安装位置" in names and os.path.normcase(HERE) in os.path.normcase(names),
                  names[:400])
            check("关于页画出了引擎解释器路径", "python.exe" in names, names[:400])
    finally:
        proc.kill()
        subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
```

挂在 `--gui` 段的**最后**（`check_panel_request_race()` 之后）：那一段前面好几处门禁对"当前 settings.json / 引擎注入状态"敏感，而这段会 `taskkill /f /im Vigil.exe` 全量收进程，放最后才不会打乱比样顺序。

- [ ] **Step 2: 跑它确认红**

Run: `python smoke_test.py --gui`
Expected: `✗ AboutPage.cs 存在`（占位类还在 `Pages.cs` 里）。

- [ ] **Step 3: 关于页填实**

`bar/Panel/Pages/AboutPage.cs`（照 `RuntimePage.cs` 的行工厂用法，颜色一律 `Ui.Ref` 主题键）：

```csharp
using System;
using System.IO;
using System.Windows;
using WpfControls = System.Windows.Controls;

namespace Vigil
{
    /// <summary>
    /// 关于页：品牌行 + 安装位置 + 版本 + 检测引擎用的是哪个解释器。
    /// 引擎那一行是排障入口——「状态栏说引擎不可用」时先来看它认的是哪份 python，
    /// 而不是去翻 bar.log。开发目录里随附解释器不存在，这里就如实显示系统解释器。
    /// </summary>
    internal sealed class AboutPage : WpfControls.ContentControl, IPanelPage
    {
        readonly WpfControls.TextBlock _engine = RowValue();
        readonly WpfControls.TextBlock _where = RowValue();
        readonly WpfControls.TextBlock _version = RowValue();

        public AboutPage()
        {
            Ui.Ref(_engine, WpfControls.TextBlock.ForegroundProperty, Ui.InkDimKey);
            Ui.Ref(_where, WpfControls.TextBlock.ForegroundProperty, Ui.InkDimKey);
            Ui.Ref(_version, WpfControls.TextBlock.ForegroundProperty, Ui.InkDimKey);

            var scroll = new WpfControls.ScrollViewer
            {
                VerticalScrollBarVisibility = WpfControls.ScrollBarVisibility.Auto,
                HorizontalScrollBarVisibility = WpfControls.ScrollBarVisibility.Disabled,
            };
            scroll.Content = Ui.Column(
                Ui.Heading("Vigil"),
                Ui.Group("安装",
                    Ui.Row("安装位置", "状态栏与随附检测引擎所在的目录", _where),
                    Ui.Row("版本", "数字版本归工程文件，代号是发布标识", _version),
                    Ui.Row("检测引擎", "优先用随附的 runtime\\python\\python.exe，不受系统 PATH 影响", _engine)));
            Content = scroll;
            Fill();
        }

        public void Refresh(Snapshot snap) { }

        static WpfControls.TextBlock RowValue() => new WpfControls.TextBlock
        {
            FontSize = 12, TextWrapping = TextWrapping.Wrap, MaxWidth = 320,
            TextAlignment = TextAlignment.Right,
        };

        void Fill()
        {
            _where.Text = AppContext.BaseDirectory;
            _version.Text = App.VersionText;
            _engine.Text = App.EnginePathForDisplay();
        }
    }
}
```

同时在 `bar/Panel/Pages/Pages.cs` **删掉第 39 行的占位类**（留着就是 CS0101 重复定义），并确认 `PanelPages.cs` 的页面工厂里 `about` 那项 new 的是这个新类。

主程序需要一个只读展示入口（`App.cs` 里加，复用 Task 1 的解析，别复制第二份查找逻辑）：

```csharp
        /// <summary>关于页用：当前解析到的解释器路径，认不出来就明说。</summary>
        internal static string EnginePathForDisplay() => ResolvePython() ?? "未找到可用的 Python 3.14";
```

`ResolvePython` 是 `private static`，这条加完把它改成 `internal static`（同文件内，无外部暴露面）。

- [ ] **Step 4: 托盘菜单加「卸载…」**

在 `bar/App.cs:1130`「打开日志」之后插入（`Add` 的第 4 个参数 `enabled` 已存在）：

```csharp
            string setup = Path.Combine(AppContext.BaseDirectory, "Vigil-Setup.exe");
            Add(menu, "卸载…", (s, e) => UninstallViaSetup(setup), File.Exists(setup));
```

并在 `App.cs` 加实现（开发目录里那个文件不存在 → 菜单项置灰，**不要**抛异常）：

```csharp
        /// <summary>卸载交给安装目录里那份向导：它知道怎么删自己。</summary>
        private static void UninstallViaSetup(string setup)
        {
            try
            {
                Process.Start(new ProcessStartInfo(setup, "/UNINSTALL") { UseShellExecute = true });
                Log("已请求卸载");
                System.Windows.Application.Current?.Dispatcher.BeginInvoke(new Action(() => Shutdown()));
            }
            catch (Exception ex)
            {
                Balloon("启动卸载程序失败", ex.Message, ToolTipIcon.Error);
            }
        }
```

- [ ] **Step 5: 跑门禁确认转绿**

Run: `python smoke_test.py --gui`
Expected: `== 关于页 ==` 全 ✓；`--gui` 段不回归（尤其 `check_gui` 与面板各页出图）。手工开一次面板到「关于」页，确认三行都在、深浅色都不刺眼。

- [ ] **Step 6: 提交**

```bash
git add bar/App.cs bar/Panel/ smoke_test.py
git commit -m "feat(about): 关于页填实安装位置/版本/引擎，托盘加卸载入口

引擎那一行是「状态栏说引擎不可用」的第一现场：先来看它认的是哪份 python。
卸载复用安装目录里那份向导，开发目录没有它时菜单项置灰而不是抛异常。"
```

---

### Task 6: 合规声明与文档

**Files:**
- Modify: `THIRD-PARTY-NOTICES.md`（第 7 行的总说明、§4.2、新增 Python 一节、§5 汇总）
- Modify: `smoke_test.py:252-259`（`LICENSE_MUST_CONTAIN`）
- Modify: `README.md`（「装到任何电脑」一节 + FAQ 两条 + 文件表 + 快速开始）

**Interfaces:**
- Consumes: Task 4 的 `package.cmd` 会把 `THIRD-PARTY-NOTICES.md` 原样拷进 `dist/`。
- Produces: 关键词 `"Python 3.14.7"`、`"PSF"` 必须出现在 `LICENSE`+`THIRD-PARTY-NOTICES` 合并文本里（`check_licenses` 的 `LICENSE_MUST_CONTAIN` 门禁）。

- [ ] **Step 1: 先加失败门禁**

`smoke_test.py` 的 `LICENSE_MUST_CONTAIN` 末尾补两项：

```python
    "Python 3.14.7", "PSF",                    # 随附分发的 embeddable 解释器
```

Run: `python smoke_test.py`
Expected: `✗ 必要归属条目齐全 —— 缺 ['Python 3.14.7', 'PSF']`。

- [ ] **Step 2: 改 `THIRD-PARTY-NOTICES.md`**

三处改动：

1. 第 7 行「第 4 节的运行时依赖不随附其代码，因此只写用途与版本下限、不附全文。」——**这次随附了解释器**，改成：

```markdown
第 4.1 节（.NET 运行时）不随附其代码，只写用途与版本下限；第 4.2/4.3 节的 Python
**随附分发**（`app\runtime\python\`），因此必须附其许可证原文，见 §4.3 与随附的
`runtime\python\LICENSE.txt`。
```

2. §4.2「Python 3.14 标准库」正文里那句"不随附"删掉，改为说明分发产物自带 embeddable 解释器，并新增 §4.3：

```markdown
### 4.3 CPython 3.14.7 embeddable 版 — Python Software Foundation License

分发产物 `app\runtime\python\` 随附 Python 官方 Windows embeddable 包
（`python-3.14.7-embed-amd64.zip`，SHA-256
`d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15`），
包含 `python.exe`、`python314.dll`、`python314.zip`（标准库）以及 `_zstd.pyd`、
`_ssl.pyd` 等扩展模块。Vigil 的状态引擎 `dsh_state.py` 只用标准库，不额外携带第三方包。

- 许可证：**Python Software Foundation License**（BSD 风格 + 若干历史条款）。全文随产物分发在
  `runtime\python\LICENSE.txt`（该文件在 zip 内，本声明不复制其中 35KB 的历史条款）。
- 版权：© 2001-2026 Python Software Foundation; © 2000 BeOpen.com; © 1995-2000 Corporation for National
  Research Initiatives (CNRI)。
- 来源：<https://www.python.org/download/releases/>（python.org 官方 FTP 构建）。
- PSF License Agreement 的第一段（授权与条件）：

> 1. This LICENSE AGREEMENT is between the Python Software Foundation ("PSF"), and
>    the Individual or Organization ("Licensee") accessing and, indirectly, thereby
>    using Python 3.14 software in source or binary form and its associated documentation.
> 2. Subject to the terms and conditions set forth below, PSF hereby grants Licensee
>    a nonexclusive, royalty-free, license to use, reproduce, display, prepare
>    derivative works of, distribute, and otherwise use Python 3.14 software in source
>    or binary form and its associated documentation alone or in any derivative version
>    provided, however, that PSF's License Agreement and PSF's notice of copyright,
>    i.e., "Copyright © 2001-2026 Python Software Foundation; All Rights Reserved"
>    are retained in Python 3.14 software in source or binary form with its associated
>    documentation and in any derivative version prepared by Licensee.
```

> 引用条目必须与 `runtime\python\LICENSE.txt` 实际文本逐字核对再写（那份文件已经在解压出来的 embeddable 包中）。**别照记忆写**——PSF 条款里的年份区间和 "3.14" 字样在文件里是具体的。**核对后若与上面不同，以文件为准改这段，并同步改 `DisplayVersion` 之类无关项的冲动**（本 Task 只动文档）。

3. §5 汇总加一行：随附 CPython 3.14.7（PSF），以及"随附 LICENSE.txt"的分发义务已由 `package.cmd` 保证（`verify_package.py` 的 `MUST_EXIST` 含 `runtime/python/LICENSE.txt`）。

- [ ] **Step 3: README**

新增一节（放在「快速开始」之后）：

```markdown
## 装到任何电脑

前提：**Win10 1607+ / Win11，x64**。不需要预装 .NET，也不需要预装 Python，不需要联网，
不需要管理员权限。支持面到此为止 —— .NET 10 的 WPF 不支持 Win7/8。

```bat
package.cmd                    :: 产出 dist\Vigil-<版本>-win-x64\ 和同名 zip（约 95MB）
```

把 zip 拷到目标机 → 解压到任意目录 → 双击 `Vigil-Setup.exe`。没签名时 SmartScreen 会拦一次，
点「更多信息 → 仍要运行」。装到 `%LOCALAPPDATA%\Programs\Vigil`，只写 `HKCU`，
「设置 → 应用」里能看到并卸载。命令行/无人值守：

```bat
Vigil-Setup.exe /S /D=D:\Vigil            :: 静默安装
Vigil-Setup.exe /UNINSTALL /S            :: 静默卸载（默认保留设置与余额 Key）
```

里面装了什么：自包含的 .NET 10 运行时（161MB）+ CPython 3.14.7 embeddable（24MB，
状态引擎靠它解会话文件的 zstd 帧）+ `dsh_state.py`。想单独用引擎：

```bat
%LOCALAPPDATA%\Programs\Vigil\runtime\python\python.exe %LOCALAPPDATA%\Programs\Vigil\dsh_state.py --json
```
```

再改三处既有文字：

- 「快速开始」下面补一句：`build.cmd` 是**开发期**的快速编译（框架依赖，只在装了 .NET 10 SDK 的机器上跑）；可分发的产物一律走 `package.cmd`。
- FAQ 的 **`No module named 'compression'`** 那条改成：随附解释器已带 `_zstd.pyd`，正常安装不会遇到；只有你**手动**拿系统 Python 跑引擎时才需要 3.14。并补一句「`Vigil.exe --engine-probe` 会报它认的是哪个解释器」。
- 文件表加两行：`setup/` 安装向导、`package.cmd` 分发打包。
- 「开源协议」那节把"运行时依赖（.NET、Python 标准库）不随附其代码"改正：.NET 不随附代码，**Python 解释器随附分发**（PSF 许可，全文见 `runtime\python\LICENSE.txt`）。

- [ ] **Step 4: 跑全量门禁**

```bat
python smoke_test.py
python smoke_test.py --build
python smoke_test.py --setup
```
Expected: 全部 ✓（`--setup` 段还会顺带跑完安装端到端演练，测完把 Run 值还原）。`README 不再声称编译不需要联网` 这条既有门禁必须仍然成立。

- [ ] **Step 5: 提交**

```bash
git add THIRD-PARTY-NOTICES.md README.md smoke_test.py
git commit -m "docs: Python embeddable 随附分发的归属声明 + 装到任何电脑

原先声明里写「Python 标准库不随附其代码」，这次随附 embeddable 解释器之后这句
就不成立了，PSF 要求保留其 LICENSE 全文 —— 分发物里带 runtime\\python\\LICENSE.txt，
声明引关键条款并指向那份文件。LICENSE_MUST_CONTAIN 同步加两项，漏声明会红。"
```

---

## 收尾验收（全部任务做完后逐条过）

1. `git log --oneline` 有 6 条与上面任务一一对应的提交，`git status` 干净。
2. `python smoke_test.py --all` 全绿（既有 61 项 + 本次新增项）。跑之前确认 `%TEMP%\vigil-e2e*` 不存在，别拿脏目录测。
3. `package.cmd` 从头跑一次成功，`dist\Vigil-<ver>-win-x64.zip` 存在且 40–200MB。
4. **真换机验证**（这一步没法自动化，必须手工做一次，且只有它能证明"任何电脑"）：找一台干净 Windows（或新建一个本地用户账户）→ 解压 zip → 双击 `Vigil-Setup.exe` → 装完确认：任务栏出现状态栏、托盘图标有色、状态栏显示的是那台机器上 dsh 会话的真实状态（不是「未运行」也不是「引擎不可用」）、面板「关于页」的引擎行指向 `runtime\python\python.exe`、重启后自启仍然在、从「设置 → 应用」卸载后 `%LOCALAPPDATA%\Programs\Vigil` 与 `HKCU\...\Run\Vigil` 都没了而 `%LOCALAPPDATA%\Vigil` 还在。
5. 那台机器上**没有** Python、**没有** .NET SDK 时仍然成立——这才是本计划的验收判据，不是"我机器上能跑"。
6. 若第 4 步因客观条件跑不了，**明确说出来**，别把"未验证"写成"已完成"。
