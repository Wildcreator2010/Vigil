r"""坏 JSON 的行级取舍：C++ 的解析器必须和 json.loads 一样"整行丢掉"。

为什么单独立一个：会话是**正在被写**的文件，最后一行经常是半截的；
`_iter_records` 的契约就是"解析不了的那条丢掉，其余照算"。C++ 的解析器一旦
比 Python 宽松（把 `tru` 当 true、把值后面还有东西收下、把未知转义当字面量），
两边的记录条数就分叉 —— 表现出来是 turn/step/last_tool 差一格，而不是报错。
这类差异靠真实会话碰不出来（真数据都是合法的），只能自己造。

判据走的是能替下 CPython 的那条完整通路：把坏行压成会话文件（每条一个独立
zstd 帧，与真会话同格式），两边各自读文件、各自解压、各自判定，再逐字段比。

每个用例配一条"合法对照行"，并且先要求它**真的能改变判定**。上一版把未知转义
放在 cwd 里，而 cwd 不进 classify 的输出 —— 把解析器改回宽松照样全绿，
测的是空气。下面的"可观察"断言就是用来堵这个的。

用法（在仓库根）:  python engine/compare_json.py
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

GOOD = [
    '{"type":"session","id":"sess-json","cwd":"C:\\\\work\\\\proj-json",'
    '"createdAt":1700000000,"agentPreset":"chat"}',
]

# (坏行, 同一位置的合法行)：坏行该整条丢掉，合法行该收下并且改变判定
#
# 字面量那三条故意在坏词后面多塞一个字符。原来写成 `{"turn":tru}`，把解析器
# 改回偷懒的 `i += 4` 都测不出红 —— 4 个字符恰好把右花括号也吞掉，整行照样
# 解不动、照样丢弃，两边的结论碰巧一致（空跑）。现在 `trux` 被吞掉之后结构
# 还是完整的，宽松解析会**收下**这条记录，last_event 与 turn 就露出来了。
CASES = [
    ('{"type":"turn/start","data":{"tool":"kk","turn":trux}}',
     '{"type":"turn/start","data":{"tool":"kk","turn":true}}'),
    ('{"type":"turn/start","data":{"tool":"kk","turn":falsx}}',
     '{"type":"turn/start","data":{"tool":"kk","turn":false}}'),
    ('{"type":"turn/start","data":{"tool":"kk","turn":nullx}}',
     '{"type":"turn/start","data":{"tool":"kk","turn":null}}'),
    ('{"type":"turn/start","data":{"turn":01}}',
     '{"type":"turn/start","data":{"turn":1}}'),
    ('{"type":"turn/start","data":{"turn":-}}',
     '{"type":"turn/start","data":{"turn":-1}}'),
    ('{"type":"turn/start","data":{"turn":1e}}',
     '{"type":"turn/start","data":{"turn":1.0}}'),
    ('{"type":"turn/start","data":{"turn":+5}}',
     '{"type":"turn/start","data":{"turn":5}}'),
    ('{"type":"turn/start","data":{"turn":1.2.3}}',
     '{"type":"turn/start","data":{"turn":1.2}}'),
    ('{"type":"turn/start","data":{"tool":"a\\qb"}}',          # 未知转义
     '{"type":"turn/start","data":{"tool":"aqb"}}'),
    ('{"type":"turn/start","data":{"tool":"a\\u12"}}',         # \u 不足 4 位
     '{"type":"turn/start","data":{"tool":"a12"}}'),
    ('{"type":"turn/start","data":{"tool":"a\\uZZZZ"}}',       # \u 不是十六进制
     '{"type":"turn/start","data":{"tool":"aZZZZ"}}'),
    ('{"type":"turn/start","data":{"tool":"a\tb"}}',           # 裸控制字符
     '{"type":"turn/start","data":{"tool":"ab"}}'),
    ('{"type":"turn/start","data":{"tool":"未闭合',            # 引号没关上
     '{"type":"turn/start","data":{"tool":"未闭合"}}'),
    ('{"type":"turn/start","data":{"tool":"x"}} 尾巴',         # 值后面还有东西
     '{"type":"turn/start","data":{"tool":"x"}}'),
    ('{"type":"turn/start","data":{"tool":"y"}},',             # 同上，逗号收尾
     '{"type":"turn/start","data":{"tool":"y"}}'),
    ("""{"type":"turn/start","data":{"tool":"z"}""",    # 少一个右花括号
     '{"type":"turn/start","data":{"tool":"z"}}'),
]

FIELDS = ("state", "turn", "step", "last_event", "last_tool", "end_reason")


def verdict(lines: list[str]) -> tuple[dict, dict, int]:
    """把 lines 压成一个会话文件，返回 (Python 判定, C++ 判定, 认下的记录条数)。"""
    tmp = tempfile.mkdtemp(prefix="vigil-json-")
    try:
        d = os.path.join(tmp, ".dsh", "sessions", "proj-json", "sess-json")
        os.makedirs(d)
        path = os.path.join(d, "session.v4.jsonl.zstd")
        with open(path, "wb") as fh:
            for ln in lines:
                fh.write(zstd.compress((ln + "\n").encode("utf-8")))
        mtime = os.path.getmtime(path)
        ts = time.time()
        sess = ds.read_session(path)
        want = ds.classify(sess, mtime, ts)
        p = subprocess.run([EXE, "--session", path, repr(mtime), repr(ts)],
                           capture_output=True, timeout=60, cwd=ROOT)
        if p.returncode != 0:
            raise RuntimeError(f"C++ 退出码 {p.returncode}: {p.stderr[:200]!r}")
        got = json.loads(p.stdout.decode("utf-8"))
        return want, got, sess.get("records", 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def diff_fields(a: dict, b: dict) -> list[str]:
    return [f"{k}: {a.get(k)!r} vs {b.get(k)!r}" for k in FIELDS if a.get(k) != b.get(k)]


def main() -> int:
    fails: list[str] = []

    base_want, base_got, _ = verdict(GOOD)
    d0 = diff_fields(base_want, base_got)
    if d0:
        # 干净语料都对不上，后面每条差异都不再是"坏行取舍"的问题
        print("✗ 基线就不一致：" + " | ".join(d0))
        return 1

    for bad, good in CASES:
        try:
            bw, bg, nrec = verdict(GOOD + [bad])
            gw, _, _ = verdict(GOOD + [good])
        except Exception as exc:
            fails.append(f"{bad[:44]!r}: 跑不动 {type(exc).__name__}: {exc}")
            continue
        # 1) 两边的判定要一致：要么都丢，要么都收且收成同一个结果
        for msg in diff_fields(bw, bg):
            fails.append(f"{bad[:44]!r}（认下 {nrec} 条）{msg}")
        # 2) 这个位置必须有人看：合法对照行要能改变判定。不然第 1 条比的是空气
        #    —— 上一版就是这么把空用例混过去的（坏词被吞掉后整行仍解不动，
        #    两边都"丢掉"，改宽松改严格都测不出红）。
        if not diff_fields(base_want, gw):
            fails.append(f"{bad[:44]!r}: 对照行没有可观察效果，用例是空跑")

    # 一整锅坏行：一条都不该剩下
    allbad = [c[0] for c in CASES]
    aw, ag, nrec = verdict(allbad)
    for msg in diff_fields(aw, ag):
        fails.append("全是坏行：" + msg)
    if nrec != 0:
        fails.append(f"全是坏行：Python 仍认下 {nrec} 条，用例没造对")

    # 合法的非 ASCII（含代理对）两边都要收下且解出同一个字符串
    w3, g3, _ = verdict(GOOD + ['{"type":"turn/start","data":{"turn":3,'
                                '"tool":"\\u00e9\\ud83d\\ude00"}}'])
    if diff_fields(w3, g3):
        fails.append("非 ASCII：" + " | ".join(diff_fields(w3, g3)))

    for f in fails:
        print("  ✗ " + f)
    if fails:
        print(f"\n{len(fails)} 处不一致")
        return 1
    print(f"✓ {len(CASES)} 种坏行两边同样处置，对照行与非 ASCII 同样收下且可观察")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
