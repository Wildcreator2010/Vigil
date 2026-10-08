"""快照级对照：C++ 的 --snapshot vs Python 的 snapshot(want_balance=False)。

单会话判定对齐（compare_classify.py）只证明"一个会话怎么看"，而一帧快照还牵扯
主会话选择、waiting/recent 的筛选与上限、文案组装、字段白名单 —— 那些都在判定之外。
所以再要一层：同一时刻、同一批真实会话，两边各出一帧，逐字段深比。

余额那一格比的是"已禁用"这个形状：两边都走 want_balance=False / --no-balance，
字段集合与占位文案必须逐字一致（真实查询那一层由 compare_balance.py 单独对照，
那里有 HTTP 时间戳，不能混进这一帧）。

用法（在仓库根）:  python engine/compare_snapshot.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import dsh_state as ds          # noqa: E402
import _exe                   # noqa: E402

EXE = _exe.require_exe()
# 不比的东西只剩时间戳；balance 已经搬完，"已禁用"那一格要逐字比
_SKIP = {"generated_at"}


def walk(a, b, path=""):
    """递归比两个 JSON 值，返回差异列表（最多留 40 条，够看又刷屏不了）。"""
    diffs = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if path == "" and k in _SKIP:
                continue
            if k not in a:
                diffs.append(f"{path}.{k}: 只在 C++ 有 = {b[k]!r}")
            elif k not in b:
                diffs.append(f"{path}.{k}: 只在 Python 有 = {a[k]!r}")
            else:
                diffs += walk(a[k], b[k], f"{path}.{k}")
        return diffs
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append(f"{path}: 长度 py={len(a)} cpp={len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            diffs += walk(x, y, f"{path}[{i}]")
        return diffs
    if isinstance(a, bool) or isinstance(b, bool):
        return [] if a == b else [f"{path}: py={a!r} cpp={b!r}"]
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        # 容差 0.55 曾经把"age_sec 从一位小数被改成整数秒"整个吞掉 —— 变异测试当场
        # 抓到：注入那个改动，对照器照样报"一致"。age_sec 两边都是 %.1f，
        # 该逐字相等；其余数值是计数，也该精确相等。留 0.05 只给浮点表示差异。
        return [] if abs(float(a) - float(b)) < 0.05 else [f"{path}: py={a!r} cpp={b!r}"]
    return [] if a == b else [f"{path}: py={a!r} cpp={b!r}"]


def main() -> int:
    ts = ds.now()
    py = ds.snapshot(ds.SESSION_GLOB, ts, want_balance=False)
    p = subprocess.run([EXE, "--snapshot", repr(ts), "--no-balance"],
                       capture_output=True, timeout=120)
    if p.returncode != 0:
        print(f"C++ 退出码 {p.returncode}: {p.stderr[:400]!r}")
        return 1
    cpp = json.loads(p.stdout.decode("utf-8"))

    # 自证不是空跑：把 balance 那一格改坏一个字段，对照器必须立刻报出来。
    # balance 曾经整格躺在 _SKIP 里（那时它确实还没搬），搬完之后一旦忘了摘掉、
    # 或者哪天 walk() 被改成提前 return，这一层就又没人看了 —— 而"没人看"是
    # 静默的。容差 0.05 吞掉 age_sec 那次是同一类事故，这里不再靠"我记得我在比"。
    probe = json.loads(json.dumps(cpp))
    probe["balance"]["available"] = True
    if not any(d.startswith(".balance") for d in walk(py, probe)):
        print("✗ 对照器没有在比 balance：_SKIP 里还留着它，或 walk() 短路了")
        return 1

    print(f"Python: state={py['state']} sessions_scanned={py['sessions_scanned']} "
          f"waiting={len(py['waiting'])} recent={len(py['recent'])} sessions={len(py['sessions'])}")
    print(f"C++   : state={cpp['state']} sessions_scanned={cpp['sessions_scanned']} "
          f"waiting={len(cpp['waiting'])} recent={len(cpp['recent'])} sessions={len(cpp['sessions'])}")
    diffs = walk(py, cpp)
    for d in diffs[:40]:
        print("  ✗ " + d)
    if not diffs:
        print("\n✓ 整帧快照逐字段一致（只不比 generated_at）")
        return 0
    print(f"\n共 {len(diffs)} 处差异" + ("（只显示前 40 条）" if len(diffs) > 40 else ""))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
