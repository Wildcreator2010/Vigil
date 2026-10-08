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

EXE = os.path.join(HERE, "vigil-engine.exe")
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


def main() -> int:
    if not os.path.isfile(EXE):
        print("先编引擎：engine/build.cmd")
        return 2
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

        # 缓存诊断行：第二轮起命中数应当等于"这一轮扫到的会话数"
        time.sleep(0.6)
        proc.terminate()
        err = proc.stderr.read() or ""
        hits = []
        for line in err.splitlines():
            if line.startswith("CACHE") and "hits=" in line:
                hits.append(int(line.split("hits=")[1].split()[0]))
        if not hits:
            fails.append(f"stderr 里没有 CACHE 诊断行（缓存没实现）：{err[:200]!r}")
        elif len(hits) >= 2 and hits[1] < 10:
            fails.append(f"第二轮缓存命中只有 {hits[1]}，文件没变应当接近全部命中：{hits}")
    finally:
        if proc.poll() is None:
            proc.kill()

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
