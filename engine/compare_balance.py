"""RED→GREEN：C++ 侧的余额 Key 存取，与 Python 双向互操作。

为什么重点放在互操作而不是 HTTP：api.deepseek.com 通不通、Key 有没有权限都不是这次
改动引入的东西；而 **"C++ 存的 Key Python 读得到、Python 存的 C++ 读得到"** 是切换
引擎时唯一会静默坏掉的地方 —— 坏了的表现是面板显示"来源 none、余额不可用"，
而保存那一步照样退出 0，用户只看到"存了但没用"。

四件事，每件都是"改了产品哪一处就该红"：
① C++ 保存 → 落盘非明文 → Python 的 balance_key() 读回同一把，来源 dpapi
② Python 保存 → C++ --balance-probe 报同一把的长度与来源 dpapi
③ C++ 清除 → 文件消失；再清一次输出"没有已保存的 Key"且退出码 0（与 Python 同文案）
④ DPAPI 失败时的明文回退落在 state_dir()/balance.key，两边都读得到
   （③④ 合起来才是"读写对称"：Python 侧那个不对称是今天刚修的，见 15d003b）

probe 只报长度不报 Key —— 面板那套"只显示来源、不显示 Key 本身"的口径不能因为
多了个诊断开关就漏出去。

用法: python engine/compare_balance.py
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import dsh_state as ds          # noqa: E402

EXE = os.path.join(HERE, "vigil-engine.exe")
PROBE_A = "CPP-WROTE-NOT-A-REAL-KEY-1111"
PROBE_B = "PY-WROTE-NOT-A-REAL-KEY-2222"


def run(args, stdin=None):
    return subprocess.run([EXE, *args], input=stdin, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=60, cwd=ROOT)


def main() -> int:
    if not os.path.isfile(EXE):
        print("先编引擎：engine/build.cmd")
        return 2
    for var in ("DEEPSEEK_BALANCE_KEY", "DEEPSEEK_API_KEY"):
        os.environ.pop(var, None)              # 环境变量会盖住落盘分支
    sd = ds.state_dir()
    prot = os.path.join(sd, "balance.protected")
    plain = os.path.join(sd, "balance.key")
    backup = {p: open(p, "rb").read() for p in (prot, plain) if os.path.isfile(p)}
    fails = []
    try:
        # ① C++ 写 → Python 读
        for p in (prot, plain):
            if os.path.isfile(p):
                os.remove(p)
        r = run(["--save-balance-key"], stdin=PROBE_A)
        if r.returncode != 0 or "已保存到" not in (r.stdout or ""):
            fails.append(f"C++ --save-balance-key 没成功：退出码 {r.returncode} {(r.stdout or r.stderr)[:160]!r}")
        if os.path.isfile(prot):
            blob = open(prot, "rb").read()
            if PROBE_A.encode() in blob:
                fails.append("C++ 落盘是明文（DPAPI 没生效或没做）")
            key, source = ds.balance_key()
            if key != PROBE_A or source != "dpapi":
                fails.append(f"Python 读不回 C++ 存的 Key：{source}/{key!r}")
        else:
            fails.append("C++ 保存后没有 balance.protected")

        # ② Python 写 → C++ 读
        for p in (prot, plain):
            if os.path.isfile(p):
                os.remove(p)
        ds.save_balance_key(PROBE_B)
        if not os.path.isfile(prot):
            fails.append("Python 保存后没有 balance.protected（前置条件坏了）")
        else:
            r = run(["--balance-probe"])
            out = (r.stdout or "").strip()
            if f"source=dpapi" not in out or f"len={len(PROBE_B)}" not in out:
                fails.append(f"C++ 读不回 Python 存的 Key：{out!r} {(r.stderr or '')[:120]!r}")

        # ③ C++ 清除 + 幂等
        for p in (prot, plain):
            if os.path.isfile(p):
                os.remove(p)
        ds.save_balance_key(PROBE_B)
        r = run(["--clear-balance-key"])
        if r.returncode != 0 or os.path.isfile(prot):
            fails.append(f"C++ 清除失败：退出码 {r.returncode} 文件仍在 {os.path.isfile(prot)}")
        r2 = run(["--clear-balance-key"])
        if r2.returncode != 0 or "没有已保存的 Key" not in (r2.stdout or ""):
            fails.append(f"重复清除不幂等或文案不一致：{(r2.stdout or r2.stderr)[:160]!r}")

        # ④ 明文回退：Python 在 DPAPI 不可用时写 state_dir()/balance.key，C++ 必须也认
        for p in (prot, plain):
            if os.path.isfile(p):
                os.remove(p)
        real = ds._dpapi
        try:
            ds._dpapi = lambda data, protect=True: None
            ds.save_balance_key(PROBE_A)
        finally:
            ds._dpapi = real
        if not os.path.isfile(plain):
            fails.append("明文回退没落在 state_dir()/balance.key（前置条件变了）")
        else:
            r = run(["--balance-probe"])
            out = (r.stdout or "").strip()
            if "source=file" not in out or f"len={len(PROBE_A)}" not in out:
                fails.append(f"C++ 读不到明文回退：{out!r}")

        # ⑤ 无 Key 时快照里的 balance 形状与 Python 一致
        for p in (prot, plain):
            if os.path.isfile(p):
                os.remove(p)
        import json
        import time
        ts = time.time()
        py = ds.snapshot(ds.SESSION_GLOB, ts, want_balance=True)["balance"]
        r = run(["--snapshot", repr(ts)])
        cpp = json.loads(r.stdout)["balance"]
        for k in ("available", "source", "error"):
            if py.get(k) != cpp.get(k):
                fails.append(f"无 Key 时 balance.{k}: py={py.get(k)!r} cpp={cpp.get(k)!r}")
    finally:
        for p in (prot, plain):
            if os.path.isfile(p):
                os.remove(p)
        for p, data in backup.items():
            with open(p, "wb") as fh:
                fh.write(data)

    for f in fails:
        print("  ✗ " + f)
    if fails:
        print(f"\n{len(fails)} 条不满足")
        return 1
    print("✓ Key 存取双向互操作：C++↔Python 互读、DPAPI 失败回退两边都认、清除幂等")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
