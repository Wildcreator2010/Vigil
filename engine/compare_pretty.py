r"""--pretty 详细视图的逐行对照：同一帧快照，两种画法必须讲同一个故事。

为什么单独立一个：README 的排障那条（"状态一直待命 → 看 ~/.dsh/sessions 到底在不在动"）
指的是 `--pretty`，而装产物的人手上没有 Python —— 这一格没搬过去，那句指引就是空话。
搬过来就要有判据：render_pretty 读的是 snapshot() 那个 dict，里面有 8 处条件分支
（标题 / 轮次 / 等你 / 选项 / 错误 / 任务 / 余额 / 待处理），抄漏一条不会体现在 JSON 里，
只有这张人读的表上看得出来。

判据拿**已知年龄的合成会话**跑（同 compare_snapshot_boundary 的办法）。两个坑记在这里：
· 光有一条 tool/call 不算"在等人"—— 引擎要先看到 turn/start 才算这一轮开着
  （`_turn_open`），所以每个用例的记录序列都得从 turn/start 起步，
  否则测的是"没有 pending"这件与本用例无关的事（第一版就是这么空跑的）。
· Python 的 f-string 走的是 `str(v)`：`None` 打成 "None"、整数 12 打成 "12"、
  age_sec 打成 "300.0"。三种形态都要有用例钉住，否则 C++ 那边用 to_string 也"看着对"。

用法（在仓库根）:  python engine/compare_pretty.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import compression.zstd as zstd   # noqa: E402
import dsh_state as ds            # noqa: E402
import _exe                       # noqa: E402

EXE = _exe.require_exe()


def session_rec(idx: int) -> dict:
    return {"type": "session", "id": f"sess-{idx}", "cwd": os.path.join("C:", "work", f"proj-{idx}"),
            "createdAt": 1700000000, "agentPreset": "chat", "title": f"VPN 联机排查 {idx}"}


TURN = {"type": "turn/start", "data": {"turn": 19, "step": 8}}

ASK = {"type": "tool/call", "data": {
    "name": "ask_user_question", "callId": "c1",
    "arguments": json.dumps({"questions": [{"question": "先只改固件？",
                                            "options": [{"label": "只改固件"},
                                                        {"label": "两套都改"}]}]},
                            ensure_ascii=False)}}

TODO = {"type": "todo/write", "data": {"todos": [
    {"content": "a", "status": "completed"},
    {"content": "b", "status": "in_progress"},
    {"content": "c", "status": "pending"}]}}

# (每个会话的记录序列, 想让 render_pretty 走到哪几支, 期望里必须出现的关键词)
CASES = [
    ([ [session_rec(1), TURN] ], "轮次行：整数 T/S + 最后事件 + 浮点 age_sec", ["轮次", "T19/S8"]),
    ([ [session_rec(1)] ], "没有 turn 记录 → 轮次整行不出现", []),
    ([ [session_rec(1), TURN, ASK] ], "等你 + 选项两支", ["等你", "选项", "只改固件"]),
    ([ [session_rec(1), TURN, TODO] ], "任务行 done/total 与 in_progress", ["任务", "1/3"]),
    ([ [session_rec(1), {"type": "turn/start", "data": {"turn": None}}] ],
     "turn 显式为 null → 轮次行不出现（Python 判的是 is not None）", []),
    ([ [session_rec(1), {"type": "turn/start", "data": {"turn": 5}}] ],
     "step 这一格缺失 → Python 的 f-string 打 'None'，C++ 打空串就看不出来",
     ["SNone"]),
    ([ [session_rec(1), TURN, ASK], [session_rec(2), TURN, ASK] ],
     "两个会话：主会话 + 待处理那一行（int(age_sec) 走的是已舍入值）", ["待处理"]),
]


def corpus(tmp: str, seqs: list[list[dict]], mtime: float) -> str:
    """按 dsh 的目录形状摆好若干会话，返回给 Python 那侧用的 glob。"""
    for i, seq in enumerate(seqs, start=1):
        d = os.path.join(tmp, ".dsh", "sessions", f"proj-{i}", f"sess-{i}")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "session.v4.jsonl.zstd"), "wb") as fh:
            for r in seq:
                fh.write(zstd.compress((json.dumps(r, ensure_ascii=False) + "\n").encode("utf-8")))
        os.utime(os.path.join(d, "session.v4.jsonl.zstd"), (mtime, mtime))
    return os.path.join(tmp, ".dsh", "sessions", "*", "*", "session.v4.jsonl.zstd")


def cpp_pretty(tmp: str, ts: float) -> str:
    env = dict(os.environ)
    env["USERPROFILE"] = tmp
    env["HOME"] = tmp
    p = subprocess.run([EXE, "--snapshot", repr(ts), "--pretty", "--no-balance"],
                       capture_output=True, timeout=180, env=env, cwd=ROOT)
    if p.returncode != 0:
        raise RuntimeError(f"退出码 {p.returncode}: {p.stderr[:300]!r}")
    return p.stdout.decode("utf-8").rstrip("\n")


def main() -> int:
    fails: list[str] = []
    for seqs, why, must in CASES:
        tmp = tempfile.mkdtemp(prefix="vigil-pretty-")
        try:
            wall = time.time()
            glob_ = corpus(tmp, seqs, wall)
            first = os.path.join(tmp, ".dsh", "sessions", "proj-1", "sess-1",
                                 "session.v4.jsonl.zstd")
            mtime = os.path.getmtime(first)
            ts = mtime + 300.04          # 已知年龄：先舍入再取整跨不过去，正好测这一格
            want = ds.render_pretty(ds.snapshot(glob_, ts, want_balance=False))
            got = cpp_pretty(tmp, ts)
            wl, gl = want.split("\n"), got.split("\n")
            for i in range(max(len(wl), len(gl))):
                a = wl[i] if i < len(wl) else "<py 少一行>"
                b = gl[i] if i < len(gl) else "<cpp 少一行>"
                if a != b:
                    fails.append(f"{why}｜第 {i + 1} 行 py={a!r} cpp={b!r}")
            # 空跑检查：这个用例声称要走的分支，得真的出现在输出里
            missing = [k for k in must if k not in want]
            if missing:
                fails.append(f"{why}：合成记录没走到 {missing}，用例是空跑")
        except Exception as exc:
            fails.append(f"{why}: 跑不动 {type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # --demo 那一支：Python 在 pretty 分派之前就 return，所以 --demo X --pretty
    # 也必须出 JSON —— 钉住"别让全局 flag 把 demo 改道"
    p = subprocess.run([EXE, "--demo", "needs_action", "--pretty"],
                       capture_output=True, timeout=120, cwd=ROOT)
    out = p.stdout.decode("utf-8").strip()
    if not out.startswith("{"):
        fails.append(f"--demo 该出 JSON 却出了别的：{out[:80]!r}")

    for f in fails:
        print("  ✗ " + f)
    if fails:
        print(f"\n{len(fails)} 处不一致")
        return 1
    print(f"✓ {len(CASES)} 组形状的 --pretty 逐行一致；--demo 仍只出 JSON")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
