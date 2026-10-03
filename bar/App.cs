using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows;
using System.Windows.Forms;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace DshBar
{
    internal static class App
    {
        private static readonly Dictionary<string, Icon> IconCache = new Dictionary<string, Icon>();
        private static readonly string StateDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "dsh-status");
        private static readonly string LogFile = Path.Combine(StateDir, "bar.log");
        private static readonly string SettingsFile = Path.Combine(StateDir, "settings.json");
        private static readonly string RefreshToken = Path.Combine(StateDir, "refresh.token");
        private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
        private const string RunValue = "dsh-status";

        private static Settings _settings;
        private static BarWindow _window;
        private static NotifyIcon _tray;
        private static StateClient _client;
        private static DispatcherTimer _watchdog;
        private static IntPtr _taskbar = IntPtr.Zero;
        private static Snapshot _last;
        private static string _lastState = "";
        private static DateTime _lastNotifyAt = DateTime.MinValue;
        private static Mutex _mutex;
        private static ToolStripMenuItem _miState, _miProject, _miBalance, _miBalanceErr, _miTips, _miAuto;
        private static DispatcherTimer _tween;
        private static int _curW, _targetW;
        private static Rect _barRect;
        private static DateTime _hoverOut = DateTime.MinValue;

        // ---- 面板请求的消费状态（见 TryConsumePanelRequest）----
        // 请求文件改成「先开窗、开成了才删」，于是要回答「没删成要不要再来一次」：
        // 无条件重试 = 每 2 秒弹一次面板抢焦点，所以按请求身份收敛。
        private const int PanelOpenTries = 2;   // 同一条请求最多尝试开窗几次
        private static string _reqStamp = "";   // 当前请求的身份：内容 + 写入时间 + 长度
        private static int _reqTries;           // 该身份已尝试开窗的次数
        private static bool _reqGivenUp;        // 该身份已放弃：不再开窗，只安静等文件可删时清掉
        private static bool _reqQueued;         // 已排进 dispatcher、还没执行完（防重入）

        [STAThread]
        private static int Main(string[] args)
        {
            Native.SetProcessDpiAwarenessContext((IntPtr)(-4));
            Directory.CreateDirectory(StateDir);

            string demo = null, panel = null, shotPage = null, shotOut = null;
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--demo" && i + 1 < args.Length) demo = args[++i];
                else if (args[i] == "--panel") panel = i + 1 < args.Length ? args[++i] : "";
                else if (args[i] == "--panel-shot")
                {
                    // 参数不够也必须在这里就非零退出，不能让它落到下面的状态栏分支：
                    // 那样等于「参数没被认出来」，调用方（冒烟）会一直等到超时才崩。
                    if (i + 2 >= args.Length)
                    {
                        Console.Error.WriteLine("用法: --panel-shot <page> <out.png|->");
                        return 2;
                    }
                    shotPage = args[++i];
                    shotOut = args[++i];
                }
            }

            // shot 模式不开窗口、不抢单实例，状态栏正在跑时也能出图，所以在互斥体之前就返回。
            if (shotPage != null) return RenderShot(shotPage, shotOut);

            _mutex = new Mutex(false, @"Local\dsh-status-bar-mutex");
            bool owned = false;
            try
            {
                owned = _mutex.WaitOne(0);
            }
            catch (AbandonedMutexException)
            {
                owned = true;
            }
            if (!owned)
            {
                RequestPanel(panel);
                return 0;
            }

            _settings = Settings.Load(SettingsFile);
            Log($"DshBar 启动 interval={_settings.Interval} notify={_settings.Notify} demo={demo ?? "-"} panel={panel ?? "-"}");

            var app = new System.Windows.Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
            // 趁一个窗口都还没建，把 Fluent 资源挂上：BarWindow 是嵌进任务栏的分层窗口，
            // 晚一步 Apply 会去动已存在窗口的背景，没必要冒这个风险。
            ApplyFluentTheme();
            _window = new BarWindow();
            _window.LeftClicked += FocusHarness;
            _window.RightClicked += pt => _tray.ContextMenuStrip.Show((int)pt.X, (int)pt.Y);
            _window.Wheel += Cycle;
            _window.HoverChanged += () => PlaceNow(new System.Windows.Interop.WindowInteropHelper(_window).Handle);
            _window.Show();
            var hwnd = new System.Windows.Interop.WindowInteropHelper(_window).Handle;
            // 启动路径也过一遍 showBar：true 就是原来的 DockNow；false 则 Undock 隐藏 ——
            // 否则 Show() 出来的窗口只被 PlaceNow 挡着不落位，会带着初始坐标浮在屏上。
            ApplyBarVisibility();

            BuildTray();
            if (demo != null) DemoOnce(demo);
            else StartClient();
            if (panel != null) OpenPanel(string.IsNullOrEmpty(panel) ? "overview" : panel);

            _watchdog = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
            _watchdog.Tick += (s, e) => Tick(hwnd);
            _watchdog.Start();

            _tween = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(60) };
            _tween.Tick += (s, e) => { PollHover(); Tween(); };
            _tween.Start();

            app.Run();
            Cleanup();
            return 0;
        }

        internal static Settings Config => _settings;
        internal static Snapshot Latest => _last;
        internal static event Action SnapshotChanged;
        internal static string LogPath => LogFile;
        internal static string DataDir => StateDir;
        internal static string PanelRequestFile => Path.Combine(StateDir, "panel.request");

        internal static void RestartEngine() => RestartClient();
        internal static void RequestBalanceRefresh() => RefreshBalance();
        internal static bool AutostartOn() => AutostartEnabled();
        internal static void SetAutostart(bool on) => ToggleAutostart(on);

        /// <summary>
        /// 把 Config.Theme 当场落到主题字典（机制就是换 Application.Resources.MergedDictionaries
        /// 里那两份，见 ApplyFluentTheme 的注释 —— 前提的合并没做完时这里是空操作）。
        /// backDrop 传 None 而不是简报写的 Mica：BarWindow 是嵌进任务栏的分层子窗口，
        /// Apply 会去动已存在窗口的背景，Task 3 起就定死 None，本任务不翻案（记入报告）。
        /// "system" 由 FluentTheme() 就地解析成当前系统深浅，与启动路径同一口径。
        /// </summary>
        internal static void ApplyTheme()
        {
            try
            {
                Wpf.Ui.Appearance.ApplicationThemeManager.Apply(
                    FluentTheme(), Wpf.Ui.Controls.WindowBackdropType.None, false);
            }
            catch (Exception ex) { Log($"应用主题失败: {ex.Message}"); }
        }

        /// <summary>
        /// showBar 决定状态条停靠/消失。隐藏 = Native.Undock（SWP_HIDEWINDOW + 脱离任务栏父子关系），
        /// 显示 = DockNow（PlaceNow 的 SetWindowPos 带 SWP_SHOWWINDOW，重新停靠顺带重新可见）。
        /// 托盘、watchdog、面板唤起通路都不经过这里 —— 隐藏状态条不杀进程。
        /// shot 路径 _window 为 null：静默返回（渲染离屏页不该改任务栏，也不该刷失败日志）。
        /// </summary>
        internal static void ApplyBarVisibility()
        {
            try
            {
                if (_window == null) return;
                var hwnd = new System.Windows.Interop.WindowInteropHelper(_window).Handle;
                if (hwnd == IntPtr.Zero) return;
                if (_settings != null && _settings.ShowBar) { DockNow(hwnd); }
                else { Native.Undock(hwnd); }
            }
            catch (Exception ex) { Log($"切换状态条可见性失败: {ex.Message}"); }
        }

        internal static void SaveSettings()
        {
            try { _settings?.Save(SettingsFile); }
            catch (Exception ex) { Log($"保存设置失败: {ex.Message}"); }
        }

        internal static void OpenInExplorer(string path)
        {
            try
            {
                System.Diagnostics.Process.Start(
                    new System.Diagnostics.ProcessStartInfo(path) { UseShellExecute = true });
            }
            catch (Exception ex) { Log($"打开路径失败 {path}: {ex.Message}"); }
        }

        /// <summary>
        /// 在资源管理器里定位到某个文件（把它选中）。走 `explorer.exe /select,"完整路径"`：
        /// 开的是**所在文件夹**，全程不查文件关联 —— 与 OpenInExplorer(文件) 的分别就在这儿。
        /// `.log` 这类扩展名在本机可能压根没有可用的打开方式（UserChoice 指向一个已被移除的
        /// AppX），ShellExecute 于是落到系统的「选取应用」对话框，落点由 shell 决定、不由我们决定；
        /// 面板上「打开日志位置」那颗按钮要的是恒定行为，所以不能用那条通路（spec §5 写的也是日志目录）。
        /// 参数形态本机实测过：`/select,"路径"` 与 `/select,路径` 都能开对文件夹并选中它，
        /// 而把整条参数再包一层引号（`"/select,路径"`）会退化成打开「文档」—— 不走那种拼法。
        /// 路径不存在时退回定位它的父目录（`/select` 对不存在的路径会静默打开默认文件夹，
        /// 那是「看起来成功了」的最坏形态），父目录也没有才记失败。
        /// </summary>
        internal static void RevealInExplorer(string path)
        {
            try
            {
                var full = Path.GetFullPath(path);
                if (!File.Exists(full) && !Directory.Exists(full))
                {
                    var parent = Path.GetDirectoryName(full);
                    if (string.IsNullOrEmpty(parent) || !Directory.Exists(parent))
                    {
                        Log($"定位路径失败 {full}: 文件与所在目录都不存在");
                        return;
                    }
                    full = parent;
                }
                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
                {
                    FileName = "explorer.exe",
                    Arguments = "/select,\"" + full + "\"",
                    UseShellExecute = false,
                });
            }
            catch (Exception ex) { Log($"定位路径失败 {path}: {ex.Message}"); }
        }

        static void RequestPanel(string page)
        {
            try { File.WriteAllText(PanelRequestFile, page ?? ""); }
            catch (Exception ex) { Log($"转交面板请求失败: {ex.Message}"); }
        }

        /// <summary>
        /// 打开/唤起面板。返回 false 表示没开成，调用方据此决定要不要消费请求文件。
        /// 这里必须自带 try：bar/ 里没有 DispatcherUnhandledException、Main 也没有外层 try，
        /// 而 Task 3 之后这条路径第一次真的会 `new PanelWindow()`（XAML 加载、资源解析、
        /// 首帧布局都可能抛）。让它逃出去的后果不是「面板打不开」，而是「常驻状态栏进程
        /// 当场死亡、状态栏和托盘一起没」。三个调用点（watchdog 委托、--panel 启动、
        /// 托盘菜单）都吃这个兜底。
        /// </summary>
        static bool OpenPanel(string page)
        {
            try
            {
                if (_panel == null) _panel = new PanelWindow();
                _panel.ShowOn(page);
                return true;
            }
            catch (Exception ex)
            {
                Log($"打开面板失败: {ex.GetType().Name} {ex.Message}");
                return false;
            }
        }

        static PanelWindow _panel;

        /// <summary>跑一次引擎拿真实快照，只给 --panel-shot 用（常驻路径走 StateClient）。</summary>
        static void LoadOneShotSnapshot()
        {
            try
            {
                string engine = Path.Combine(AppContext.BaseDirectory, "dsh_state.py");
                if (!File.Exists(engine))
                    engine = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "dsh_state.py"));
                string python = ResolvePython();
                if (python == null || !File.Exists(engine)) return;
                var psi = new System.Diagnostics.ProcessStartInfo
                {
                    FileName = python,
                    Arguments = $"-X utf8 \"{engine}\" --json --no-balance",
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    StandardOutputEncoding = new UTF8Encoding(false),
                };
                using var proc = System.Diagnostics.Process.Start(psi);
                // ReadLine() 没有超时：python 起不来、或者卡在网络请求上不肯写 stdout 时，
                // --panel-shot 会在这里永挂，冒烟侧只能等到 subprocess 的 120 秒超时抛
                // TimeoutExpired —— 失败表现是「整轮崩」而不是「一条 ✗」。
                // 并发读 + 20 秒等：读不到第一行就杀掉引擎子进程树，让 shot 带着空快照继续。
                var read = System.Threading.Tasks.Task.Run(() => proc.StandardOutput.ReadLine());
                if (!read.Wait(20000))
                {
                    try { proc.Kill(true); } catch { }
                    Log("shot 取快照超时（20 秒没读到 stdout），已终止引擎子进程");
                    return;
                }
                string line = read.Result;
                proc.WaitForExit(5000);
                if (string.IsNullOrWhiteSpace(line)) return;
                _last = System.Text.Json.JsonSerializer.Deserialize<Snapshot>(line,
                    new System.Text.Json.JsonSerializerOptions { PropertyNameCaseInsensitive = true });
            }
            catch (Exception ex)
            {
                Log($"shot 取快照失败: {ex.Message}");
            }
        }

        /// <summary>
        /// 把 WPF UI 的资源字典挂进应用资源，并按 Config.Theme 定深浅。
        /// 本项目没有 App.xaml（入口是纯代码的 [STAThread] Main），而 ApplicationThemeManager.Apply
        /// 的机制就是换 Application.Resources.MergedDictionaries 里那两份字典 ——
        /// 那里什么都没有时 Apply 是空操作：ToggleSwitch 退化成系统 ToggleButton 的灰条、
        /// NumberBox 是裸文本框、InfoBar 干脆不画。所以必须先把 ControlsDictionary 和
        /// ThemesDictionary 加进去，Apply 才有东西可换。
        /// 必须在创建任何窗口之前调用。
        /// </summary>
        static void ApplyFluentTheme()
        {
            var app = System.Windows.Application.Current;
            if (app == null) return;
            var merged = app.Resources.MergedDictionaries;
            bool hasControls = false, hasThemes = false;
            foreach (var d in merged)
            {
                if (d is Wpf.Ui.Markup.ControlsDictionary) hasControls = true;
                else if (d is Wpf.Ui.Markup.ThemesDictionary) hasThemes = true;
            }
            // 顺序有讲究：ThemesDictionary.Theme 只有 setter，且只在「已挂到 Application 上」
            // 时才真的切字典，所以先 Add 再交给 Apply 定主题。
            if (!hasControls) merged.Add(new Wpf.Ui.Markup.ControlsDictionary());
            if (!hasThemes) merged.Add(new Wpf.Ui.Markup.ThemesDictionary());
            // 深浅的落点收敛到 ApplyTheme() 这一处（Main / shot 两条路径都经过这里）：
            // 删掉这行调用，--panel-shot appearance 的深浅两张取样会重新变得一模一样，
            // 冒烟那条「主题真的生效」门禁当场翻红 —— 补上 Task 3 复核 N3 的缺口。
            ApplyTheme();
        }

        /// <summary>settings.json 的 theme 是 light/dark/system；system 按当前系统主题就地解析。</summary>
        static Wpf.Ui.Appearance.ApplicationTheme FluentTheme()
        {
            string t = _settings?.Theme ?? "light";
            if (t == "dark") return Wpf.Ui.Appearance.ApplicationTheme.Dark;
            if (t == "system"
                && Wpf.Ui.Appearance.ApplicationThemeManager.GetSystemTheme() == Wpf.Ui.Appearance.SystemTheme.Dark)
                return Wpf.Ui.Appearance.ApplicationTheme.Dark;
            return Wpf.Ui.Appearance.ApplicationTheme.Light;
        }

        /// <summary>离屏渲染某一页为 PNG 并打印统计，供冒烟做像素门禁。</summary>
        static int RenderShot(string pageKey, string outPath)
        {
            try
            {
                // shot 模式在单实例检查之前就 return 了，_settings 还没加载；
                // 而每一页构造时都要读 App.Config，不先加载就是空引用。
                _settings = Settings.Load(SettingsFile);
                // 离屏出图也要喂真实快照，否则概览页永远是空表，Task 8 的门禁无从判断。
                LoadOneShotSnapshot();
                var app = new System.Windows.Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
                ApplyFluentTheme();
                if (!PanelPages.TryCreate(pageKey, out FrameworkElement root))
                {
                    // 非法页名明确报错并非零退出。以前 default 把它静默当 overview，
                    // `--panel-shot nonsense` 回 `SHOT nonsense 900 620 7` 还退 0，
                    // 冒烟只比 parts[1] == page 的回声，看不出渲染其实跑偏去了别页。
                    Console.Error.WriteLine(
                        $"SHOT-FAIL 未知页面 \"{pageKey}\"，可用页：{string.Join(", ", PanelPages.Keys)}");
                    Console.Error.Flush();
                    return 2;
                }
                double w = 900, h = 620;
                // 页面是在 Refresh 里读快照填内容的（Task 8 的会话表就照这个契约写）。
                // 排版之前不把快照交进去，占位页也许看不出来，但填实以后 --panel-shot
                // 渲染的永远是空表，spec §9「概览页色数显著高于关于页」那条门禁无从判定。
                if (root is IPanelPage pp) pp.Refresh(_last);
                // 必须全限定：本命名空间下有 DshBar.Rect（Win32 用的那个），App.cs 又同时
                // using 了 System.Drawing 与 System.Windows，裸写 Size / Rect 会撞 CS0104 或绑到 DshBar.Rect。
                root.Measure(new System.Windows.Size(w, h));
                root.Arrange(new System.Windows.Rect(0, 0, w, h));
                root.UpdateLayout();
                var rtb = new RenderTargetBitmap((int)w, (int)h, 96, 96, PixelFormats.Pbgra32);
                rtb.Render(root);
                var pixels = new byte[(int)w * (int)h * 4];
                rtb.CopyPixels(pixels, (int)w * 4, 0);
                var seen = new HashSet<uint>();
                // 统计键必须带上 alpha：主题刷几乎都是「纯黑/纯白 + alpha」
                // （Light 的 TextFillColorPrimaryBrush 就是 #E4000000），
                // PBGRA32 是预乘的，丢掉 alpha 之后它的 RGB 和「未绘制的透明像素」同为 0x000000，
                // 整页会被数成 1 色 —— 色数门禁当场失效还看不出来。
                for (int i = 0; i + 3 < pixels.Length; i += 4)
                    seen.Add((uint)(pixels[i + 3] << 24 | pixels[i] << 16 | pixels[i + 1] << 8 | pixels[i + 2]));
                if (!string.IsNullOrEmpty(outPath) && outPath != "-")
                {
                    var enc = new PngBitmapEncoder();
                    enc.Frames.Add(BitmapFrame.Create(rtb));
                    using var fs = File.Create(outPath);
                    enc.Save(fs);
                }
                Console.Out.WriteLine($"SHOT {pageKey} {(int)w} {(int)h} {seen.Count}");
                Console.Out.Flush();
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"SHOT-FAIL {ex.GetType().Name}: {ex.Message}");
                return 1;
            }
        }

        private static void StartClient()
        {
            string engine = Path.Combine(AppContext.BaseDirectory, "dsh_state.py");
            if (!File.Exists(engine))
            {
                engine = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "dsh_state.py"));
            }
            string python = ResolvePython();
            if (python == null || !File.Exists(engine))
            {
                Log($"找不到引擎或 python engine={engine} python={python ?? "null"}");
                Balloon("状态检测引擎不可用", "请确认 dsh_state.py 与 python 3.14 就位", ToolTipIcon.Error);
                return;
            }
            _client = new StateClient(python, engine, _settings.Interval);
            _client.Log += Log;
            _client.Updated += snap => _window.Dispatcher.BeginInvoke(new Action(() => OnSnapshot(snap)));
            _client.Start();
        }

        /// <summary>--demo 只喂一帧伪造快照，用来肉眼核对各状态的视觉。</summary>
        private static void DemoOnce(string state)
        {
            string engine = Path.Combine(AppContext.BaseDirectory, "dsh_state.py");
            string python = ResolvePython();
            if (python == null || !File.Exists(engine)) return;
            try
            {
                var psi = new System.Diagnostics.ProcessStartInfo
                {
                    FileName = python,
                    Arguments = $"-X utf8 \"{engine}\" --demo {state} --json",
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    StandardOutputEncoding = new UTF8Encoding(false),
                };
                using var proc = System.Diagnostics.Process.Start(psi);
                string line = proc.StandardOutput.ReadLine();
                proc.WaitForExit(15000);
                if (string.IsNullOrWhiteSpace(line)) return;
                var snap = System.Text.Json.JsonSerializer.Deserialize<Snapshot>(line,
                    new System.Text.Json.JsonSerializerOptions { PropertyNameCaseInsensitive = true });
                if (snap != null) _window.Dispatcher.BeginInvoke(new Action(() => OnSnapshot(snap)));
            }
            catch (Exception ex)
            {
                Log($"demo 失败: {ex.Message}");
            }
        }

        private static string ResolvePython()
        {
            foreach (var name in new[] { "python", "python3" })
            {
                foreach (var dir in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
                {
                    if (dir.Length == 0) continue;
                    try
                    {
                        string candidate = Path.Combine(dir.Trim(), name + ".exe");
                        if (File.Exists(candidate)) return candidate;
                    }
                    catch
                    {
                    }
                }
            }
            var fallback = Path.Combine(@"C:\Python314", "python.exe");
            return File.Exists(fallback) ? fallback : null;
        }

        private static void DockNow(IntPtr hwnd)
        {
            _taskbar = Native.TaskbarHandle();
            if (_taskbar != IntPtr.Zero) Native.Dock(hwnd, _taskbar);
            PlaceNow(hwnd);
        }

        private static void PlaceNow(IntPtr hwnd)
        {
            // showBar=false 时不再落位：Undock 之后窗口是脱离任务栏的隐藏 popup，
            // 每 2 秒的 watchdog 若继续 PlaceNow，SetWindowPos(SWP_SHOWWINDOW) 会把它重新点亮。
            if (_settings != null && !_settings.ShowBar) return;
            if (_taskbar == IntPtr.Zero) return;
            if (!Native.GetWindowRect(_taskbar, out Rect band)) return;
            var (left, right) = Native.FreeRange(band, 240);
            int height = Math.Max(20, band.Height - 8);
            int max = Math.Max(200, right - left);

            double scale = 1.0;
            try
            {
                scale = VisualTreeHelper.GetDpi(_window).DpiScaleX;
            }
            catch
            {
            }
            int wanted = Math.Max(200, (int)Math.Ceiling(_window.ContentWidth() * scale));
            int target = _window.Expanded ? Math.Min(max, wanted + (int)(200 * scale)) : Math.Min(max, wanted);
            if (_curW <= 0) _curW = target;
            _targetW = target;

            int width = Math.Max(200, Math.Min(_curW, max));
            int top = band.Top + (band.Height - height) / 2;
            var rect = new Rect { Left = left, Top = top, Right = left + width, Bottom = top + height };
            Native.Place(hwnd, _taskbar, rect);
            _barRect = rect;
        }

        private static void PollHover()
        {
            if (!Native.GetCursorPos(out Point cur)) { Log("GetCursorPos 失败"); return; }
            bool inside = cur.X >= _barRect.Left && cur.X < _barRect.Right && cur.Y >= _barRect.Top && cur.Y < _barRect.Bottom;
            if (inside)
            {
                _window.SetHover(true);
                _hoverOut = DateTime.Now.AddMilliseconds(260);
            }
            else if (DateTime.Now > _hoverOut)
            {
                _window.SetHover(false);
            }
        }

        private static void Tween()
        {
            if (_curW <= 0 || Math.Abs(_curW - _targetW) <= 2)
            {
                if (_curW != _targetW)
                {
                    _curW = _targetW;
                    PlaceNow(new System.Windows.Interop.WindowInteropHelper(_window).Handle);
                }
                return;
            }
            int step = Math.Max(6, (int)(Math.Abs(_targetW - _curW) * 0.35));
            _curW = _targetW > _curW ? Math.Min(_targetW, _curW + step) : Math.Max(_targetW, _curW - step);
            PlaceNow(new System.Windows.Interop.WindowInteropHelper(_window).Handle);
        }

        private static void Tick(IntPtr hwnd)
        {
            try
            {
                IntPtr tb = Native.TaskbarHandle();
                if (tb == IntPtr.Zero) return;
                // 隐藏状态条期间不做任何重停靠：简报在 PlaceNow 之前加的是整段 early-return，
                // 那会把后面的 panel.request 消费一起吞掉 —— 「隐藏后仍可通过唤起回到面板」
                // （托盘菜单「打开面板」与二次实例 --panel 走的是同一条 OpenPanel）就假了。
                // 落位由 PlaceNow 自己的 showBar 守卫挡；这里只补重停靠这一半。
                if (tb != _taskbar && _settings.ShowBar)
                {
                    Log("任务栏窗口已重建，重新停靠");
                    _taskbar = tb;
                    Native.Dock(hwnd, _taskbar);
                }
                PlaceNow(hwnd);
                _tray.Text = TooltipFor(_last);

                // 第二个实例只写一个请求文件就退出，由这里（已在 UI 线程的消息循环里）唤起面板。
                // 读失败就不唤起；开窗失败也不删文件 —— 请求留给下一轮重试，别把用户的点击吞掉。
                // 重试按 PanelOpenTries 收敛、删除只删「刚打开的那一条」，理由都写在 TryConsumePanelRequest 上。
                var req = PanelRequestFile;
                if (File.Exists(req) && !_reqQueued)
                {
                    string page = null;
                    try { page = File.ReadAllText(req).Trim(); }
                    catch (Exception ex) { Log($"读取面板请求失败，下一轮重试: {ex.Message}"); }
                    if (page != null) TryConsumePanelRequest(req, page);
                }
            }
            catch (Exception ex)
            {
                Log($"watchdog 异常: {ex.Message}");
            }
        }

        /// <summary>
        /// 面板请求的消费状态机。请求身份 = 文件内容 + 写入时间 + 长度，所以重试预算是按
        /// 「这一条请求」算的：用户再点一次会写一个新时间戳，预算随之重置，不会被上一条顶掉。
        /// 收敛条件（三条，各自兜一种病）：
        ///   开窗失败 → 文件保留，同一请求最多试 PanelOpenTries=2 次；预算用完就记一条日志
        ///     并把文件删掉。之所以不是「永远留着」：面板构造失败通常是确定性的（XAML/资源/
        ///     首帧布局），无限重试只是每 2 秒白抢一次焦点；而永久留在磁盘上的请求会让每次
        ///     启动状态栏都重放一遍同一场失败。2 次足够覆盖瞬时故障（例如首帧布局撞上的
        ///     一次性资源紧张），第 2 次仍失败就该认输、记账、让用户再点一次。
        ///   开窗成功但删除失败 → 请求其实已经满足了，直接标记放弃，之后每轮只安静地试着删，
        ///     删得掉就清掉残留并记一行；绝不再次开窗，否则就是每 2 秒把面板弹到前台。
        ///   开窗成功、但文件已经被新请求覆盖了 → 不删（见 SameRequest）。读请求和删请求之间
        ///     隔着一次 dispatcher 跳跃和整个 PanelWindow 冷构造首帧（本机实测 185~245ms），
        ///     这段时间第二个实例写进来的新请求要是被无条件删掉，就是一次彻底的静默丢失：
        ///     面板停在第一页、日志一行没有、那条点击再也不会回来。
        /// </summary>
        private static void TryConsumePanelRequest(string req, string page)
        {
            string stamp = page + "|" + RequestStamp(req);
            if (stamp != _reqStamp)
            {
                _reqStamp = stamp;
                _reqTries = 0;
                _reqGivenUp = false;
            }
            if (_reqGivenUp)
            {
                if (TryDeleteRequest(req)) Log("此前删不掉的面板请求文件已清理");
                return;
            }
            if (_reqTries >= PanelOpenTries)
            {
                Log($"面板请求连续 {_reqTries} 次没能打开面板，放弃这条请求（再点一次会写新请求）");
                _reqGivenUp = true;
                TryDeleteRequest(req);
                return;
            }
            _reqTries++;
            _reqQueued = true;
            string target = string.IsNullOrEmpty(page) ? "overview" : page;
            // 走 OpenPanel 而不是 PanelWindow.Instance?.ShowOn：常驻实例通常是普通
            // DshBar.exe 起的，面板压根没创建过，Instance 一直是 null，
            // 那时 ShowOn 的调用会被 ?. 静默吃掉 —— 请求删了、窗口却没开。
            // 开窗排到下一个 dispatcher 轮，本轮 Tick 的 try 罩不到委托体，
            // 所以委托体必须自带 try（N1）：面板打不开只该是打不开，不该带走状态栏。
            _window.Dispatcher.BeginInvoke(new Action(() =>
            {
                try
                {
                    if (!OpenPanel(target)) return;   // 异常已在 OpenPanel 里记录，文件留给下一轮
                    // 删之前复核身份：此刻文件里躺着的，可能已经是刚才这段冷构造期间
                    // 另一个实例写进来的新请求了。不是刚打开的那一条就别删，留着让
                    // 下一轮（2 秒内）按新身份重新开一次窗 —— 删了就等于把那次点击吞了。
                    if (!SameRequest(req, stamp))
                    {
                        Log($"面板已打开，但请求文件在此期间被新请求覆盖，不删除，交给下一轮消费: {req}");
                        return;
                    }
                    if (!TryDeleteRequest(req))
                    {
                        Log($"面板已打开但请求文件删不掉，之后只清理不再重开: {req}");
                        _reqGivenUp = true;
                    }
                }
                catch (Exception ex)
                {
                    Log($"面板请求开窗委托异常: {ex.GetType().Name} {ex.Message}");
                }
                finally
                {
                    _reqQueued = false;
                }
            }));
        }

        /// <summary>
        /// 文件里现在这条请求，还是刚刚打开面板时读到的那一条吗？
        /// 按同样的口径重算一次身份（内容 + 写入时间 + 长度）。读不到就答 false：
        /// 宁可多留一轮让下一条重开一次窗，也不能把没打开过的请求删掉。
        /// </summary>
        private static bool SameRequest(string req, string stamp)
        {
            try
            {
                return string.Equals(File.ReadAllText(req).Trim() + "|" + RequestStamp(req), stamp,
                    StringComparison.Ordinal);
            }
            catch
            {
                return false;
            }
        }

        /// <summary>请求身份的一部分。元数据读不到就返回 "?"，宁可少一次重试也不丢请求。</summary>
        private static string RequestStamp(string req)
        {
            try
            {
                var info = new FileInfo(req);
                return info.LastWriteTimeUtc.Ticks.ToString("x") + "|" + info.Length;
            }
            catch
            {
                return "?";
            }
        }

        /// <summary>删请求文件。成功时顺手把身份清空，让下一条同内容请求也能重新计预算。</summary>
        private static bool TryDeleteRequest(string req)
        {
            try
            {
                File.Delete(req);
                _reqStamp = "";
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static string TooltipFor(Snapshot snap)
        {
            if (snap == null) return "dsh 状态检测";
            string tip = (snap.Tooltip ?? snap.Label ?? "").Replace('\n', ' ');
            tip = System.Text.RegularExpressions.Regex.Replace(tip, @"\s+", " ").Trim();
            return tip.Length <= 63 ? tip : tip.Substring(0, 62) + "…";
        }

        private static void OnSnapshot(Snapshot snap)
        {
            if (snap == null) return;
            if (!snap.Ok)
            {
                Log($"引擎报错: {snap.Error}");
                return;
            }
            _last = snap;
            _window.Apply(snap);
            _tray.Icon = StatusIcon(snap.Color, snap.Glyph);
            _tray.Text = TooltipFor(snap);
            _miState.Text = "状态：" + snap.Label;
            _miProject.Text = "项目：" + (snap.Session?.Project ?? "-");
            _miBalance.Text = "余额：" + (snap.StripRight ?? "--");
            string err = snap.Balance != null && !snap.Balance.Available ? snap.Balance.Error : "";
            _miBalanceErr.Text = string.IsNullOrEmpty(err) ? "" : "（" + err + "）";
            _miBalanceErr.Visible = !string.IsNullOrEmpty(err);
            _miTips.DropDownItems.Clear();
            foreach (var line in snap.TipLines ?? new List<string>())
            {
                _miTips.DropDownItems.Add(new ToolStripMenuItem(line) { Enabled = false });
            }
            // 面板开着的话让它自己拉新快照。不在这里发的话 SnapshotChanged 就是个从不触发的事件
            // （CS0067，本仓库门禁是 0 警告），Task 4 之后每页也就没有数据驱动的重绘入口。
            SnapshotChanged?.Invoke();
            Notify(snap);
        }

        private static void Notify(Snapshot snap)
        {
            string state = snap.State ?? "";
            string prev = _lastState;
            var now = DateTime.Now;
            string project = snap.Session?.Project ?? "";
            string pending = snap.Session?.Pending?.Text ?? "";

            if (state != prev)
            {
                switch (state)
                {
                    case "needs_action":
                        Balloon("DeepSeek 在等你操作", project + "\n" + pending, ToolTipIcon.Warning);
                        break;
                    case "error":
                        Balloon("DeepSeek 出错了", project + "\n" + (snap.Session?.Error ?? ""), ToolTipIcon.Error);
                        break;
                    case "done":
                        if (prev == "thinking" || prev == "answering" || prev == "tool_running" || prev == "needs_action" || prev == "stalled")
                        {
                            Balloon("回答完成", project, ToolTipIcon.Info);
                        }
                        break;
                }
                _lastNotifyAt = now;
            }
            else if (state == "needs_action" && _settings.RepeatSec > 0
                     && (now - _lastNotifyAt).TotalSeconds >= _settings.RepeatSec)
            {
                Balloon("还在等你操作", project + "\n" + pending, ToolTipIcon.Warning);
                _lastNotifyAt = now;
            }
            _lastState = state;
        }

        private static void Balloon(string title, string text, ToolTipIcon icon)
        {
            if (!_settings.Notify || _tray == null) return;
            _tray.BalloonTipTitle = title;
            _tray.BalloonTipText = string.IsNullOrEmpty(text) ? " " : (text.Length > 240 ? text.Substring(0, 240) : text);
            _tray.BalloonTipIcon = icon;
            _tray.ShowBalloonTip(12000);
            Log($"通知 [{icon}] {title} / {(text ?? "").Replace('\n', ' ')}");
        }

        private static void Cycle(int direction)
        {
            var snap = _last;
            if (snap == null || direction == 0) return;
            var items = new List<string> { snap.Label + " · " + (snap.Session?.Project ?? "") };
            foreach (var w in snap.Waiting ?? new List<Brief>())
            {
                items.Add($"{w.Project}：{w.Text}");
            }
            foreach (var r in snap.Recent ?? new List<Brief>())
            {
                if (r.Project != snap.Session?.Project) items.Add($"{r.Project} · {r.State}");
            }
            if (items.Count < 2) return;
            _cycleIndex = ((_cycleIndex + direction) % items.Count + items.Count) % items.Count;
            _window.ShowTransient(items[_cycleIndex]);
        }

        private static int _cycleIndex = -1;

        private static void FocusHarness()
        {
            foreach (var p in System.Diagnostics.Process.GetProcessesByName("DeepSeek Harness"))
            {
                if (p.MainWindowHandle == IntPtr.Zero) continue;
                Native.ShowWindowAsync(p.MainWindowHandle, 9);
                Native.SetForegroundWindow(p.MainWindowHandle);
                return;
            }
            Balloon("DeepSeek Harness 没有可激活的窗口", "", ToolTipIcon.Info);
        }

        private static Icon StatusIcon(string hex, string glyph)
        {
            string key = (hex ?? "") + "|" + (glyph ?? "");
            if (IconCache.TryGetValue(key, out Icon hit) && hit != null) return hit;

            var color = System.Drawing.Color.Gray;
            try
            {
                var c = (System.Windows.Media.Color)System.Windows.Media.ColorConverter.ConvertFromString(hex);
                color = System.Drawing.Color.FromArgb(c.A, c.R, c.G, c.B);
            }
            catch
            {
            }

            var bmp = new Bitmap(32, 32);
            using (var g = Graphics.FromImage(bmp))
            {
                g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
                g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAliasGridFit;
                g.Clear(System.Drawing.Color.Transparent);
                using var brush = new SolidBrush(color);
                g.FillEllipse(brush, 1, 1, 30, 30);
                using var font = new Font("Segoe UI", 15, System.Drawing.FontStyle.Bold, GraphicsUnit.Pixel);
                using var text = new SolidBrush(System.Drawing.Color.White);
                var sf = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center };
                g.DrawString(string.IsNullOrEmpty(glyph) ? "?" : glyph, font, text, new RectangleF(0, 0, 32, 32), sf);
            }
            IntPtr hicon = bmp.GetHicon();
            bmp.Dispose();
            var icon = Icon.FromHandle(hicon);
            IconCache[key] = icon;
            return icon;
        }

        private static void OpenLog()
        {
            try
            {
                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(LogFile) { UseShellExecute = true });
            }
            catch
            {
            }
        }

        private static void BuildTray()
        {
            var menu = new ContextMenuStrip { Font = new Font("Microsoft YaHei UI", 9f) };
            Add(menu, "打开面板", (s, e) => OpenPanel("overview"));
            menu.Items.Add(new ToolStripSeparator());
            _miState = Add(menu, "状态：检测中…", null, false);
            _miProject = Add(menu, "项目：-", null, false);
            _miBalance = Add(menu, "余额：-", null, false);
            _miBalanceErr = Add(menu, "", null, false);
            _miBalanceErr.Visible = false;
            _miTips = Add(menu, "详情", null, false);
            menu.Items.Add(new ToolStripSeparator());
            Add(menu, "立即刷新余额", (s, e) => RefreshBalance());
            Add(menu, "设置余额 Key…", (s, e) => KeyDialog.Ask());
            Add(menu, "切到 DeepSeek Harness", (s, e) => FocusHarness());
            Add(menu, "重启检测进程", (s, e) => RestartClient());
            _miAuto = Add(menu, "开机自动启动", (s, e) => ToggleAutostart(!_miAuto.Checked));
            _miAuto.CheckOnClick = false;
            Add(menu, "打开日志", (s, e) => OpenLog());
            menu.Items.Add(new ToolStripSeparator());
            Add(menu, "退出", (s, e) => System.Windows.Application.Current?.Shutdown());

            _tray = new NotifyIcon
            {
                Icon = StatusIcon("#9CA3AF", "?"),
                Text = "dsh 状态检测",
                Visible = true,
                ContextMenuStrip = menu,
            };
            _tray.DoubleClick += (s, e) => FocusHarness();
            _miAuto.Checked = AutostartEnabled();
        }

        private static ToolStripMenuItem Add(ToolStripDropDown menu, string text, EventHandler handler, bool enabled = true)
        {
            var item = new ToolStripMenuItem(text) { Enabled = enabled };
            if (handler != null) item.Click += handler;
            menu.Items.Add(item);
            return item;
        }

        private static void RefreshBalance()
        {
            try
            {
                File.WriteAllText(RefreshToken, DateTime.Now.ToString("o", CultureInfo.InvariantCulture));
                Log("已请求刷新余额");
            }
            catch (Exception ex)
            {
                Log($"刷新余额失败: {ex.Message}");
            }
        }

        private static void RestartClient()
        {
            _client?.Dispose();
            _client = null;
            StartClient();
        }

        private static bool AutostartEnabled()
        {
            try
            {
                using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(RunKey);
                return key?.GetValue(RunValue) != null;
            }
            catch
            {
                return false;
            }
        }

        private static void ToggleAutostart(bool on)
        {
            try
            {
                using var key = Microsoft.Win32.Registry.CurrentUser.CreateSubKey(RunKey);
                if (on)
                {
                    key.SetValue(RunValue, $"\"{Environment.ProcessPath}\"");
                }
                else
                {
                    key.DeleteValue(RunValue, false);
                }
                _miAuto.Checked = on;
                Log($"开机自启 -> {on}");
            }
            catch (Exception ex)
            {
                Balloon("设置开机自启失败", ex.Message, ToolTipIcon.Error);
            }
        }

        private static void Log(string message)
        {
            try
            {
                File.AppendAllText(LogFile,
                    $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {message}{Environment.NewLine}", Encoding.UTF8);
                var info = new FileInfo(LogFile);
                if (info.Length > 262144)
                {
                    var lines = File.ReadAllLines(LogFile);
                    int take = Math.Min(200, lines.Length);
                    var keep = new string[take];
                    Array.Copy(lines, lines.Length - take, keep, 0, take);
                    File.WriteAllLines(LogFile, keep, Encoding.UTF8);
                }
            }
            catch
            {
            }
        }

        private static void Cleanup()
        {
            try
            {
                _watchdog?.Stop();
                _tween?.Stop();
                _client?.Dispose();
                var hwnd = new System.Windows.Interop.WindowInteropHelper(_window).Handle;
                Native.Undock(hwnd);
                if (_tray != null)
                {
                    _tray.Visible = false;
                    _tray.Dispose();
                }
                try { _panel?.ForceClose(); } catch { }
                _window?.Close();
                foreach (var kv in IconCache)
                {
                    Native.DestroyIcon(kv.Value.Handle);
                    kv.Value.Dispose();
                }
                IconCache.Clear();
                _settings?.Save(SettingsFile);
                _mutex.ReleaseMutex();
                _mutex.Dispose();
                Log("DshBar 退出");
            }
            catch
            {
            }
        }
    }
}
