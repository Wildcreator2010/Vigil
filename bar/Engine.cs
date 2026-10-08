using System;
using System.Diagnostics;
using System.IO;
using System.Text;

namespace Vigil
{
    /// <summary>
    /// 引擎的启动形状，收在一处。
    ///
    /// 两级：随包的 vigil-engine.exe（C++，解压与判定都在自己怀里）优先；它不在
    /// （开发检出没跑过 engine\build.cmd）才退回 `python -X utf8 dsh_state.py`。
    ///
    /// 为什么要收：五处调用点原先各自拼 `-X utf8 "{engine}" {verb}`，一共五个地方。
    /// 换引擎形状要改五遍，而漏改一处的表现不是报错 —— 是面板里一半功能走新引擎、
    /// 另一半还在满机找 Python，找不到的那半弹一句"状态检测引擎不可用"。
    /// </summary>
    internal static class Engine
    {
        internal const string ExeName = "vigil-engine.exe";

        /// 编好的 C++ 引擎在哪；没有则 null。安装包与开发检出两条路径都认。
        internal static string NativePath()
        {
            string here = Path.Combine(AppContext.BaseDirectory, ExeName);
            if (File.Exists(here)) return here;
            // bar\bin\Release\net10.0-windows\ 往上四级是仓库根，引擎产物在 engine\ 下
            string dev = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory,
                            "..", "..", "..", "..", "engine", ExeName));
            return File.Exists(dev) ? dev : null;
        }

        /// Python 那版引擎的脚本位置；没有则 null。
        internal static string ScriptPath()
        {
            string here = Path.Combine(AppContext.BaseDirectory, "dsh_state.py");
            if (File.Exists(here)) return here;
            string dev = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory,
                            "..", "..", "..", "..", "dsh_state.py"));
            return File.Exists(dev) ? dev : null;
        }

        /// 回退级才碰解释器：ResolvePython 会逐个候选子进程跑 `import compression.zstd`，
        /// 随包的引擎在时没必要为这个掏启动时间。
        internal static string PythonForFallback() => NativePath() == null ? App.ResolvePython() : null;

        /// 正在用的是哪一级："native" / "python" / ""（两条都不通）。
        internal static string Kind()
        {
            if (NativePath() != null) return "native";
            return PythonForFallback() != null && ScriptPath() != null ? "python" : "";
        }

        /// 按调用形状拼出启动参数。两条都不通时返回 null，由调用方按自己的口径报错
        /// （状态栏那条是弹气泡、shot 那条是静默带空帧继续 —— 不该在这里替它们决定）。
        /// withStdin：--save-balance-key 那两条，Key 只能从管道进（argv 不是秘密存放处）。
        /// withStderr：常驻 --watch 要把引擎的诊断行转进日志环形缓冲。
        internal static ProcessStartInfo Start(string modeArgs, bool withStdin = false, bool withStderr = false)
        {
            string exe = NativePath();
            string file, args, workdir;
            if (exe != null)
            {
                file = exe;
                args = modeArgs;
                workdir = Path.GetDirectoryName(exe);
            }
            else
            {
                string py = App.ResolvePython(), script = ScriptPath();
                if (py == null || script == null) return null;
                file = py;
                args = $"-X utf8 \"{script}\" {modeArgs}";
                workdir = Path.GetDirectoryName(script);
            }
            var psi = new ProcessStartInfo
            {
                FileName = file,
                Arguments = args,
                WorkingDirectory = workdir,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                // 显式 UTF-8，两侧都要：不写 StandardInputEncoding 时写入侧按本机 ANSI
                // 码页走，非 ASCII 的 Key 会被改形成再存进 DPAPI —— 而 Key 是用户从
                // 控制台复制粘贴的，恰恰最可能带非 ASCII。
                StandardOutputEncoding = new UTF8Encoding(false),
            };
            if (withStdin)
            {
                psi.RedirectStandardInput = true;
                psi.StandardInputEncoding = new UTF8Encoding(false);
            }
            if (withStderr)
            {
                psi.RedirectStandardError = true;
                psi.StandardErrorEncoding = new UTF8Encoding(false);
            }
            return psi;
        }

        /// 关于页与 --engine-probe 显示用的"引擎是什么"。
        internal static string Describe()
        {
            string exe = NativePath();
            if (exe != null) return exe;
            string py = App.ResolvePython(), script = ScriptPath();
            if (py != null && script != null) return py + "  " + script;
            return "未找到 vigil-engine.exe，也没有能解 zstd 的 Python 3.14";
        }
    }
}
