#!/usr/bin/env python3
"""dsh_state 状态机的可复跑断言测试。

  python test_dsh_state.py            跑全部用例（合成事件，不碰网络、不写会话）
  python test_dsh_state.py --live     额外用真实 ~/.dsh 会话做冒烟校验
"""

from __future__ import annotations

import compression.zstd as zstd
import glob
import json
import os
import sys
import tempfile
import time

if sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") != "utf8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dsh_state as ds  # noqa: E402

SEQ = [0]


def make(etype: str, data: dict | None = None) -> dict:
    SEQ[0] += 1
    return {"type": etype, "seq": SEQ[0], "time": 1, "data": data or {}}


def turn_start(turn=1):
    return make("turn/start", {"turn": turn})


def step(turn=1, step=1):
    return make("step/start", {"turn": turn, "step": step})


def text_msg(turn=1, step=1, text="答案是……"):
    return make(
        "assistant/message",
        {"turn": turn, "step": step, "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}},
    )


def tool_msg(call_id, name, args="{}", turn=1, step=1):
    return make(
        "assistant/message",
        {
            "turn": turn,
            "step": step,
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "正在处理"},
                    {"type": "tool-call", "id": call_id, "name": name, "arguments": args},
                ],
            },
        },
    )


def call(call_id, name, args="{}", turn=1, step=1):
    return make("tool/call", {"turn": turn, "step": step, "callId": call_id, "name": name, "arguments": args})


def result(call_id, turn=1, step=1):
    return make("tool/result", {"turn": turn, "step": step, "callId": call_id, "content": "ok"})


def turn_end(kind, turn=1, error=None):
    reason = {"kind": "error", "error": error or {}} if kind == "error" else {"kind": kind}
    return make("turn/end", {"turn": turn, "reason": reason})


def asked(aid="a1", tool="pwsh", reason="需要提权"):
    return make("approval/asked", {"id": aid, "toolName": tool, "reason": reason})


def decided(aid="a1", outcome="allowed-once"):
    return make("approval/decided", {"id": aid, "outcome": outcome})


def sess(*events):
    return {
        "tail": list(events),
        "meta": {"id": "s1", "cwd": "C:\\proj\\Demo"},
        "title": None,
        "records": len(events),
        "usage_total": {"input": 0, "output": 0, "total": 0},
        "last_todo": None,
        "last_turn_end": next((e for e in reversed(events) if e["type"] == "turn/end"), None),
        "last_turn_start": next((e for e in reversed(events) if e["type"] == "turn/start"), None),
    }


QUESTION = json.dumps(
    {"questions": [{"id": "q1", "question": "要不要装 git？", "options": [{"label": "装"}, {"label": "先不"}]}]},
    ensure_ascii=False,
)

# (用例名, 事件序列, 距上次写入秒数, 期望状态, 期望等待类型)
SCENARIOS = [
    ("思考中：步已开始、模型未回", [turn_start(), step()], 2.0, "thinking", None),
    (
        "思考中：工具结果刚返回",
        [turn_start(), step(), tool_msg("c1", "read"), call("c1", "read"), result("c1")],
        1.0,
        "thinking",
        None,
    ),
    ("回答中：纯文本且轮次未结束", [turn_start(), step(), text_msg()], 0.5, "answering", None),
    ("执行工具：调用已发出无结果", [turn_start(), step(), tool_msg("c1", "pwsh"), call("c1", "pwsh")], 0.5, "tool_running", None),
    ("待命：completed 且过了窗口", [turn_start(), step(), text_msg(), turn_end("completed")], 120.0, "idle", None),
    ("回答完成：轮次刚结束", [turn_start(), step(), text_msg(), turn_end("completed")], 3.0, "done", None),
    ("出错：轮次 error", [turn_start(), step(), turn_end("error", error={"message": "429 rate limited"})], 3.0, "error", None),
    ("已中断：轮次 aborted", [turn_start(), step(), turn_end("aborted")], 3.0, "aborted", None),
    ("需要操作：等待授权", [turn_start(), step(), asked()], 30.0, "needs_action", "approval"),
    ("授权已处理则回到思考", [turn_start(), step(), asked(), decided(), step(1, 2)], 1.0, "thinking", None),
    (
        "需要操作：模型提问",
        [turn_start(), step(), tool_msg("c9", "ask_user_question", QUESTION), call("c9", "ask_user_question", QUESTION)],
        30.0,
        "needs_action",
        "question",
    ),
    (
        "提问已回答则回到思考",
        [turn_start(), step(), tool_msg("c9", "ask_user_question", QUESTION), call("c9", "ask_user_question", QUESTION), result("c9"), step(1, 2)],
        1.0,
        "thinking",
        None,
    ),
    ("需要操作：等待计划确认", [turn_start(), step(), tool_msg("c8", "exit_plan_mode"), call("c8", "exit_plan_mode")], 5.0, "needs_action", "question"),
    ("需要操作：轮次 blocked", [turn_start(), step(), turn_end("blocked")], 5.0, "needs_action", "blocked"),
    ("卡住：轮次开着但久无写入", [turn_start(), step()], 3000.0, "stalled", None),
    ("空会话：待命", [], 10.0, "idle", None),
]

