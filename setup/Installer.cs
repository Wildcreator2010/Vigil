using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using Microsoft.Win32;

namespace Vigil.Setup
{
    /// <summary>
    /// 安装/卸载的全部动作。不含 UI，可被静默模式直接驱动（冒烟的门禁走的就是这条）。
    ///
    /// 两条设计约束贯穿全文，改代码前先读：
    ///  ① 一切写入都在当前用户范围内（%LOCALAPPDATA% 与 HKCU），所以 manifest 是 asInvoker、
    ///     永不弹 UAC。代价是"给这台机器所有用户装"做不到，那是另一个产品决定。
    ///  ② net48 没有 Environment.ProcessId、也没有 Process.Kill(bool entireProcessTree)，
    ///     用了就是编译错误。别照着 .NET 10 的习惯往这里抄。
    /// </summary>
    internal static class Installer
    {
        // 下面这几个字面量是 bar/App.cs 里那些常量的**第二份拷贝**：net48 向导引用不到主程序
        // 程序集，只能抄。抄的东西一定会漂，所以 smoke_test.py 的 check_setup_contract()
        // 钉住了必须两边同时改——漂了的代价是面板「开机自动启动」勾态变假，
        // 或者卸完载 Run 值还留着、下次开机弹「找不到 Vigil.exe」。
        internal const string ProductName = "Vigil";
        internal const string RunKeyPath = @"Software\Microsoft\Windows\CurrentVersion\Run";
        internal const string RunKeyValueName = "Vigil";
        internal const string UninstallKeyPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\Vigil";
        internal const string SetupExeName = "Vigil-Setup.exe";
        internal const string MainExeName = "Vigil.exe";
        internal const string PayloadFolderName = "app";

        public sealed class Options
        {
            public string TargetDir;
            public string PayloadDir;      // 永远是 <向导所在目录>\app，不给命令行覆盖
            public bool Autostart = true;
            public bool StartMenuShortcut = true;
            public bool DesktopShortcut;
            public bool LaunchAfter = true;
            public bool PurgeData;         // 仅卸载用：删数据是 opt-in，见 Program.cs 的注释
            public Action<string> Log = _ => { };
        }

        public static string DefaultTargetDir() => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Programs", ProductName);

        public static string PayloadDirOf(string setupDir) => Path.Combine(setupDir, PayloadFolderName);

        /// <summary>版本取向导自己的 AssemblyVersion：package.cmd 用同一个 -p:Version 喂两个工程。</summary>
        public static string Version()
        {
            var v = typeof(Installer).Assembly.GetName().Version;
            return v == null ? "0.0.0" : v.Major + "." + v.Minor + "." + v.Build;
        }

        public static bool IsInstalled(string target) =>
            File.Exists(Path.Combine(target, MainExeName));

        /// <summary>装好的那份是什么版本。读不到就当没有（返回 null 让界面退回「安装」）。</summary>
        public static string InstalledVersion(string target)
        {
            var exe = Path.Combine(target, MainExeName);
            if (!File.Exists(exe)) return null;
            try
            {
                var parts = (FileVersionInfo.GetVersionInfo(exe).FileVersion ?? "").Split('.');
                return parts.Length >= 3 ? parts[0] + "." + parts[1] + "." + parts[2] : null;
            }
            catch { return null; }
        }

        public static string AppDir() => AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');

        // ---------------------------------------------------------------- 安装

        public static bool Install(Options o)
        {
            try
            {
                GuardNotInsideTarget(o);
                var payload = Path.GetFullPath(o.PayloadDir);
                if (!File.Exists(Path.Combine(payload, MainExeName)))
                {
                    o.Log("✗ 载荷目录里没有 " + MainExeName + "：" + payload);
                    return false;
                }
                EnsureFreeSpace(o.TargetDir, payload, o.Log);
                KillRunning(o.Log);

                // 先整体复制到 <target>.new，全部成功后再换名（spec §7）：中途崩最多留下
                // .new 垃圾，绝不会留下半个打不开的 Vigil。
                Sweep(o.TargetDir, o.Log);
                var stage = o.TargetDir + ".new";
                CopyTree(payload, stage, o.Log);
                File.Copy(SelfPath(), Path.Combine(stage, SetupExeName), true);
                o.Log("卸载器已放进安装目录（卸载要靠它，见 DeleteWithTempGuard）");
                Swap(stage, o.TargetDir, o.Log);

                if (o.StartMenuShortcut && !MakeShortcut(StartMenuPath(), o.TargetDir, o.Log)) return false;
                if (o.DesktopShortcut && !MakeShortcut(DesktopPath(), o.TargetDir, o.Log)) return false;
                if (o.Autostart && !SetRunValue(o.TargetDir, true, o.Log)) return false;
                if (!WriteUninstallKey(o.TargetDir, o.Log)) return false;

                o.Log("✓ 已安装 " + Version() + " 到 " + o.TargetDir);
                if (o.LaunchAfter) Launch(o.TargetDir, o.Log);
                return true;
            }
            catch (Exception ex)
            {
                o.Log("✗ 安装异常：" + ex.GetType().Name + ": " + ex.Message);
                return false;
            }
        }

