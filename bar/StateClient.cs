using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;

namespace Vigil
{
    public sealed class Todo
    {
        [JsonPropertyName("total")] public int Total { get; set; }
        [JsonPropertyName("done")] public int Done { get; set; }
        [JsonPropertyName("in_progress")] public int InProgress { get; set; }
        [JsonPropertyName("pending")] public int Pending { get; set; }
    }

    public sealed class Pending
    {
        [JsonPropertyName("kind")] public string Kind { get; set; }
        [JsonPropertyName("tool")] public string Tool { get; set; }
        [JsonPropertyName("text")] public string Text { get; set; }
        [JsonPropertyName("options")] public List<string> Options { get; set; }
    }

    public sealed class Session
    {
        [JsonPropertyName("project")] public string Project { get; set; }
        [JsonPropertyName("title")] public string Title { get; set; }
        [JsonPropertyName("state")] public string State { get; set; }
        [JsonPropertyName("turn")] public int? Turn { get; set; }
        [JsonPropertyName("step")] public int? Step { get; set; }
        [JsonPropertyName("age_sec")] public double AgeSec { get; set; }
        [JsonPropertyName("last_tool")] public string LastTool { get; set; }
        [JsonPropertyName("pending")] public Pending Pending { get; set; }
        [JsonPropertyName("todo")] public Todo Todo { get; set; }
        [JsonPropertyName("error")] public string Error { get; set; }
    }

    public sealed class Balance
    {
        [JsonPropertyName("available")] public bool Available { get; set; }
        [JsonPropertyName("currency")] public string Currency { get; set; }
        [JsonPropertyName("total")] public string Total { get; set; }
        /// <summary>赠送额度与充值额度分开：DeepSeek 的余额接口本来就分这两笔
        /// （dsh_state.py 的 granted_balance / topped_up_balance），合在一起看不出
        /// 「充的钱还没动、送的可用完了」那种状态。</summary>
        [JsonPropertyName("granted")] public string Granted { get; set; }
        [JsonPropertyName("topped_up")] public string ToppedUp { get; set; }
        [JsonPropertyName("error")] public string Error { get; set; }
        /// <summary>这一帧的取数时间（unix 秒）。0 表示引擎没给。</summary>
        [JsonPropertyName("fetched_at")] public double FetchedAt { get; set; }
        /// <summary>是不是 TTL 内的缓存帧。5 分钟内的重复快照都算缓存，
        /// 不标出来用户点完「立即刷新」看不出到底重查了没有。</summary>
        [JsonPropertyName("cached")] public bool Cached { get; set; }
        /// <summary>Key 的**来源**，不是 Key 本身：`env:DEEPSEEK_BALANCE_KEY` / `dpapi` /
        /// `file:xxx` / `none`（dsh_state.py 的 balance_key）。面板只显示这一格，
        /// 好让用户看清「环境变量盖住了已保存的 Key」这种情形。缺失时为 null
        /// （--no-balance 的帧只有 available/error/skipped）。</summary>
        [JsonPropertyName("source")] public string Source { get; set; }
    }

    public sealed class Brief
    {
        [JsonPropertyName("project")] public string Project { get; set; }
        [JsonPropertyName("state")] public string State { get; set; }
        [JsonPropertyName("text")] public string Text { get; set; }
        /// <summary>会话目录名。引擎的 waiting 条目本来就带这一格（dsh_state.py 的
        /// snapshot()["waiting"]），C# 侧此前没接。概览页要靠它把待处理正文
        /// 对回 sessions[].pending.options —— spec §4 保留 options 的理由就是 §5 那一格。</summary>
        [JsonPropertyName("key")] public string Key { get; set; }
    }

    public sealed class Usage
    {
        [JsonPropertyName("input")] public long Input { get; set; }
        [JsonPropertyName("output")] public long Output { get; set; }
        [JsonPropertyName("total")] public long Total { get; set; }
    }

    public sealed class SessionRow
    {
        [JsonPropertyName("key")] public string Key { get; set; }
        [JsonPropertyName("project")] public string Project { get; set; }
        [JsonPropertyName("title")] public string Title { get; set; }
        [JsonPropertyName("state")] public string State { get; set; }
        [JsonPropertyName("turn")] public int? Turn { get; set; }
        [JsonPropertyName("step")] public int? Step { get; set; }
        [JsonPropertyName("age_sec")] public double AgeSec { get; set; }
        [JsonPropertyName("last_event")] public string LastEvent { get; set; }
        [JsonPropertyName("last_tool")] public string LastTool { get; set; }
        [JsonPropertyName("end_reason")] public string EndReason { get; set; }
        // 可空：引擎侧是 "records": s.get("records")（dsh_state.py 的 _session_rows），
        // 出 null 是合法的。这里写成非可空 int 的话，System.Text.Json 是在**整帧**上抛，
        // 而 StateClient.ReadStdout 的 catch 只记一行「JSON 解析失败」就 continue ——
        // 表现是状态栏从此静默冻结，而不是变红。null 时概览页那一格留白（与 Turn 同行为）。
        [JsonPropertyName("records")] public int? Records { get; set; }
        [JsonPropertyName("todo")] public Todo Todo { get; set; }
        [JsonPropertyName("usage_total")] public Usage Usage { get; set; }
        [JsonPropertyName("pending")] public Pending Pending { get; set; }
    }