NOW = 1000.0


def run_scenarios():
    failures = []
    for name, events, age, want_state, want_kind in SCENARIOS:
        got = ds.classify(sess(*events), NOW - age, NOW)
        kind = (got.get("pending") or {}).get("kind")
        good = got["state"] == want_state and (want_kind is None or kind == want_kind)
        mark = "✓" if good else "✗"
        print(f"{mark} {name:<28} -> {got['state']:<13} {kind or ''}")
        if not good:
            failures.append(f"  ✗ {name}: 期望 {want_state}{'/' + want_kind if want_kind else ''}，实得 {got['state']}/{kind}（最后事件 {got['last_event']}）")
    return len(SCENARIOS) - len(failures), len(SCENARIOS), failures


def run_details():
    errs = []
    got = ds.classify(sess(turn_start(3), step(3, 7)), NOW - 1, NOW)
    if (got["turn"], got["step"]) != (3, 7):
        errs.append(f"轮次解析错误: {got['turn']}/{got['step']}")

    pend = ds.classify(sess(turn_start(), call("c9", "ask_user_question", QUESTION)), NOW - 30, NOW)["pending"]
    if not pend or "要不要装 git" not in pend["text"]:
        errs.append(f"提问正文解析错误: {pend}")
    elif pend.get("options") != ["装", "先不"]:
        errs.append(f"选项解析错误: {pend.get('options')}")

    err = ds.classify(sess(turn_start(), turn_end("error", error={"message": "boom"})), NOW - 1, NOW)
    if err.get("error") != "boom":
        errs.append(f"错误信息未带出: {err}")

    todo = ds._todo_progress([{"status": "completed"}, {"status": "in_progress"}, {"status": "pending"}, {"status": "pending"}])
    if todo != {"total": 4, "done": 1, "in_progress": 1, "pending": 2}:
        errs.append(f"todo 统计错误: {todo}")

    if ds.format_money({"available": True, "currency": "CNY", "total": "110.00"}) != "¥110.00":
        errs.append("余额格式化错误")
    if ds.format_money({"available": False}) != "--":
        errs.append("无余额时应显示 --")

    # 截断帧容错：dsh 每条记录一个 zstd 帧，写到一半被读到时应保留已完成的记录
    frames = [zstd.compress(json.dumps({"type": "step/start", "seq": i, "data": {"turn": 1, "step": i}}).encode() + b"\n") for i in range(50)]
    blob = b"".join(frames)
    with tempfile.TemporaryDirectory() as td:
        good = os.path.join(td, "session.v4.jsonl.zstd")
        with open(good, "wb") as fh:
            fh.write(blob)
        n = sum(1 for _ in ds._iter_records(ds._read_bytes(good)))
        if n != 50:
            errs.append(f"整读记录数不符: {n}/50")
        half = os.path.join(td, "half.v4.jsonl.zstd")
        with open(half, "wb") as fh:
            fh.write(blob[:-25])
        p = sum(1 for _ in ds._iter_records(ds._read_bytes(half)))
        if not 40 <= p < 50:
            errs.append(f"半帧截断未正确恢复: {p}")
        single = os.path.join(td, "single.v4.jsonl.zstd")
        with open(single, "wb") as fh:
            fh.write(zstd.compress(b'{"type":"session","id":"x"}\n{"type":"turn/start","seq":2}\n'))
        if sum(1 for _ in ds._iter_records(ds._read_bytes(single))) != 2:
            errs.append("单帧整流会话读取失败")
        loaded = ds.read_session_cached(good)
        if loaded["records"] != 50:
            errs.append(f"read_session_cached 记录数错误: {loaded['records']}")
        if ds.read_session_cached(good) is not loaded:
            errs.append("同一 (大小,mtime) 未复用缓存对象")
        with open(good, "ab") as fh:
            fh.write(zstd.compress(b'{"type":"step/end","seq":99,"data":{"turn":1,"step":50}}\n'))
        if ds.read_session_cached(good) is loaded:
            errs.append("文件增长后缓存未失效")
    return errs