        /// <summary>向导不能装进它自己待的地方：从安装目录内部跑安装会覆盖正在跑的文件。</summary>
        static void GuardNotInsideTarget(Options o)
        {
            // 两边都先还原成长路径，理由见 LongPath()：短/长混着比会漏判。
            string here = LongPath(AppDir());
            var target = LongPath(Path.GetFullPath(o.TargetDir)).TrimEnd('\\');
            if (here.StartsWith(target + "\\", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(here, target, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("请从解压出来的目录运行安装，不要从安装目录里运行它");
        }

        static void EnsureFreeSpace(string target, string payload, Action<string> log)
        {
            // 复制要瞬时双份（.new + 旧的），再加 250MB 余量给换名与临时文件。
            long need = TreeSize(payload) * 2 + (250L << 20);
            var drive = new DriveInfo(Path.GetPathRoot(Path.GetFullPath(target)));
            if (drive.AvailableFreeSpace < need)
                throw new InvalidOperationException(drive.Name + " 只剩 "
                    + (drive.AvailableFreeSpace >> 20) + "MB，这份安装要 " + (need >> 20)
                    + "MB（复制时瞬时双份）");
        }

        static void Sweep(string target, Action<string> log)
        {
            foreach (var junk in new[] { target + ".new", target + ".old" })
                if (Directory.Exists(junk))
                {
                    log("清理上次残留 " + junk);
                    Try(() => Directory.Delete(junk, true), log);
                }
        }

        static void CopyTree(string src, string dst, Action<string> log)
        {
            src = src.TrimEnd('\\');
            Directory.CreateDirectory(dst);
            foreach (var dir in Directory.EnumerateDirectories(src, "*", SearchOption.AllDirectories))
                Directory.CreateDirectory(dst + dir.Substring(src.Length));
            int n = 0;
            foreach (var file in Directory.EnumerateFiles(src, "*", SearchOption.AllDirectories))
            {
                var to = dst + file.Substring(src.Length);
                Directory.CreateDirectory(Path.GetDirectoryName(to));
                CopyRetry(file, to, log);
                n++;
            }
            log("复制完成：" + n + " 个文件");
        }

        static void CopyRetry(string from, string to, Action<string> log)
        {
            for (int i = 1; ; i++)
            {
                try { File.Copy(from, to, true); return; }
                catch (IOException) when (i < 3)
                {
                    log("占用，300ms 后第 " + i + " 次重试：" + Path.GetFileName(to));
                    System.Threading.Thread.Sleep(300);
                }
            }
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

        // ---------------------------------------------------------------- 卸载

        public static bool Uninstall(Options o)
        {
            try
            {
                KillRunning(o.Log);
                SetRunValue(o.TargetDir, false, o.Log);
                Try(() => Registry.CurrentUser.DeleteSubKeyTree(UninstallKeyPath, false), o.Log);
                foreach (var lnk in new[] { StartMenuPath(), DesktopPath() })
                    Try(() => { if (File.Exists(lnk)) File.Delete(lnk); }, o.Log);

                var data = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), ProductName);
                if (o.PurgeData && Directory.Exists(data))
                {
                    Try(() => Directory.Delete(data, true), o.Log);
                    o.Log("已删数据目录 " + data);
                }
                else
                    o.Log("保留设置与余额 Key：" + data);

                if (Directory.Exists(o.TargetDir)) DeleteWithTempGuard(o.TargetDir, o.Log);
                o.Log("✓ 卸载完成");
                return true;
            }
            catch (Exception ex)
            {
                o.Log("✗ 卸载异常：" + ex.GetType().Name + ": " + ex.Message);
                return false;
            }
        }

        /// <summary>
        /// 卸载器就在它自己要删的目录里。做法：把自身拷到 %TEMP% 重启一份（带 /FROM-TEMP 和
        /// /PARENT=父pid），父进程立刻退出；副本等父进程真退了再删目录——父进程还活着时它自己
        /// 那份 exe 镜像是锁着的，Directory.Delete 必然失败。副本删完再给自己排一条延后自删。
        /// </summary>
        static void DeleteWithTempGuard(string target, Action<string> log)
        {
            string mine = SelfPath();
            bool inside = LongPath(mine).StartsWith(LongPath(Path.GetFullPath(target)) + @"\",
                                                   StringComparison.OrdinalIgnoreCase);
            if (!inside)
            {
                DeleteWithRetry(target, log);
                return;
            }
            int pid = Process.GetCurrentProcess().Id;      // net48 没有 Environment.ProcessId
            var tmp = Path.Combine(Path.GetTempPath(), "Vigil-Setup-" + pid + ".tmp.exe");
            File.Copy(mine, tmp, true);
            var args = string.Join(" ", SelfArgs("/FROM-TEMP /PARENT=" + pid + " /D=\"" + LongPath(target) + "\""));
            Process.Start(new ProcessStartInfo(tmp, args) { UseShellExecute = false, CreateNoWindow = true });
            log("改由临时副本继续删除目录：" + tmp);
            Environment.Exit(0);        // 父进程必须真退出，副本才删得掉这个目录
        }

        [System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode, SetLastError = true)]
        static extern int GetLongPathName(string path, System.Text.StringBuilder buffer, int maxLength);

        /// <summary>
        /// 把 8.3 短路径还原成长路径。不还原就没法判断"我是不是就住在要删的目录里"：
        /// `AppDomain.BaseDirectory` 跟着启动时的写法给短路径（`C:\Users\WILDCR~1\...`），
        /// 而 `MainModule.FileName` 一律给长路径，两边 `StartsWith` 永远不相等，
        /// 于是卸载走"直接删"，一头撞在正在运行的 exe 上（UnauthorizedAccessException，实测）。
        /// </summary>
        static string LongPath(string p)
        {
            if (string.IsNullOrEmpty(p)) return p ?? "";
            try
            {
                var sb = new System.Text.StringBuilder(4096);
                int n = GetLongPathName(p, sb, sb.Capacity);
                return n > 0 ? sb.ToString() : p;
            }
            catch { return p; }
        }

        /// <summary>父进程没退干净之前别动手：exe 镜像锁着，删了也是失败。</summary>
        public static void WaitForParent(int pid, Action<string> log)
        {
            if (pid <= 0) return;
            try
            {
                var p = Process.GetProcessById(pid);
                try { if (!p.WaitForExit(15000)) log("父进程 " + pid + " 15 秒没退，照样往下删"); }
                finally { p.Dispose(); }
            }
            catch (ArgumentException) { /* 已经退了，正好 */ }
            catch (Exception ex) { log("等父进程时异常：" + ex.Message); }
        }

        /// <summary>给临时副本自己排一条延后删除（cmd 先 ping 几秒等句柄释放）。</summary>
        public static void ScheduleSelfDelete()
        {
            string mine = SelfPath();
            if (!mine.StartsWith(Path.GetTempPath(), StringComparison.OrdinalIgnoreCase)) return;
            Process.Start(new ProcessStartInfo("cmd.exe",
                "/c ping -n 4 127.0.0.1 >nul & del /q /f \"" + mine + "\"")
            { UseShellExecute = false, CreateNoWindow = true });
        }

        /// <summary>目录里可能还有刚退出的进程句柄没释放，重试三轮再认输。</summary>
        static void DeleteWithRetry(string target, Action<string> log)
        {
            for (int i = 1; ; i++)
            {
                try { Directory.Delete(target, true); return; }
                catch (IOException) when (i < 3)
                {
                    log("目录还被占用，1 秒后第 " + i + " 次重试");
                    System.Threading.Thread.Sleep(1000);
                }
            }
        }

        /// <summary>把父进程的参数带给副本：剔掉标记位与旧的 /D=，再追加新的。</summary>
        static IEnumerable<string> SelfArgs(string extra)
        {
            return Environment.GetCommandLineArgs().Skip(1)
                .Where(x => !string.Equals(x, "/FROM-TEMP", StringComparison.OrdinalIgnoreCase)
                         && !x.StartsWith("/PARENT=", StringComparison.OrdinalIgnoreCase)
                         && !x.StartsWith("/D=", StringComparison.OrdinalIgnoreCase))
                .Concat(new[] { extra });
        }

        // -------------------------------------------------------- 注册表与快捷方式

        /// <summary>Run 值必须是 "exe 全路径" 外加引号，与 App.cs 的写法逐字一致。</summary>
        public static bool SetRunValue(string target, bool on, Action<string> log)
        {
            try
            {
                using (var key = Registry.CurrentUser.CreateSubKey(RunKeyPath))
                {
                    if (key == null) return false;
                    if (on) key.SetValue(RunKeyValueName, $"\"{Path.Combine(target, MainExeName)}\"");
                    else key.DeleteValue(RunKeyValueName, false);
                }
                log("Run 值 -> " + on);
                return true;
            }
            catch (Exception ex) { log("✗ 写 Run 值失败：" + ex.Message); return false; }
        }

        public static bool WriteUninstallKey(string target, Action<string> log)
        {
            try
            {
                using (var key = Registry.CurrentUser.CreateSubKey(UninstallKeyPath))
                {
                    if (key == null) return false;
                    string setup = Path.Combine(target, SetupExeName);
                    string exe = Path.Combine(target, MainExeName);
                    key.SetValue("DisplayName", ProductName);
                    key.SetValue("DisplayVersion", Version());
                    key.SetValue("Publisher", "Wildcreator");
                    key.SetValue("URL", "https://github.com/Wildcreator2010/dsh-status");
                    key.SetValue("DisplayIcon", exe + ",0");
                    key.SetValue("InstallLocation", target);
                    key.SetValue("InstallDate", DateTime.Now.ToString("yyyyMMdd"));
                    key.SetValue("UninstallString", "\"" + setup + "\" /UNINSTALL");
                    key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                }
                log("卸载注册项已写（系统「设置 → 应用」里能看到并卸载）");
                return true;
            }
            catch (Exception ex) { log("✗ 写卸载项失败：" + ex.Message); return false; }
        }

        public static string StartMenuPath() => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.StartMenu), "Programs", ProductName + ".lnk");

