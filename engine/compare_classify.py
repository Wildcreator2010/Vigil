"""C++ 判定核心 vs Python 引擎的逐字段对照。

为什么这么对：C++ 那份是照 dsh_state.py 的 classify() 手搬的，手搬必错。
判据不能是"看着一样"，得拿真数据逐字段比。

分工按引擎的边界切：解压仍由 Python 做（本机实测 Windows 自带压缩 API 解不了
zstd，见 engine/probe_zstd.cpp 的结论），C++ 只吃已解压的 JSONL。所以这个脚本
是"同一份记录，两个判定器"的对照器，不是端到端测试。

用法（在仓库根）:  python engine/compare_classify.py [--limit N]
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import dsh_state as ds          # noqa: E402

EXE = os.path.join(HERE, "vigil-engine.exe")
FIELDS = ("state", "turn", "step", "last_event", "last_tool", "end_reason")
# 默认走 --session：C++ 自己解压，整条通路都不碰 CPython。加 --stdin 退回只比判定的老口径。
FILE_MODE = "--stdin" not in sys.argv


def cpp_verdict(records: list[bytes], mtime: float, ts: float) -> dict:
    p = subprocess.run(
        [EXE, "--classify", repr(mtime), repr(ts)],
        input=b"\n".join(records), capture_output=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError(f"退出码 {p.returncode}: {p.stderr[:200]!r}")
    return json.loads(p.stdout.decode("utf-8"))


def cpp_verdict_file(path: str, mtime: float, ts: float) -> dict:
    """让 C++ 自己读文件、自己解压 —— 这才是能替掉 Python 的那条完整通路。

    与 cpp_verdict 的区别就是"谁剥掉 zstd"：走 stdin 那份只证明判定对，
    走 --session 这份才证明整个引擎不再需要 CPython。
    """
    p = subprocess.run([EXE, "--session", path, repr(mtime), repr(ts)],
                       capture_output=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError(f"退出码 {p.returncode}: {p.stderr[:200]!r}")
    return json.loads(p.stdout.decode("utf-8"))


def main() -> int:
    if not os.path.isfile(EXE):
        print(f"先编 C++：engine/build-one.bat vigil_engine.cpp vigil-engine.exe")
        return 2
    limit = 0
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    files = sorted(glob.glob(os.path.expanduser(ds.SESSION_GLOB)),
                   key=os.path.getmtime, reverse=True)
    if limit:
        files = files[:limit]
    if not files:
        print("本机没有 dsh 会话文件，无从对照")
        return 2

    ts = ds.now()
    bad = 0
    checked = 0
    for path in files:
        try:
            mtime = os.path.getmtime(path)
            sess = ds.read_session(path)
            if sess.get("error"):
                continue
            want = ds.classify(sess, mtime, ts)
            got = (cpp_verdict_file(path, mtime, ts) if FILE_MODE
                   else cpp_verdict([ln for ln in ds._read_bytes(path).split(b"\n") if ln.strip()],
                                    mtime, ts))
        except Exception as exc:
            print(f"  ✗ {os.path.basename(os.path.dirname(path))}: 跑不动 {type(exc).__name__}: {exc}")
            bad += 1
            continue
        diffs = []
        for k in FIELDS:
            if want.get(k) != got.get(k):
                diffs.append(f"{k}: py={want.get(k)!r} cpp={got.get(k)!r}")
        # age 只比整数秒：两边取整时刻差几毫秒是正常的
        if abs(float(want.get("age_sec") or 0) - float(got.get("age_sec") or 0)) > 1.0:
            diffs.append(f"age_sec: py={want.get('age_sec')} cpp={got.get('age_sec')}")
        wp, gp = want.get("pending"), got.get("pending")
        if bool(wp) != bool(gp):
            diffs.append(f"pending 有无: py={bool(wp)} cpp={bool(gp)}")
        elif wp and gp:
            for k in ("kind", "tool", "text"):
                if (wp.get(k) or "") != (gp.get(k) or ""):
                    diffs.append(f"pending.{k}: py={wp.get(k)!r} cpp={gp.get(k)!r}")
            if list(wp.get("options") or []) != list(gp.get("options") or []):
                diffs.append(f"pending.options: py={wp.get('options')} cpp={gp.get('options')}")
        checked += 1
        if diffs:
            bad += 1
            print(f"  ✗ {want.get('state'):<13} {path.split(os.sep)[-3]}: " + " | ".join(diffs[:4]))
        else:
            print(f"  ✓ {want.get('state'):<13} {want.get('label')} "
                  f"T{want.get('turn')}/S{want.get('step')} {os.path.basename(os.path.dirname(path))}")

    print(f"\n对照 {checked} 个会话，一致 {checked - bad}，不一致 {bad}")
    return 0 if bad == 0 and checked else 1


if __name__ == "__main__":
    raise SystemExit(main())
