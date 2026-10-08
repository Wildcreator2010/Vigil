"""RED→GREEN：C++ 引擎的常驻 --watch 与解析缓存。

要验的两件事（都是 C# 侧真实依赖的行为）：
① --watch 每 --interval 秒出一行 NDJSON，且 generated_at 单调递增 ——
   StateClient 就是按行读的（bar/StateClient.cs 的 ReadStdout），一次坏行不能带崩。
② 会话文件没变时，第二轮必须走缓存 —— 缓存命中数从 stderr 的诊断行读。
   为什么把命中数吐在 stderr 而不是加个只为测试存在的开关：常驻引擎的
   "这一轮到底重解了多少个文件"本来就是排障要看的东西（旧版 59 个会话
   每轮全量重读、单轮 1567ms 那个坑，缺的就是这个数）。

用法: python engine/compare_watch.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import dsh_state as ds          # noqa: E402
import _exe                   # noqa: E402

EXE = _exe.require_exe()
ROUNDS = 3
INTERVAL = 0.2


def read_lines(proc, n, timeout=30.0):
    """按行读子进程 stdout。管道在 Windows 上是阻塞的，所以用读线程 + 队列。"""
    import queue
    import threading
    q: queue.Queue = queue.Queue()

    def pump():
        for _ in range(n):
            line = proc.stdout.readline()
            if not line:
                break
            q.put(line)

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    lines = []
    deadline = time.time() + timeout
    while len(lines) < n and time.time() < deadline:
        try:
            lines.append(q.get(timeout=0.5))
        except queue.Empty:
            pass
    return lines


def orphan_check(limit: float = 10.0) -> list[str]:
    """关掉读端以后，两边的常驻循环都必须自己收工。

    README 明写"引擎子进程靠 stdout 管道断裂随后自行退出，不会留孤儿"，而
    `taskkill /f /im Vigil.exe` 走不到 StateClient.Dispose —— 管道断裂是唯一那条退出路径。
    Python 靠 sys.stdout.write 抛 BrokenPipeError 自然成立；C++ 的 printf 失败**只置
    流上的错误位、不抛不醒**，一开始这里不检查，实测留下 3 个孤儿子进程在后台各睡各的。
    所以这一条不是"顺手加的"，是那次现场的账。
    """
    fails = []
    cases = [
        ("C++", [EXE, "--watch", "--interval", str(INTERVAL), "--no-balance"]),
        ("Python", [sys.executable, "-X", "utf8", os.path.join(ROOT, "dsh_state.py"),
                    "--watch", "--interval", str(INTERVAL), "--no-balance"]),
    ]
    for name, cmd in cases:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT)
        try:
            p.stdout.readline()          # 确认它真的开始出帧了
            p.stdout.close()             # 读端关断 == Vigil 被强杀
            deadline = time.time() + limit
            while time.time() < deadline:
                if p.poll() is not None:
                    break
                time.sleep(0.2)
            else:
                fails.append(f"{name} 引擎在读端关闭后 {limit:.0f} 秒仍不退出（会留孤儿）")
        finally:
            if p.poll() is None:
                p.kill()
            p.wait(timeout=10)
    return fails


def main() -> int:
    fails = []

    proc = subprocess.Popen([EXE, "--watch", "--interval", str(INTERVAL), "--no-balance"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", bufsize=1, cwd=ROOT)
    try:
        lines = read_lines(proc, ROUNDS, timeout=40.0)
        if len(lines) < ROUNDS:
            fails.append(f"--watch 只出了 {len(lines)} 行，要 {ROUNDS} 行（模式没实现就一行都没有）")
        else:
            stamps = []
            for i, ln in enumerate(lines):
                try:
                    snap = json.loads(ln)
                except Exception as exc:
                    fails.append(f"第 {i + 1} 行不是合法 JSON：{exc}: {ln[:120]!r}")
                    continue
                if not snap.get("ok"):
                    fails.append(f"第 {i + 1} 行 ok 不为 true")
                stamps.append(snap.get("generated_at", 0))
            if len(stamps) == ROUNDS:
                for i in range(1, ROUNDS):
                    if stamps[i] <= stamps[i - 1]:
                        fails.append(f"generated_at 没单调递增：{stamps}")
                # 每一行都拿 Python 同一时刻的快照对一遍：常驻路径不能只测"出几行"
                for i, ln in enumerate(lines):
                    snap = json.loads(ln)
                    py = ds.snapshot(ds.SESSION_GLOB, snap["generated_at"], want_balance=False)
                    for k in ("state", "strip", "strip_left", "tooltip", "label", "color"):
                        if py.get(k) != snap.get(k):
                            fails.append(f"第 {i + 1} 行 {k}: py={py.get(k)!r} cpp={snap.get(k)!r}")
                    if len(py.get("sessions", [])) != len(snap.get("sessions", [])):
                        fails.append(f"第 {i + 1} 行 sessions 条数 py={len(py['sessions'])} "
                                     f"cpp={len(snap['sessions'])}")

        # 缓存诊断行的新契约：**只在有未命中时**才打（每帧都打会把 App.Log 的
        # 200 行环形缓冲挤爆）。所以稳态的证据不是"第二行 hits 很大"，而是
        # "第二轮起根本没有 CACHE 行" —— 沉默本身就是全命中的证明。
        # 上一版断言写的是 hits[1] >= 10，改成安静稳态后那条会静默失效
        # （只剩一行，`len(hits) >= 2` 永不成立），所以判据必须跟着换。
        time.sleep(0.6)
        proc.terminate()
        err = proc.stderr.read() or ""
        hits = []
        for line in err.splitlines():
            if line.startswith("CACHE") and "hits=" in line:
                hits.append(int(line.split("hits=")[1].split()[0]))
        if not hits:
            fails.append(f"第一轮应当有未命中并打 CACHE 行，实际一行都没有：{err[:200]!r}")
        elif hits[0] != 0:
            fails.append(f"第一轮 hits 应为 0（冷启动没有缓存），实得 {hits[0]}")
        elif len(hits) > 1:
            fails.append(f"稳态不该再打 CACHE 行（打了说明每帧都在重解）：{hits}")
    finally:
        if proc.poll() is None:
            proc.kill()

    # ③ 读端关掉以后，常驻循环必须自己收工。
    # README 明写"引擎子进程靠 stdout 管道断裂随后自行退出，不会留孤儿"，而
    # `taskkill /f /im Vigil.exe` 走不到 StateClient.Dispose —— 管道断裂是**唯一**
    # 那条退出路径。Python 那边靠 write 抛 BrokenPipeError 自然成立；C++ 的 printf
    # 失败只置流上的错误位、不抛不醒，一开始没检查，实测留下 3 个孤儿子进程。
    fails.extend(orphan_check())

    for f in fails:
        print("  ✗ " + f)
    if fails:
        print(f"\n{len(fails)} 条不满足")
        return 1
    print(f"✓ --watch 出 {ROUNDS} 行、generated_at 单调、每行与 Python 同刻快照一致")
    print("✓ 缓存诊断行显示第二轮命中生效")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
