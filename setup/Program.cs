using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;

namespace Vigil.Setup
{
    /// <summary>
    /// 参数分流：静默模式直接干活并返回退出码，GUI 模式开窗口。
    ///
    /// 静默开关的语义刻意保守：`/PURGE-DATA` 才是"连设置一起删"，不写就保留。
    /// spec §5.6 原本写的是反方向的 `/NO-PURGE-DATA`，倒过来是因为删除应当 opt-in ——
    /// 一个记错的开关不该让人丢掉按 DPAPI 加密的余额 Key（那玩意儿换电脑本来就找不回来）。
    /// </summary>
    public static class Program
    {
        static StreamWriter _logFile;

        [STAThread]
        public static int Main(string[] raw)
        {
            // 中文写进 setup.log / 控制台一律 UTF-8，否则 Win10 默认 GBK 代码页就是乱码。
            try { Console.OutputEncoding = new UTF8Encoding(false); } catch { }

            var args = new List<string>(raw);
            bool silent = Has(args, "/S"), uninstall = Has(args, "/UNINSTALL"), fromTemp = Has(args, "/FROM-TEMP");
            var o = new Installer.Options
            {
                TargetDir = TargetOf(args, uninstall),
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
                Log("✗ 不认识的参数：" + unknown);
                Log("用法: Vigil-Setup.exe [/S] [/D=<目录>] [/NO-AUTOSTART] [/NO-STARTMENU] [/DESKTOP] [/NO-START] [/UNINSTALL] [/PURGE-DATA]");
                return 2;
            }

            AppDomain.CurrentDomain.ProcessExit += (s, e) =>
            {
                try
                {
                    if (_logFile == null) return;
                    _logFile.Flush();
                    _logFile.Dispose();
                    _logFile = null;
                }
                catch { }
            };
            Log("--- " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " "
                + (uninstall ? "卸载" : "安装") + " " + ((silent || fromTemp) ? "静默" : "GUI") + " ---");

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

        /// <summary>
        /// 卸载时"装在哪"从哪来。注册表里那条 UninstallString 是 `"&lt;安装目录&gt;\Vigil-Setup.exe" /UNINSTALL`，
        /// 不带 /D=，所以必须认向导自己所在的目录——拿默认路径去删一个不存在的地方，
        /// 就是"卸载成功了但文件还在全盘皆是"（端到端演练实测到的）。
        /// </summary>
        static string TargetOf(List<string> args, bool uninstall)
        {
            var given = ValueOf(args, "/D=");
            if (given != null) return given;
            if (uninstall)
            {
                var here = Installer.AppDir();
                if (File.Exists(Path.Combine(here, Installer.MainExeName))) return here;
            }
            return Installer.DefaultTargetDir();
        }

        static int ParentPid(List<string> args)
        {
            var raw = args.FirstOrDefault(x => x.StartsWith("/PARENT=", StringComparison.OrdinalIgnoreCase));
            if (raw == null) return 0;
            int pid;
            return int.TryParse(raw.Substring(8), out pid) ? pid : 0;
        }

        /// <summary>进度与错误同时进控制台和 %LOCALAPPDATA%\Vigil\setup.log。</summary>
        static void Log(string msg)
        {
            Console.WriteLine(msg);
            try
            {
                var dir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Vigil");
                Directory.CreateDirectory(dir);
                if (_logFile == null)
                    _logFile = new StreamWriter(Path.Combine(dir, "setup.log"), true) { AutoFlush = true };
                _logFile.WriteLine(msg);
            }
            catch { /* 日志写不进去别把安装搞崩 */ }
        }

        static bool Has(List<string> a, string flag) =>
            a.Any(x => string.Equals(x, flag, StringComparison.OrdinalIgnoreCase));

        static string ValueOf(List<string> a, string prefix)
        {
            // 必须砍掉前缀再返回：留着 "/D=" 就成了拿 "=/C:\x" 当路径用。
            var raw = a.FirstOrDefault(x => x.StartsWith(prefix, StringComparison.OrdinalIgnoreCase));
            return raw == null ? null : raw.Substring(prefix.Length);
        }

        static readonly string[] KnownFlags =
        {
            "/S", "/UNINSTALL", "/FROM-TEMP", "/NO-AUTOSTART", "/NO-STARTMENU",
            "/NO-START", "/DESKTOP", "/PURGE-DATA", "/PARENT", "/D",
        };

        static string FindUnknown(List<string> a) => a.FirstOrDefault(x =>
            x.StartsWith("/") && !KnownFlags.Contains(x.Split('=')[0], StringComparer.OrdinalIgnoreCase));
    }
}