    public sealed class Snapshot
    {
        [JsonPropertyName("ok")] public bool Ok { get; set; }
        [JsonPropertyName("app_running")] public bool AppRunning { get; set; }
        [JsonPropertyName("state")] public string State { get; set; }
        [JsonPropertyName("label")] public string Label { get; set; }
        [JsonPropertyName("glyph")] public string Glyph { get; set; }
        [JsonPropertyName("color")] public string Color { get; set; }
        [JsonPropertyName("strip_left")] public string StripLeft { get; set; }
        [JsonPropertyName("strip_right")] public string StripRight { get; set; }
        [JsonPropertyName("tooltip")] public string Tooltip { get; set; }
        [JsonPropertyName("tip_lines")] public List<string> TipLines { get; set; }
        [JsonPropertyName("session")] public Session Session { get; set; }
        [JsonPropertyName("balance")] public Balance Balance { get; set; }
        [JsonPropertyName("waiting")] public List<Brief> Waiting { get; set; }
        [JsonPropertyName("recent")] public List<Brief> Recent { get; set; }
        // 会话表逐行快照（spec §4 的白名单行，无 path/cwd）。
        // 可以是 null：--watch 的异常帧（ok:false）根本不带 sessions，
        // 消费方（概览页）必须容忍空表，见 Pages.cs 的 Refresh。
        [JsonPropertyName("sessions")] public List<SessionRow> Sessions { get; set; }
        [JsonPropertyName("error")] public string Error { get; set; }
    }

    /// <summary>
    /// 状态来源：常驻子进程 python dsh_state.py --watch，逐行读 NDJSON。
    /// 读管道放在独立线程，避免阻塞 UI；子进程退出后自动重启。
    /// </summary>
    internal sealed class StateClient : IDisposable
    {
        private static readonly JsonSerializerOptions JsonOptions = new JsonSerializerOptions
        {
            PropertyNameCaseInsensitive = true,
        };

        private readonly string _python;
        private readonly string _engine;
        private readonly int _intervalSeconds;
        private readonly object _gate = new object();
        private Process _proc;
        private Thread _reader;
        private Thread _errReader;
        private volatile bool _disposed;
        private DateTime _lastStart = DateTime.MinValue;

        public event Action<Snapshot> Updated;
        public event Action<string> Log;

        public StateClient(string python, string engine, int intervalSeconds)
        {
            _python = python;
            _engine = engine;
            _intervalSeconds = Math.Max(1, intervalSeconds);
        }

        public void Start()
        {
            lock (_gate)
            {
                if (_disposed || _proc != null) return;
                var psi = new ProcessStartInfo
                {
                    FileName = _python,
                    Arguments = $"-X utf8 \"{_engine}\" --watch --interval {_intervalSeconds}",
                    WorkingDirectory = Path.GetDirectoryName(_engine),
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    StandardOutputEncoding = new UTF8Encoding(false),
                    StandardErrorEncoding = new UTF8Encoding(false),
                };
                try
                {
                    _proc = Process.Start(psi);
                }
                catch (Exception ex)
                {
                    RaiseLog($"引擎启动失败: {ex.Message}");
                    return;
                }
                _lastStart = DateTime.Now;
                RaiseLog($"引擎已启动 PID={_proc.Id}");
                _reader = new Thread(ReadStdout) { IsBackground = true, Name = "dsh-state-stdout" };
                _errReader = new Thread(ReadStderr) { IsBackground = true, Name = "dsh-state-stderr" };
                _reader.Start();
                _errReader.Start();
            }
        }

        private void ReadStdout()
        {
            var proc = _proc;
            if (proc == null) return;
            try
            {
                using var reader = proc.StandardOutput;
                while (!_disposed)
                {
                    string line = reader.ReadLine();
                    if (line == null) break;
                    if (line.Trim().Length == 0) continue;
                    Snapshot snap = null;
                    try
                    {
                        snap = JsonSerializer.Deserialize<Snapshot>(line, JsonOptions);
                    }
                    catch (Exception ex)
                    {
                        RaiseLog($"JSON 解析失败: {ex.Message}");
                        continue;
                    }
                    if (snap != null) Updated?.Invoke(snap);
                }
            }
            catch (Exception ex)
            {
                if (!_disposed) RaiseLog($"引擎 stdout 读取结束: {ex.Message}");
            }
            RaiseLog("引擎已退出，准备重启");
            lock (_gate)
            {
                if (_disposed) return;
                _proc = null;
            }
            if ((DateTime.Now - _lastStart).TotalSeconds < 2) Thread.Sleep(2000);
            Start();
        }

        private void ReadStderr()
        {
            var proc = _proc;
            if (proc == null) return;
            try
            {
                using var reader = proc.StandardError;
                while (!_disposed)
                {
                    string line = reader.ReadLine();
                    if (line == null) break;
                    if (line.Trim().Length > 0) RaiseLog($"引擎 stderr: {line}");
                }
            }
            catch
            {
            }
        }

        private void RaiseLog(string message) => Log?.Invoke(message);

        public void Dispose()
        {
            lock (_gate)
            {
                _disposed = true;
                if (_proc == null) return;
                try
                {
                    if (!_proc.HasExited) _proc.Kill(entireProcessTree: true);
                }
                catch
                {
                }
                _proc.Dispose();
                _proc = null;
            }
        }
    }
}
