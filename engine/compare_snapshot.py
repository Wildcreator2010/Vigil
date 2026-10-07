"""快照级对照：C++ 的 --snapshot vs Python 的 snapshot(want_balance=False)。

单会话判定对齐（compare_classify.py）只证明"一个会话怎么看"，而一帧快照还牵扯
主会话选择、waiting/recent 的筛选与上限、文案组装、字段白名单 —— 那些都在判定之外。
所以再要一层：同一时刻、同一批真实会话，两边各出一帧，逐字段深比。

余额那一格故意不比：HTTP + DPAPI + Key 发现还没搬过去，C++ 固定出"已禁用"，
与 want_balance=False 的 Python 同形。搬完之后把 _SKIP 里的 balance 去掉即可。

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

EXE = os.path.join(HERE, "vigil-engine.exe")
# 不比的东西：时间戳两边必然不同；balance 是已知未搬的那一层
_SKIP = {"generated_at", "balance"}


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
        return [] if abs(float(a) - float(b)) < 0.55 else [f"{path}: py={a!r} cpp={b!r}"]
    return [] if a == b else [f"{path}: py={a!r} cpp={b!r}"]


def main() -> int:
    if not os.path.isfile(EXE):
        print("先编引擎：engine/build.cmd")
        return 2
    ts = ds.now()
    py = ds.snapshot(ds.SESSION_GLOB, ts, want_balance=False)
    p = subprocess.run([EXE, "--snapshot", repr(ts), "--no-balance"],
                       capture_output=True, timeout=120)
    if p.returncode != 0:
        print(f"C++ 退出码 {p.returncode}: {p.stderr[:400]!r}")
        return 1
    cpp = json.loads(p.stdout.decode("utf-8"))

    print(f"Python: state={py['state']} sessions_scanned={py['sessions_scanned']} "
          f"waiting={len(py['waiting'])} recent={len(py['recent'])} sessions={len(py['sessions'])}")
    print(f"C++   : state={cpp['state']} sessions_scanned={cpp['sessions_scanned']} "
          f"waiting={len(cpp['waiting'])} recent={len(cpp['recent'])} sessions={len(cpp['sessions'])}")
    diffs = walk(py, cpp)
    for d in diffs[:40]:
        print("  ✗ " + d)
    if not diffs:
        print("\n✓ 整帧快照逐字段一致（不比 generated_at 与 balance）")
        return 0
    print(f"\n共 {len(diffs)} 处差异" + ("（只显示前 40 条）" if len(diffs) > 40 else ""))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