def run_primary():
    errs = []
    ts = NOW
    base = dict(path="p", key="k", project="P", age_sec=1.0, priority=0, state="idle")
    rows = [
        {**base, "key": "busy", "project": "Busy", "state": "thinking", "priority": ds.STATES["thinking"][3], "age_sec": 2.0},
        {**base, "key": "wait", "project": "Wait", "state": "needs_action", "priority": ds.STATES["needs_action"][3], "age_sec": 40.0, "pending": {"kind": "approval", "text": "要提权"}},
    ]
    primary, waiting = ds.pick_primary(rows, ts)
    if primary["key"] != "wait":
        errs.append(f"等待处理的会话应胜出: {primary['key']}")
    if [w["key"] for w in waiting] != ["wait"]:
        errs.append(f"待处理列表错误: {waiting}")

    old = [{**base, "key": "stale", "state": "idle", "priority": ds.STATES["idle"][3], "age_sec": 99999.0}]
    primary, waiting = ds.pick_primary(old, ts)
    if primary["key"] != "stale" or waiting:
        errs.append("全部陈旧时应回退到最新会话且无待处理")
    return errs


def run_offline():
    errs = []
    original = ds.harness_running
    ds.harness_running = lambda: False
    try:
        snap = ds.snapshot(ds.SESSION_GLOB, time.time(), want_balance=False)
        if snap["state"] != "offline":
            errs.append(f"应用未运行时应为 offline，实得 {snap['state']}")
        if snap["app_running"]:
            errs.append("app_running 未反映进程缺失")
        if "未运行" not in snap["label"]:
            errs.append(f"离线标签错误: {snap['label']}")
    finally:
        ds.harness_running = original
    return errs