        public static string DesktopPath() => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), ProductName + ".lnk");

        /// <summary>late-bound COM 走 WScript.Shell：net48 里不引 IWshRuntimeLibrary 互操作程序集。</summary>
        public static bool MakeShortcut(string lnk, string target, Action<string> log)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(lnk));
                var t = Type.GetTypeFromProgID("WScript.Shell");
                if (t == null) { log("✗ 这台机器没有 WScript.Shell，建不了快捷方式"); return false; }
                dynamic shell = Activator.CreateInstance(t);
                dynamic sc = shell.CreateShortcut(lnk);
                sc.TargetPath = Path.Combine(target, MainExeName);
                sc.WorkingDirectory = target;
                sc.Arguments = "";
                sc.IconLocation = Path.Combine(target, MainExeName) + ",0";
                sc.Description = "嵌在任务栏里的 dsh 会话守望条";
                sc.Save();
                log("快捷方式 -> " + lnk);
                return true;
            }
            catch (Exception ex) { log("✗ 建快捷方式失败：" + ex.Message); return false; }
        }

        // ------------------------------------------------------------------ 杂项

        static string SelfPath() => Process.GetCurrentProcess().MainModule.FileName;

        static void KillRunning(Action<string> log)
        {
            // /f 必需：状态栏窗口是 Shell_TrayWnd 的子窗口，不带 /f 的优雅关闭会静默返回
            // 成功而进程照跑（README 常见问题与 smoke_test.py 的 --gui 段同一口径）。
            var psi = new ProcessStartInfo("taskkill", "/f /im " + MainExeName)
            {
                UseShellExecute = false, RedirectStandardOutput = true,
                RedirectStandardError = true, CreateNoWindow = true,
            };
            using (var p = Process.Start(psi))
            {
                if (p != null)
                {
                    p.WaitForExit(10000);
                    log(p.ExitCode == 0 ? "已关闭运行中的 Vigil" : "没有正在运行的 Vigil");
                }
            }
            for (int i = 0; i < 40 && Process.GetProcessesByName(ProductName).Length > 0; i++)
                System.Threading.Thread.Sleep(250);
        }

        static void Launch(string target, Action<string> log)
        {
            try
            {
                Process.Start(new ProcessStartInfo(Path.Combine(target, MainExeName)) { UseShellExecute = true });
                log("已启动 Vigil");
            }
            catch (Exception ex) { log("启动失败：" + ex.Message); }
        }

        static long TreeSize(string dir)
        {
            try { return Directory.EnumerateFiles(dir, "*", SearchOption.AllDirectories).Sum(f => new FileInfo(f).Length); }
            catch { return 1L << 30; }
        }

        static void Try(Action a, Action<string> log)
        {
            try { a(); } catch (Exception ex) { log("  （忽略：" + ex.Message + "）"); }
        }
    }
}
