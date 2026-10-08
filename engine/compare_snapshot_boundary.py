"""阈值与取整边界：拿**造出来的**会话对照，不靠本机数据碰运气。

为什么还要这一个：compare_snapshot 比的是真实会话，而真实会话年龄的小数部分是
随机的 —— 把"C++ 忘了先把 age 舍入到 1 位小数"改回去，59 个会话可能一个都不跨
整数边界，对照器照样全绿。这件事真发生过：变异测试注入 Round1→恒等，四个对照器
全绿，红的是上一次构建的二进制没被换掉那件事（已由 engine/_exe.py 堵住）。
根因还有一层：**发出去的 age_sec 两边都用 %.1f 格式化**，所以发射路径本身
看不出差别；差别只活在两个地方 ——
  ① 阈值比较读的是舍入前还是舍入后的值（recent / 主会话降级那两条），
  ② 文案里的 int(age_sec)：159518.96 在 Python 是 159519（先 round 再 int），
     直接截断原始值得 159518。
这两处都要**已知精确年龄**的会话才测得到，所以这里自己搭一个小会话库，
年龄挑在边界上：300.04 跨 ACTIVE_WINDOW=300，30.999999 与 159518.96 跨整数，
1.25 钉住 Python round(x,1) 的 banker's rounding 点。

会话是压缩过的记录，与真会话同格式（每条记录一个独立 zstd 帧），所以这同时也是
"解压 → 解析 → 判定 → 组装"的端到端对照。

用法（在仓库根）:  python engine/compare_snapshot_boundary.py
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
from compare_snapshot import walk  # noqa: E402

EXE = _exe.require_exe()

# (相对 mtime 的年龄, 挑这个数的理由)
CASES = [
    (300.02, "ACTIVE_WINDOW=300：舍入后 300.0 不降级，用原始值 300.02 就降级成待命"),
    (300.04, "同上一条，离边界再远一点"),
    (30.999999, "文案 int(age_sec) 跨整数：该是 31，截原始值得 30"),
    (159518.96, "同上一条，大数值那一侧"),
    (1.25, ".x5 舍入点：Python 的 round(x,1) 往偶数取，两边都得 1.2"),
    (0.05, ".x5 舍入点的另一侧：0.05 精确落在中点上，取 0.0 还是 0.1 说得清谁在舍入"),
    (120.02, "控制组：STALL_WINDOW 在判定内部，两边都用**原始**年龄，谁也不许先舍入"),
]

RECS = [
    {"type": "session", "id": "sess-0001", "cwd": os.path.join("C:", "work", "proj-chat"),
     "createdAt": 1700000000, "agentPreset": "chat"},
    {"type": "turn/start", "data": {"turn": 1, "step": 1}},
]


def make_corpus(tmp: str, mtime: float) -> tuple[str, str]:
    """在 tmp 下摆一个只有一个会话的库，返回 (给 Python 的 glob, 会话文件路径)。

    C++ 侧不接 glob —— 它的根固定是 HomeDir()\\.dsh\\sessions，所以同一个 tmp
    目录通过 USERPROFILE 传给它（见 HomeDir：USERPROFILE 优先于 HOME，与
    ntpath.expanduser 同序）。两边目录形状必须一致，否则测的是两套查找规则。
    """
    d = os.path.join(tmp, ".dsh", "sessions", "proj-chat", "sess-0001")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "session.v4.jsonl.zstd")
    with open(path, "wb") as fh:
        for r in RECS:
            fh.write(zstd.compress((json.dumps(r, ensure_ascii=False) + "\n").encode("utf-8")))
    os.utime(path, (mtime, mtime))
    glob_ = os.path.join(tmp, ".dsh", "sessions", "*", "*", "session.v4.jsonl.zstd")
    return glob_, path


def cpp_snapshot(tmp: str, ts: float) -> dict:
    env = dict(os.environ)
    env["USERPROFILE"] = tmp
    env["HOME"] = tmp
    p = subprocess.run([EXE, "--snapshot", repr(ts), "--no-balance"],
                       capture_output=True, timeout=180, env=env, cwd=ROOT)
    if p.returncode != 0:
        raise RuntimeError(f"退出码 {p.returncode}: {p.stderr[:400]!r}")
    return json.loads(p.stdout.decode("utf-8"))


def main() -> int:
    fails: list[str] = []
    for age, why in CASES:
        tmp = tempfile.mkdtemp(prefix="vigil-boundary-")
        try:
            wall = time.time()
            glob_, path = make_corpus(tmp, wall)
            # ts 从**读回来的** mtime 算，不从写进去的那个变量算：utime 会把浮点秒
            # 量化到 100ns，用写入前的变量得到的年龄就不是精确的 .x5 中点，
            # 而那两个中点正是拿来钉"谁在什么时候舍入"的。
            # 这也顺带钉住了 mtime 的换算公式：C++ 那边只要和 CPython 的
            # _PyTime_ns_to_double 差一个 ulp（2.4e-7 秒），这两个中点就会翻到
            # 对面去 —— 实测把 FileMtime 改成 (double)ticks/1e7，60 次里 8 次红。
            mtime = os.path.getmtime(path)
            ts = mtime + age
            py = ds.snapshot(glob_, ts, want_balance=False)
            cpp = cpp_snapshot(tmp, ts)
            print(f"  · age={age!r:<14} py: state={py['state']:<8} recent={len(py['recent'])} "
                  f"tip={str(py.get('tooltip'))[-14:]!r}")
            print(f"{'':<18} cpp: state={cpp['state']:<8} recent={len(cpp['recent'])} "
                  f"tip={str(cpp.get('tooltip'))[-14:]!r}")
            if not py["sessions"] or not cpp["sessions"]:
                # 一个会话都没扫到就整场戏演不下去 —— 那是合成库没被认，不是引擎判定不同
                fails.append(f"age={age!r} 没扫到会话 py={len(py['sessions'])} "
                             f"cpp={len(cpp['sessions'])}（{path}）")
            for d in walk(py, cpp)[:6]:
                fails.append(f"age={age!r}（{why}）{d}")
        except Exception as exc:
            fails.append(f"age={age!r} 跑不动 {type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    for f in fails:
        print("  ✗ " + f)
    if fails:
        print(f"\n{len(fails)} 处边界差异")
        return 1
    print(f"\n✓ {len(CASES)} 个边界年龄整帧一致（recent / 主会话降级 / 文案取整 / 一位小数舍入）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