def write_session(root, project, sid, records, mtime):
    d = os.path.join(root, f"--{project}--", sid)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "session.v4.jsonl.zstd")
    with open(path, "wb") as fh:
        for rec in records:
            fh.write(zstd.compress((json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")))
    os.utime(path, (mtime, mtime))
    return path


def rec(etype, data=None, seq=1):
    return {"type": etype, "seq": seq, "time": 1, "data": data or {}}


def run_e2e():
    """端到端：造临时会话目录，验证 scan → pick_primary → snapshot 的整条链路。"""
    errs = []
    ts = time.time()
    running = ds.harness_running
    ds.harness_running = lambda: True
    try:
        with tempfile.TemporaryDirectory() as td:
            glob_str = os.path.join(td, "*", "*", "session.v4.jsonl.zstd")
            head = rec("session", {"id": "s1", "cwd": "C:\\p\\Alpha"})

            write_session(td, "Alpha", "s-old-error", [
                head, rec("turn/start", {"turn": 1}), rec("step/start", {"turn": 1, "step": 1}),
                rec("turn/end", {"turn": 1, "reason": {"kind": "error", "error": {"message": "429"}}}),
            ], ts - 4000)
            snap = ds.snapshot(glob_str, ts, want_balance=False)
            if snap["state"] != "idle":
                errs.append(f"4000s 前的 error 会话不该继续报警，实得 {snap['state']}")

            write_session(td, "Beta", "s-fresh-error", [
                rec("session", {"id": "s2", "cwd": "C:\\p\\Beta"}), rec("turn/start", {"turn": 1}),
                rec("turn/end", {"turn": 1, "reason": {"kind": "error", "error": {"message": "429"}}}),
            ], ts - 5)
            snap = ds.snapshot(glob_str, ts, want_balance=False)
            if snap["state"] != "error":
                errs.append(f"刚出错的会话应报 error，实得 {snap['state']}")

            write_session(td, "Gamma", "s-pending", [
                rec("session", {"id": "s3", "cwd": "C:\\p\\Gamma"}), rec("turn/start", {"turn": 4}),
                rec("step/start", {"turn": 4, "step": 2}),
                rec("approval/asked", {"id": "a1", "toolName": "pwsh", "reason": "要联网下载"}),
            ], ts - 30)
            snap = ds.snapshot(glob_str, ts, want_balance=False)
            if snap["state"] != "needs_action" or not snap["attention"]:
                errs.append(f"等待授权应报 needs_action，实得 {snap['state']} attention={snap['attention']}")
            if "Gamma" not in snap["strip_left"]:
                errs.append(f"状态条应指向等待处理的会话: {snap['strip_left']}")
            if snap["session"].get("pending", {}).get("text") != "要联网下载":
                errs.append(f"待处理正文丢失: {snap['session']}")

            write_session(td, "Delta", "s-thinking", [
                rec("session", {"id": "s5", "cwd": "C:\\p\\Delta"}), rec("turn/start", {"turn": 2}),
                rec("step/start", {"turn": 2, "step": 1}),
            ], ts - 2)
            snap = ds.snapshot(glob_str, ts, want_balance=False)
            if snap["state"] != "needs_action":
                errs.append(f"同时有活跃与待处理时，应优先提示待处理，实得 {snap['state']}")
            if "Gamma" not in [w.get("project") for w in snap["waiting"]] and "Gamma" not in snap["strip_left"]:
                errs.append(f"待处理会话未出现在 waiting 或状态条里: {snap['waiting']}")

        with tempfile.TemporaryDirectory() as td2:
            glob2 = os.path.join(td2, "*", "*", "session.v4.jsonl.zstd")
            write_session(td2, "Old", "s-ancient-wait", [
                rec("session", {"id": "s4", "cwd": "C:\\p\\Old"}), rec("turn/start", {"turn": 1}),
                rec("approval/asked", {"id": "a9", "toolName": "pwsh", "reason": "很久以前"}),
            ], ts - 3 * 86400)
            snap = ds.snapshot(glob2, ts, want_balance=False)
            if snap["state"] != "idle":
                errs.append(f"三天前放弃的待授权会话应降级为待命，实得 {snap['state']}")
    finally:
        ds.harness_running = running
    return errs


# ---- 提问型待处理会话的 golden fixture（由 run_sessions_field 使用）----
# 概览页要显示「正文 + 选项」，而 pending.options 是白名单里唯一有意不设上限的字段，
# 它存在的唯一理由就是把模型给的 label 一字不差送到界面上。所以这里的 label 特意做成
# 多条、长短悬殊（3~86 字，最长那条越过 title 的 80 字截断线）、含中文且含空格，
# 并且首字打乱成**非字典序**——「截断 / 去重 / 排序 / 只取第一条」任何改动都会报红。
# 仓内既有 fixture 全是 approval 型（源头本就不带 options），只有这条能覆盖真选项链路。
QST_KEY = "s-qst"  # write_session 的会话目录名，即 sessions 行里的 key
QST_PROJECT = "QST"
QST_QUESTION = "要不要 只改 固件，协议 与 服务端 先不动？"
QST_LABELS = [
    "先不动",
    "两套都改：协议、服务端、控制台、面板与全部测试一起动，这一条特意写得很长，"
    "长到足以越过 title 的 80 字截断线，任何按长度截断 options 的改动都会在这里报红",
    "按 Enter 继续 等待",
    "保 留 现 有 选 项",
    "删掉重来",
]
QST_ASK_COUNT = 2  # 多问：引擎按现有行为在正文后追加「（共 N 问）」
QST_TEXT = f"{QST_QUESTION}（共 {QST_ASK_COUNT} 问）"
QST_ARGUMENTS = json.dumps(
    {
        "questions": [
            {"id": "q1", "question": QST_QUESTION,
             "options": [{"label": x} for x in QST_LABELS]},
            {"id": "q2", "question": "第二问：要不要顺手补测试？"},
        ]
    },
    ensure_ascii=False,
)


def run_sessions_field():
    """快照的 sessions 数组：上限、字段齐全、字符串截断，且不破坏 recent/waiting。"""
    errs = []
    ts = time.time()
    running = ds.harness_running
    ds.harness_running = lambda: True
    try:
        with tempfile.TemporaryDirectory() as td:
            g = os.path.join(td, "*", "*", "session.v4.jsonl.zstd")
            for i in range(70):
                write_session(td, f"Proj{i}", f"s{i:02d}", [
                    rec("session", {"id": f"s{i}", "cwd": f"C:\\p\\Proj{i}"}),
                    rec("session/title", {"title": "很长的标题" * 30}),
                    rec("turn/start", {"turn": 3}),
                    rec("approval/asked", {"id": "a", "toolName": "pwsh", "reason": "问" * 300}),
                ], ts - i)
            # 与 70 条 approval 型混排的同一条 ask_user_question 型待处理会话：
            # mtime 取 ts-0.5，保证它落在 60 行窗口内（第二新），且 age 仍可判序。
            write_session(td, QST_PROJECT, QST_KEY, [
                sess_head(QST_KEY, QST_PROJECT),
                rec("turn/start", {"turn": 6}),
                rec("step/start", {"turn": 6, "step": 2}),
                rec("tool/call", {"turn": 6, "step": 2, "callId": "cq",
                                  "name": "ask_user_question", "arguments": QST_ARGUMENTS}),
            ], ts - 0.5)
            snap = ds.snapshot(g, ts, want_balance=False)
            rows = snap.get("sessions")
            if not isinstance(rows, list):
                errs.append(f"sessions 缺失或类型错: {type(rows)}")
                return errs
            if len(rows) != ds.SESSIONS_IN_SNAPSHOT:
                errs.append(f"sessions 未截到上限: {len(rows)}/{ds.SESSIONS_IN_SNAPSHOT}")
            need = {"key", "project", "title", "state", "turn", "step", "age_sec",
                    "last_event", "last_tool", "end_reason", "records", "todo",
                    "usage_total", "pending"}
            for r in rows[:3]:
                miss = need - set(r)
                if miss:
                    errs.append(f"sessions 字段缺失: {sorted(miss)}")
            long_titles = [r["title"] for r in rows if r.get("title")]
            if not all(isinstance((r.get("pending") or {}).get("options"), list)
                       for r in rows if r.get("pending")):
                errs.append("pending.options 缺失或类型错：概览页要就地显示选项")
            if any("path" in r for r in rows):
                errs.append("sessions 不应携带 path：无消费者，且是每帧最大的冗余项")
            # —— pending.options 的入库 golden：概览页要「正文 + 选项」，这里逐字钉死 ——
            qrow = next((r for r in rows if r["key"] == QST_KEY), None)
            if qrow is None:
                errs.append(f"sessions 里找不到提问型会话 {QST_KEY}（fixture 或 60 行窗口变了）")
            else:
                qpend = qrow.get("pending") or {}
                if qrow["state"] != "needs_action" or qpend.get("kind") != "question":
                    errs.append(
                        "提问型会话判错：应为 needs_action/question，"
                        f"实得 {qrow['state']}/{qpend.get('kind')}"
                    )
                got = qpend.get("options")
                if got != QST_LABELS:
                    bad = "；".join(
                        f"[{i}] 实得 {(got[i] if isinstance(got, list) and i < len(got) else None)!r} "
                        f"应为 {w!r}"
                        for i, w in enumerate(QST_LABELS)
                        if not isinstance(got, list) or i >= len(got) or got[i] != w
                    )
                    errs.append(
                        "pending.options 不是逐字按序透传（截断/去重/排序都会栽在这里）："
                        f"条数 {got if not isinstance(got, list) else len(got)}/{len(QST_LABELS)}；{bad}"
                    )
                if qpend.get("text") != QST_TEXT:
                    errs.append(
                        f"pending.text 丢了多问提示：实得 {qpend.get('text')!r} 应为 {QST_TEXT!r}"
                    )
            if not long_titles or max(len(t) for t in long_titles) > 80:
                errs.append("title 未截断到 80")
            texts = [(r.get("pending") or {}).get("text") or "" for r in rows]
            if not max((len(t) for t in texts), default=0) <= 120:
                errs.append("pending.text 未截断到 120")
            ages = [r["age_sec"] for r in rows]
            if ages != sorted(ages):
                errs.append("sessions 未按静默时长升序（即 mtime 倒序）")
            for k in ("recent", "waiting"):
                if k not in snap:
                    errs.append(f"回归：{k} 字段丢失")
            json.dumps(snap, ensure_ascii=False)
    finally:
        ds.harness_running = running
    return errs


PIN_TS = 1_800_000_000.0

# recent / waiting 被 C# 的滚轮循环 Cycle() 消费，Task 2 只许新增 sessions，不许动这两个字段。
# 下面的期望值不是手推的，是从改动前的引擎（git HEAD 的 dsh_state.py）对同一 fixture
# 跑出来的真实输出，逐字钉住；连键名一起钉，因为 C# 侧按 JsonPropertyName 反序列化。
PIN_RECENT = [
    ("DONE1", "done", 5.0),
    ("TOOL1", "tool_running", 7.0),
    ("W01", "needs_action", 10.0),
    ("W02", "needs_action", 20.0),
    ("W03", "needs_action", 30.0),
    ("W04", "needs_action", 40.0),
]
PIN_WAITING = [
    ("W02", "授权 2：请确认是否放行这条 pwsh 命令", 20.0),
    ("W03", "授权 3：请确认是否放行这条 pwsh 命令", 30.0),
    ("W04", "授权 4：请确认是否放行这条 pwsh 命令", 40.0),
    ("W05", "授权 5：请确认是否放行这条 pwsh 命令", 50.0),
    ("W06", "授权 6：请确认是否放行这条 pwsh 命令", 60.0),
    ("W07", "授权 7：请确认是否放行这条 pwsh 命令", 70.0),
    ("W08", "授权 8：请确认是否放行这条 pwsh 命令", 80.0),
    ("W09", "授权 9：请确认是否放行这条 pwsh 命令", 90.0),
]


def sess_head(sid, proj):
    """真实 dsh 的 session 头记录把 id/cwd 放在顶层，read_session 读的也是顶层。"""
    return {"type": "session", "seq": 1, "time": 1, "id": sid, "cwd": "C:\\p\\" + proj}


def run_recent_waiting_pin():
    """钉住 recent/waiting 的确切内容与既有上限（6 / 8），防 sessions 的改动外溢。

    fixture 特意铺 14 个会话：recent 有 11 个候选（截到 6）、waiting 有 10 个候选
    （去掉主会话后截到 8）、外加一个超过 2 小时的待授权（须被排除）。
    """
    errs = []
    running = ds.harness_running
    ds.harness_running = lambda: True
    try:
        with tempfile.TemporaryDirectory() as td:
            g = os.path.join(td, "*", "*", "session.v4.jsonl.zstd")
            for k in range(1, 12):
                write_session(td, f"W{k:02d}", f"s-w{k:02d}", [
                    sess_head(f"s{k}", f"W{k:02d}"),
                    rec("turn/start", {"turn": k}),
                    rec("approval/asked", {"id": f"a{k}", "toolName": "pwsh",
                                           "reason": f"授权 {k}：请确认是否放行这条 pwsh 命令"}),
                ], PIN_TS - k * 10)
            write_session(td, "OLD", "s-old", [
                sess_head("sold", "OLD"), rec("turn/start", {"turn": 1}),
                rec("approval/asked", {"id": "az", "toolName": "pwsh", "reason": "四天前"}),
            ], PIN_TS - 4 * 86400)
            write_session(td, "DONE1", "s-done", [
                sess_head("sd", "DONE1"), rec("turn/start", {"turn": 1}),
                rec("turn/end", {"turn": 1, "reason": {"kind": "completed"}}),
            ], PIN_TS - 5)
            write_session(td, "TOOL1", "s-tool", [
                sess_head("st", "TOOL1"), rec("turn/start", {"turn": 2}),
                rec("step/start", {"turn": 2, "step": 3}),
                rec("tool/call", {"turn": 2, "step": 3, "callId": "c1", "name": "grep"}),
            ], PIN_TS - 7)
            snap = ds.snapshot(g, PIN_TS, want_balance=False)
            want_recent = [{"project": p, "state": s, "age_sec": a} for p, s, a in PIN_RECENT]
            want_waiting = [{"project": p, "text": t, "age_sec": a, "key": f"s-w{p[1:]}"}
                            for p, t, a in PIN_WAITING]
            if snap["recent"] != want_recent:
                errs.append(f"回归：recent 变了\n     实得 {snap['recent']}\n     应为 {want_recent}")
            if snap["waiting"] != want_waiting:
                errs.append(f"回归：waiting 变了\n     实得 {snap['waiting']}\n     应为 {want_waiting}")
            if len(snap["recent"]) > 6 or len(snap["waiting"]) > 8:
                errs.append(f"回归：recent/waiting 超上限 {len(snap['recent'])}/{len(snap['waiting'])}")
            if snap["state"] != "needs_action" or snap["session"].get("project") != "W01":
                errs.append(f"回归：主会话选择变了 {snap['state']}/{snap['session'].get('project')}")
            if any(w["project"] == "OLD" for w in snap["waiting"]):
                errs.append("回归：四小时前的待授权会话又冒出来了")
    finally:
        ds.harness_running = running
    return errs


def run_live():
    errs = []
    files = glob.glob(os.path.expanduser(ds.SESSION_GLOB))
    if not files:
        print("  （跳过：没有真实会话文件）")
        return errs
    ts = time.time()
    t0 = time.perf_counter()
    dist = {}
    for p in files:
        info = ds.classify(ds.read_session(p), os.path.getmtime(p), ts)
        dist[info["state"]] = dist.get(info["state"], 0) + 1
    ms = (time.perf_counter() - t0) * 1000
    print(f"  真实会话 {len(files)} 个，耗时 {ms:.0f}ms，分布 {dist}")
    ds.scan(ds.SESSION_GLOB, ts)
    t1 = time.perf_counter()
    rows = ds.scan(ds.SESSION_GLOB, ts + 1)
    ms2 = (time.perf_counter() - t1) * 1000
    print(f"  缓存命中后单轮扫描 {ms2:.1f}ms（{len(rows)} 个会话）")
    if ms2 > 120:
        errs.append(f"缓存未生效，单轮扫描 {ms2:.0f}ms")
    if "unknown" in dist:
        errs.append("真实会话被判为 unknown")
    if ms > 8000:
        errs.append(f"全量扫描过慢: {ms:.0f}ms")

    snap = ds.snapshot(ds.SESSION_GLOB, ts, want_balance=False)
    for key in ("state", "label", "color", "glyph", "detail", "strip", "tooltip", "balance", "sessions_scanned", "app_running"):
        if key not in snap:
            errs.append(f"快照缺少字段 {key}")
    if len(snap["tooltip"]) > 63:
        errs.append(f"tooltip 超过 63 字符（NotifyIcon 上限）: {len(snap['tooltip'])}")
    if not snap["tip_lines"]:
        errs.append("tip_lines 为空")
    if snap["state"] not in ds.STATES:
        errs.append(f"非法状态: {snap['state']}")
    json.dumps(snap, ensure_ascii=False)
    print(f"  快照: {snap['state']:<13} {snap['strip']}")
    return errs


def main() -> int:
    print("== 状态判定用例 ==")
    passed, total, failures = run_scenarios()
    print("\n== 解析细节 ==")
    errs = run_details()
    errs += run_primary()
    errs += run_offline()
    print("== 端到端（临时会话目录）==")
    e2e = run_e2e()
    if not e2e:
        print("✓ 陈旧降级 / 实时报错 / 待授权优先 / 陈旧待授权 通过")
    errs += e2e
    se = run_sessions_field()
    if not se:
        print("✓ sessions 契约（上限/字段/截断/排序/无回归）通过")
    errs += se
    print("== recent/waiting 回归钉 ==")
    pw = run_recent_waiting_pin()
    if not pw:
        print("✓ recent/waiting 内容与上限与基线逐字一致")
    errs += pw
    if not errs:
        print("✓ 轮次/提问/错误/todo/余额/截断/主会话选择/离线 断言通过")
    if "--live" in sys.argv:
        print("\n== 真实数据冒烟 ==")
        errs += run_live()

    print()
    for f in failures:
        print(f)
    for e in errs:
        print(f"  ✗ {e}")
    if failures or errs:
        print(f"结果：{passed}/{total} 用例通过，{len(errs)} 个断言失败")
        return 1
    print(f"结果：全部通过（{passed} 个状态用例 + 解析断言）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
