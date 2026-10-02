#!/usr/bin/env python3
"""DeepSeek Harness (dsh) 状态检测 + DeepSeek 余额查询，零第三方依赖。

用法:
  python dsh_state.py                 人类可读的单次快照
  python dsh_state.py --json          机器可读的单次快照
  python dsh_state.py --watch         每 --interval 秒输出一行 JSON（供托盘常驻读取）
  python dsh_state.py --pretty        带颜色的详细视图
  python dsh_state.py --state-only    只输出状态码
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import glob
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import deque

import compression.zstd as zstd

SESSION_GLOB = "~/.dsh/sessions/*/*/session.v4.jsonl.zstd"
DESKTOP_DATA = "~/.dsh/profiles/desktop"
APP_EXES = {"deepseek harness.exe", "dsh.exe"}
BALANCE_URL = "https://api.deepseek.com/user/balance"

ACTIVE_WINDOW = 300.0
DONE_WINDOW = 25.0
STALL_WINDOW = 120.0
WAIT_PROMOTE_WINDOW = 2 * 3600.0
STALE_ALERT_WINDOW = 24 * 3600.0
TAIL_EVENTS = 500

BALANCE_TTL = 300.0

SESSIONS_IN_SNAPSHOT = 60

BLOCKING_TOOLS = {"ask_user_question": "问题", "exit_plan_mode": "计划确认"}
ANSWER_TOOLS = set(BLOCKING_TOOLS)

# state -> (中文标签, 图标字母, #RRGGBB, 提示优先级)
STATES = {
    "needs_action": ("需要操作", "!", "#DC2626", 90),
    "error": ("出错了", "X", "#BE123C", 80),
    "stalled": ("疑似卡住", "S", "#7C3AED", 70),
    "thinking": ("正在思考", "T", "#D97706", 60),
    "tool_running": ("正在执行工具", "W", "#0D9488", 58),
    "answering": ("正在回答", "A", "#2563EB", 56),
    "done": ("回答完成", "D", "#16A34A", 40),
    "aborted": ("已中断", "K", "#64748B", 30),
    "idle": ("待命", "I", "#6B7280", 20),
    "offline": ("未运行", "O", "#9CA3AF", 10),
    "unknown": ("未知", "?", "#9CA3AF", 0),
}


def now() -> float:
    return time.time()


def expand(path: str) -> str:
    return os.path.expanduser(path)


# ---------------------------------------------------------------- 进程检测

class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


def harness_running() -> bool:
    try:
        kernel32 = ctypes.windll.kernel32
        snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
        if snap == -1 or snap == 0:
            return False
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        found = False
        try:
            if not kernel32.Process32FirstW(snap, ctypes.byref(entry)):
                return False
            while True:
                if entry.szExeFile.lower() in APP_EXES:
                    found = True
                    break
                if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                    break
        finally:
            kernel32.CloseHandle(snap)
        return found
    except Exception:
        return True


# ---------------------------------------------------------------- 会话读取

FRAME_MAGIC = b"\x28\xb5\x2f\xfd"


def _whole_stream(data: bytes) -> bytes:
    """非逐帧分块的会话流：分块读到哪算哪，末尾半帧直接丢弃。"""
    buf = bytearray()
    reader = zstd.ZstdFile(io.BytesIO(data))
    try:
        while True:
            try:
                chunk = reader.read(1 << 16)
            except (EOFError, zstd.ZstdError):
                break
            if not chunk:
                break
            buf += chunk
    finally:
        reader.close()
    return bytes(buf)


def _read_bytes(path: str) -> bytes:
    """dsh 每条 JSONL 记录压成一个独立 zstd 帧，因此逐帧解码：
    正在写入的最后一帧可能不完整，跳帧可以保留之前所有记录，也能整文件读取。"""
    with open(path, "rb") as fh:
        data = fh.read()
    if not data:
        return b""
    out = bytearray()
    pos = 0
    n = len(data)
    while pos < n:
        nxt = data.find(FRAME_MAGIC, pos + 1)
        seg = data[pos:nxt] if nxt != -1 else data[pos:]
        try:
            out += zstd.decompress(seg)
        except Exception:
            if not out:
                return _whole_stream(data)
            try:
                out += _whole_stream(data[pos:])
            except Exception:
                pass
            break
        if nxt == -1:
            break
        pos = nxt
    return bytes(out)


def _iter_records(data: bytes):
    start = 0
    n = len(data)
    while start < n:
        end = data.find(b"\n", start)
        if end == -1:
            break
        line = data[start:end]
        start = end + 1
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue


def read_session(path: str) -> dict:
    """返回一个会话的判定所需信息（尾部事件 + 元数据 + 累计用量）。"""
    meta = {}
    tail: deque[dict] = deque(maxlen=TAIL_EVENTS)
    usage_total = {"input": 0, "output": 0, "total": 0}
    records = 0
    title = None
    last_todo = None
    last_turn_end = None
    last_turn_start = None
    try:
        data = _read_bytes(path)
    except Exception as exc:
        return {"error": f"读取失败: {type(exc).__name__}", "records": 0, "tail": []}

    for ev in _iter_records(data):
        records += 1
        etype = ev.get("type")
        if etype == "session":
            meta = {
                "id": ev.get("id"),
                "cwd": ev.get("cwd"),
                "createdAt": ev.get("createdAt"),
                "agentPreset": ev.get("agentPreset"),
            }
        elif etype == "session/title":
            t = (ev.get("data") or {}).get("title")
            if t:
                title = t
        elif etype == "todo/write":
            last_todo = (ev.get("data") or {}).get("todos")
        elif etype == "turn/end":
            last_turn_end = ev
        elif etype == "turn/start":
            last_turn_start = ev
        elif etype == "assistant/attempt":
            for item in (ev.get("data") or {}).get("stream") or []:
                u = (item.get("chunk") or {}).get("usage")
                if u:
                    usage_total["input"] += int(u.get("inputTokens") or 0)
                    usage_total["output"] += int(u.get("outputTokens") or 0)
                    usage_total["total"] += int(u.get("totalTokens") or 0)
        tail.append(ev)

    return {
        "meta": meta,
        "title": title,
        "records": records,
        "tail": list(tail),
        "usage_total": usage_total,
        "last_todo": last_todo,
        "last_turn_end": last_turn_end,
        "last_turn_start": last_turn_start,
    }


# ---------------------------------------------------------------- 状态判定

def _content_blocks(ev: dict) -> list:
    return (((ev.get("data") or {}).get("message") or {}).get("content")) or []


def _call_ids(events: list, wanted: str) -> set:
    return {
        (e.get("data") or {}).get("callId")
        for e in events
        if e.get("type") == wanted and (e.get("data") or {}).get("callId")
    }


def _approval_state(events: list) -> dict | None:
    asked = {}
    for e in events:
        d = e.get("data") or {}
        if e.get("type") == "approval/asked" and d.get("id"):
            asked[d["id"]] = d
        elif e.get("type") == "approval/decided" and d.get("id"):
            asked.pop(d["id"], None)
    if not asked:
        return None
    pending = next(iter(asked.values()))
    return {
        "kind": "approval",
        "tool": pending.get("toolName"),
        "text": pending.get("reason") or f"{pending.get('toolName')} 需要授权",
    }


def _question_state(events: list) -> dict | None:
    resolved = _call_ids(events, "tool/result")
    for e in reversed(events):
        d = e.get("data") or {}
        if e.get("type") != "tool/call":
            continue
        name = d.get("name")
        if name not in BLOCKING_TOOLS or d.get("callId") in resolved:
            continue
        text = None
        try:
            args = json.loads(d.get("arguments") or "{}")
        except Exception:
            args = {}
        if name == "ask_user_question":
            qs = args.get("questions") or []
            if qs:
                first = qs[0]
                extra = f"（共 {len(qs)} 问）" if len(qs) > 1 else ""
                text = f"{first.get('question', '')}{extra}"
            elif args.get("question"):
                text = args["question"]
        return {
            "kind": "question",
            "tool": name,
            "text": (text or f"模型在等待你{BLOCKING_TOOLS[name]}").strip(),
            "options": [
                o.get("label")
                for o in ((args.get("questions") or [{}])[0].get("options") or [])
                if isinstance(o, dict) and o.get("label")
            ],
        }
    return None


def _turn_open(events: list) -> bool:
    last_start = last_end = -1
    for i, e in enumerate(events):
        if e.get("type") == "turn/start":
            last_start = i
        elif e.get("type") == "turn/end":
            last_end = i
    return last_start > last_end


def _todo_progress(todos: list | None) -> dict | None:
    if not todos:
        return None
    done = prog = pending = 0
    for t in todos:
        s = str(t.get("status") or "").lower()
        if s in ("completed", "complete", "done"):
            done += 1
        elif s in ("in_progress", "working", "active"):
            prog += 1
        else:
            pending += 1
    return {"total": len(todos), "done": done, "in_progress": prog, "pending": pending}


def _reason_kind(turn_end: dict | None) -> str:
    r = (turn_end or {}).get("data", {}).get("reason")
    if isinstance(r, dict):
        return str(r.get("kind") or "")
    return str(r or "")


def classify(sess: dict, mtime: float, ts: float) -> dict:
    """把一个会话的尾部事件映射为状态。"""
    events = sess["tail"]
    age = max(0.0, ts - mtime)
    out = {
        "age_sec": round(age, 1),
        "state": "idle",
        "pending": None,
        "turn": None,
        "step": None,
        "last_event": events[-1].get("type") if events else None,
        "last_tool": None,
        "end_reason": _reason_kind(sess["last_turn_end"]),
    }
    for e in reversed(events):
        d = e.get("data") or {}
        if out["turn"] is None and d.get("turn") is not None:
            out["turn"] = d.get("turn")
        if out["step"] is None and d.get("step") is not None:
            out["step"] = d.get("step")
        if e.get("type") == "tool/call" and out["last_tool"] is None:
            out["last_tool"] = d.get("name")
        if out["turn"] is not None and out["step"] is not None and out["last_tool"] is not None:
            break

    pending = _approval_state(events) or _question_state(events)
    if pending and _turn_open(events):
        out["state"], out["pending"] = "needs_action", pending
        return out

    if not events:
        out["state"] = "idle"
        return out

    open_turn = _turn_open(events)
    if open_turn:
        if pending:
            out["state"], out["pending"] = "needs_action", pending
        elif age > STALL_WINDOW:
            out["state"] = "stalled"
        elif out["last_event"] == "assistant/message":
            blocks = _content_blocks(events[-1])
            kinds = {b.get("type") for b in blocks}
            if kinds & {"tool-call", "input_json_delta"}:
                out["state"] = "tool_running"
            elif "text" in kinds:
                out["state"] = "answering"
            else:
                out["state"] = "thinking"
        elif out["last_event"] == "tool/call":
            out["state"] = "tool_running"
        elif out["last_event"] == "step/end" and age <= STALL_WINDOW:
            out["state"] = "thinking"
        else:
            out["state"] = "thinking"
        return out

    kind = out["end_reason"]
    if kind == "blocked":
        out["state"] = "needs_action"
        out["pending"] = _question_state(events) or {
            "kind": "blocked",
            "tool": None,
            "text": "模型已停下，等待你的指示",
        }
    elif kind == "error":
        err = ((sess["last_turn_end"] or {}).get("data", {}).get("reason") or {}).get("error") or {}
        out["state"] = "error"
        out["error"] = str(err.get("message") or "")[:180]
    elif kind == "aborted":
        out["state"] = "aborted"
    elif kind == "completed":
        out["state"] = "done" if age <= DONE_WINDOW else "idle"
    else:
        out["state"] = "idle"
    return out


# ---------------------------------------------------------------- 汇总快照

_SESSION_CACHE: dict[str, tuple[tuple[int, float], dict]] = {}
_CACHE_MAX = 40


def read_session_cached(path: str) -> dict:
    """按 (大小, mtime) 复用解析结果：常驻 watch 模式下每轮只重新解压真正变化过的会话。"""
    try:
        st = os.stat(path)
        stamp = (st.st_size, st.st_mtime)
    except OSError:
        return read_session(path)
    hit = _SESSION_CACHE.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    sess = read_session(path)
    if len(_SESSION_CACHE) > _CACHE_MAX:
        _SESSION_CACHE.clear()
    _SESSION_CACHE[path] = (stamp, sess)
    return sess


def project_name(cwd: str | None, fallback: str) -> str:
    if not cwd:
        return fallback
    name = os.path.basename(os.path.normpath(cwd))
    return name or fallback


def session_key(path: str) -> str:
    parts = os.path.normpath(path).split(os.sep)
    return parts[-2] if len(parts) >= 2 else path


def scan(sessions_glob: str, ts: float) -> list[dict]:
    rows = []
    for path in glob.glob(expand(sessions_glob)):
        try:
            st = os.stat(path)
        except OSError:
            continue
        rows.append({"path": path, "mtime": st.st_mtime, "size": st.st_size})
    rows.sort(key=lambda r: r["mtime"], reverse=True)

    out = []
    seen = {r["path"] for r in rows}
    for gone in [k for k in _SESSION_CACHE if k not in seen]:
        _SESSION_CACHE.pop(gone, None)
    for r in rows:
        sess = read_session_cached(r["path"])
        info = classify(sess, r["mtime"], ts)
        cwd = (sess.get("meta") or {}).get("cwd")
        info.update(
            {
                "path": r["path"],
                "key": session_key(r["path"]),
                "project": project_name(cwd, r["path"].split(os.sep)[-3][:28]),
                "cwd": cwd,
                "title": sess.get("title"),
                "records": sess.get("records"),
                "usage_total": sess.get("usage_total"),
                "todo": _todo_progress(sess.get("last_todo")),
            }
        )
        info["priority"] = STATES[info["state"]][3]
        out.append(info)
    return out


def pick_primary(sessions: list[dict], ts: float) -> tuple[dict | None, list[dict]]:
    """主状态取"最近在干活的那个会话"；等待你处理的会话单独列出来。"""
    waiting = [s for s in sessions if s["state"] == "needs_action" and s["age_sec"] <= WAIT_PROMOTE_WINDOW]
    recent = [s for s in sessions if s["age_sec"] <= ACTIVE_WINDOW]
    pool = recent or waiting
    if not pool:
        primary = sessions[0] if sessions else None
        return primary, waiting
    ranked = sorted(pool, key=lambda s: (-s["priority"], s["age_sec"]))
    primary = ranked[0]
    busy = [
        s
        for s in ranked[1:]
        if s["priority"] >= STATES["tool_running"][3] or s["state"] == "needs_action"
    ]
    return primary, (waiting or busy)


def _session_rows(rows: list[dict], cap: int = SESSIONS_IN_SNAPSHOT) -> list[dict]:
    """把 scan() 的结果压成面板会话表要用的定长行：字段白名单 + 行数上限 +
    title/pending.text 的字符截断。这只让「行数 × 单行」变成可算的量级，**不等于帧大小有预算**：
    pending.options 有意不截（见下方注释），60 行满额时最坏帧实测约 97KB。"""
    out = []
    for s in rows[:cap]:
        pending = s.get("pending")
        out.append({
            "key": s["key"],
            "project": s["project"],
            "title": (s.get("title") or "")[:80] or None,
            "state": s["state"],
            "turn": s.get("turn"),
            "step": s.get("step"),
            "age_sec": s["age_sec"],
            "last_event": s.get("last_event"),
            "last_tool": s.get("last_tool"),
            "end_reason": s.get("end_reason"),
            "records": s.get("records"),
            "todo": s.get("todo"),
            "usage_total": s.get("usage_total"),
            "pending": None if not pending else {
                "kind": pending.get("kind"),
                "tool": pending.get("tool"),
                "text": str(pending.get("text") or "")[:120],
                # options 有意不设长度/条数上限：裁决「不设字节预算」写在 spec §4 的约束里，
                # 「概览页就地显示并可点每个选项」是 spec §5 的页面规格（旧注释误标成 §5）。
                # 截断/去重/排序都会丢信息；代价是 60 行满额最坏帧约 97KB，已按该裁决接受。
                # 想加截断前先读 test_dsh_state.run_sessions_field() 的 QST_LABELS golden——
                # 那条断言的存在就是为了让这个决定不被无意推翻。
                # 逐项 str() 只收元素类型、不管数量：C# 侧 Task 4 的模型是 List<string>
                # （bar/StateClient.cs:25），一个真值非字符串的 label（5 / True）若原样出帧，
                # 整帧反序列化失败会让状态栏静默停更。用 str() 而不是 isinstance 过滤，
                # 是因为过滤会静默少掉一个可点选项，str() 一条都不丢。
                "options": [str(o) for o in (pending.get("options") or [])],
            },
        })
    return out


def format_money(balance: dict) -> str:
    if not balance.get("available"):
        return "--"
    cur = balance.get("currency") or "CNY"
    sym = {"CNY": "¥", "USD": "$"}.get(cur, "")
    return f"{sym}{balance.get('total') or '0'}"


def clamp_tip(text: str, limit: int = 63) -> str:
    """WinForms 的 NotifyIcon.Text 上限是 63 字符。"""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def compose_text(primary: dict | None, state: str, balance: dict, other: list[dict]) -> dict:
    label, glyph, color, _ = STATES[state]
    money = format_money(balance)
    if primary:
        left = f"{label} · {primary['project']}"
        if primary.get("turn") is not None:
            left += f" · T{primary['turn']}"
            if primary.get("step") is not None:
                left += f"/S{primary['step']}"
        detail = f"{left} · {money}"
        tip = f"{label}｜{primary['project']}"
        if primary.get("turn") is not None:
            tip += f"｜T{primary['turn']}"
        if primary.get("pending"):
            tip += f"｜{primary['pending']['text']}"
        tip += f"｜静默 {int(primary['age_sec'])}s"
        tip_lines = [f"{label}｜{primary['project']}"]
        if primary.get("title"):
            tip_lines.append(f"会话：{str(primary['title'])[:60]}")
        if primary.get("pending"):
            tip_lines.append(f"等你处理：{primary['pending']['text'][:120]}")
            if primary["pending"].get("options"):
                tip_lines.append("选项：" + " / ".join(primary["pending"]["options"]))
        if primary.get("last_tool") and state in ("tool_running", "thinking"):
            tip_lines.append(f"最近工具：{primary['last_tool']}")
        todo = primary.get("todo")
        if todo:
            tip_lines.append(
                f"任务：{todo['done']}/{todo['total']} 完成"
                + (f"，{todo['in_progress']} 进行中" if todo["in_progress"] else "")
            )
        usage = primary.get("usage_total") or {}
        if usage.get("total"):
            tip_lines.append(f"用量：入 {usage['input']:,} / 出 {usage['output']:,} tokens")
        tip_lines.append(f"已静默 {int(primary['age_sec'])}s ｜ 待处理 {len(other)} 个")
    else:
        left, detail, tip = label, f"{label} · {money}", f"{label}｜余额 {money}"
        tip_lines = [label, f"余额 {money}", "没有找到 dsh 会话记录"]
    return {
        "label": label,
        "glyph": glyph,
        "color": color,
        "detail": detail,
        "strip": detail,
        "strip_left": left,
        "strip_right": money,
        "tooltip": clamp_tip(tip),
        "tooltip_full": "\n".join(tip_lines),
        "tip_lines": tip_lines,
    }


def refresh_token() -> float:
    """托盘写 this 文件即可让常驻引擎立即重查余额。"""
    try:
        return os.path.getmtime(os.path.join(state_dir(), "refresh.token"))
    except OSError:
        return 0.0


def snapshot(
    sessions_glob: str,
    ts: float,
    want_balance: bool = True,
    balance_ttl: float | None = None,
    force_balance: bool = False,
) -> dict:
    running = harness_running()
    sessions = scan(sessions_glob, ts)
    primary, waiting = pick_primary(sessions, ts)

    if not running:
        state = "offline"
    elif primary is None:
        state = "idle"
    elif primary["age_sec"] > ACTIVE_WINDOW and primary["state"] != "needs_action":
        # 会话早已静默：陈旧的 error/done/aborted 不该一直占着托盘报警
        state = "idle"
    elif primary["state"] == "needs_action" and primary["age_sec"] > STALE_ALERT_WINDOW:
        state = "idle"
    else:
        state = primary["state"]

    balance = (
        fetch_balance(ts, force=force_balance, ttl=balance_ttl)
        if want_balance
        else {"available": False, "error": "已禁用", "skipped": True}
    )
    text = compose_text(primary if running else None, state, balance, waiting)

    return {
        "ok": True,
        "generated_at": ts,
        "app_running": running,
        "state": state,
        "label": text["label"],
        "glyph": text["glyph"],
        "color": text["color"],
        "detail": text["detail"],
        "strip": text["strip"],
        "strip_left": text["strip_left"],
        "strip_right": text["strip_right"],
        "tooltip": text["tooltip"],
        "tooltip_full": text["tooltip_full"],
        "tip_lines": text["tip_lines"],
        "attention": state in ("needs_action", "error"),
        "session": {
            k: v
            for k, v in (primary or {}).items()
            if k
            in (
                "project",
                "cwd",
                "title",
                "state",
                "turn",
                "step",
                "age_sec",
                "last_event",
                "last_tool",
                "pending",
                "end_reason",
                "error",
                "todo",
                "usage_total",
                "key",
                "records",
            )
        },
        "waiting": [
            {
                "project": s["project"],
                "text": (s.get("pending") or {}).get("text"),
                "age_sec": s["age_sec"],
                "key": s["key"],
            }
            for s in waiting
            if not primary or s["key"] != primary["key"]
        ][:8],
        "balance": balance,
        "sessions_scanned": len(sessions),
        "recent": [
            {"project": s["project"], "state": s["state"], "age_sec": s["age_sec"]}
            for s in sessions
            if s["age_sec"] <= ACTIVE_WINDOW
        ][:6],
        "sessions": _session_rows(sessions),
    }


# ---------------------------------------------------------------- 余额

def state_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or expand("~/.local/share")
    path = os.path.join(base, "dsh-status")
    os.makedirs(path, exist_ok=True)
    return path


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes | None:
    """用当前登录用户的 DPAPI 加解密，Key 不落明文。"""
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        src = ctypes.create_string_buffer(data, len(data))
        blob_in = _DataBlob(len(data), ctypes.cast(src, ctypes.POINTER(ctypes.c_char)))
        blob_out = _DataBlob()
        flags = 0x1 if protect else 0x0  # CRYPTPROTECT_UI_FORBIDDEN
        ok = (
            crypt32.CryptProtectData(ctypes.byref(blob_in), "dsh-status", None, None, None, flags, ctypes.byref(blob_out))
            if protect
            else crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, flags, ctypes.byref(blob_out))
        )
        if not ok:
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None


def save_balance_key(key: str) -> str:
    blob = _dpapi(key.encode("utf-8"), protect=True)
    if blob is None:
        path = os.path.join(state_dir(), "balance.key")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(key)
        return f"{path}（DPAPI 不可用，已存为明文，请注意文件权限）"
    path = os.path.join(state_dir(), "balance.protected")
    with open(path, "wb") as fh:
        fh.write(blob)
    return path


def _load_protected_key() -> str | None:
    path = os.path.join(state_dir(), "balance.protected")
    try:
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as fh:
            blob = fh.read()
    except OSError:
        return None
    raw = _dpapi(blob, protect=False)
    if not raw:
        return None
    return raw.decode("utf-8", "replace").strip("\x00").strip() or None


def balance_key() -> tuple[str | None, str]:
    for var in ("DEEPSEEK_BALANCE_KEY", "DEEPSEEK_API_KEY"):
        val = os.environ.get(var)
        if val and val.strip():
            return val.strip(), f"env:{var}"
    val = _load_protected_key()
    if val:
        return val, "dpapi"
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (
        os.path.join(here, "balance.key"),
        os.path.join(here, "..", "balance.key"),
        expand("~/.dsh/deepseek_balance_key"),
        expand("~/.deepseek/balance.key"),
    ):
        try:
            if os.path.isfile(cand):
                with open(cand, encoding="utf-8") as fh:
                    val = fh.read().strip()
                if val:
                    return val, f"file:{os.path.basename(cand)}"
        except OSError:
            continue
    return None, "none"


_balance_cache: dict = {"fetched_at": 0.0, "payload": None}


def fetch_balance(ts: float, force: bool = False, ttl: float | None = None) -> dict:
    ttl = BALANCE_TTL if ttl is None else ttl
    if (
        not force
        and _balance_cache["payload"] is not None
        and ts - _balance_cache["fetched_at"] < ttl
    ):
        cached = dict(_balance_cache["payload"])
        cached["cached"] = True
        return cached

    key, source = balance_key()
    payload: dict = {
        "available": False,
        "currency": None,
        "total": None,
        "granted": None,
        "topped_up": None,
        "error": None,
        "source": source,
        "fetched_at": ts,
        "cached": False,
    }
    if not key:
        payload["error"] = "未配置余额 Key"
        return _store(payload)

    req = urllib.request.Request(
        BALANCE_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "dsh-status/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        hint = "（Key 无效或没有余额查询权限，请到平台控制台 API Keys 页确认）" if exc.code == 401 else ""
        payload["error"] = f"HTTP {exc.code} {hint} {detail}".strip()[:220]
        return _store(payload)
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return _store(payload)

    infos = body.get("balance_infos") or []
    first = infos[0] if infos else {}
    payload.update(
        {
            "available": bool(body.get("is_available", True)) and bool(first),
            "currency": first.get("currency") or body.get("currency"),
            "total": first.get("total_balance") or body.get("bal_available"),
            "granted": first.get("granted_balance") or body.get("bal_granted"),
            "topped_up": first.get("topped_up_balance") or body.get("bal_topped_up"),
        }
    )
    if not payload["total"]:
        payload["error"] = f"接口未返回余额字段：{json.dumps(body, ensure_ascii=False)[:160]}"
    return _store(payload)


def _store(payload: dict) -> dict:
    _balance_cache["fetched_at"] = payload.get("fetched_at") or now()
    _balance_cache["payload"] = dict(payload)
    return payload


# ---------------------------------------------------------------- CLI

def render_pretty(snap: dict) -> str:
    s = snap["session"] or {}
    lines = [
        f"状态    {snap['label']}  ({snap['state']})",
        f"应用    {'运行中' if snap['app_running'] else '未运行'}",
        f"项目    {s.get('project') or '-'}",
    ]
    if s.get("title"):
        lines.append(f"标题    {s['title']}")
    if s.get("turn") is not None:
        lines.append(f"轮次    T{s['turn']}/S{s.get('step')}  最后事件 {s.get('last_event')}  {s.get('age_sec')}s 前")
    if s.get("pending"):
        lines.append(f"等你    {s['pending'].get('text')}")
        if s["pending"].get("options"):
            lines.append(f"选项    {' / '.join(s['pending']['options'])}")
    if s.get("error"):
        lines.append(f"错误    {s['error']}")
    if s.get("todo"):
        t = s["todo"]
        lines.append(f"任务    {t['done']}/{t['total']} 完成，{t['in_progress']} 进行中")
    bal = snap["balance"]
    if bal.get("available"):
        lines.append(
            f"余额    {bal['currency']} {bal['total']}（赠送 {bal.get('granted')} / 充值 {bal.get('topped_up')}）"
        )
    else:
        lines.append(f"余额    不可用：{bal.get('error')}")
    for w in snap["waiting"]:
        lines.append(f"待处理  {w['project']}：{w['text']}（{int(w['age_sec'])}s 前）")
    return "\n".join(lines)


def render_balance(bal: dict) -> str:
    if bal.get("available"):
        line = f"余额    {bal['currency']} {bal['total']}"
        line += f"（赠送 {bal.get('granted')} / 充值 {bal.get('topped_up')}）"
        line += f"  来源 {bal.get('source')}"
        return line
    return f"余额    不可用：{bal.get('error')}（来源 {bal.get('source')}）"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="DeepSeek Harness 状态检测")
    ap.add_argument("--json", action="store_true", help="输出单行 JSON")
    ap.add_argument("--pretty", action="store_true", help="详细视图")
    ap.add_argument("--state-only", action="store_true", help="只输出状态码")
    ap.add_argument("--watch", action="store_true", help="持续输出 NDJSON")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--balance-interval", type=float, default=BALANCE_TTL)
    ap.add_argument("--no-balance", action="store_true")
    ap.add_argument("--states", action="store_true", help="打印状态图例 JSON")
    ap.add_argument("--demo", metavar="STATE", help="用真实会话数据伪造指定状态的一次快照（视觉自检用）")
    ap.add_argument("--balance", action="store_true", help="只查询并打印余额")
    ap.add_argument("--init-balance", action="store_true", help="交互录入余额 Key（DPAPI 加密保存）")
    ap.add_argument("--save-balance-key", action="store_true", help="从标准输入读取余额 Key 并保存")
    ap.add_argument("--clear-balance-key", action="store_true", help="删除已保存的余额 Key")
    ap.add_argument("--sessions", default=SESSION_GLOB)
    args = ap.parse_args(argv)

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    if args.init_balance or args.save_balance_key:
        if args.init_balance:
            import getpass

            key = getpass.getpass("DeepSeek 余额 Key（平台控制台 API Keys 里的余额/组织 Key）: ").strip()
        else:
            key = sys.stdin.read().strip()
        if not key:
            print("没有读到 Key，已取消。", file=sys.stderr)
            return 2
        print(f"已保存到 {save_balance_key(key)}")
        bal = fetch_balance(now(), force=True, ttl=args.balance_interval)
        print(json.dumps(bal, ensure_ascii=False) if args.json else render_balance(bal))
        return 0

    if args.clear_balance_key:
        for name in ("balance.protected", "balance.key"):
            path = os.path.join(state_dir(), name)
            if os.path.isfile(path):
                os.remove(path)
                print(f"已删除 {path}")
                return 0
        print("没有已保存的 Key")
        return 0

    if args.balance:
        bal = fetch_balance(now(), force=True, ttl=args.balance_interval)
        print(json.dumps(bal, ensure_ascii=False) if args.json else render_balance(bal))
        return 0

    if args.demo:
        state = args.demo
        if state not in STATES:
            print(f"未知状态 {state}，可选：{', '.join(STATES)}", file=sys.stderr)
            return 2
        snap = snapshot(args.sessions, now(), want_balance=False)
        fake = dict(snap["session"] or {"project": "Demo", "turn": 12, "step": 3})
        fake["state"] = state
        fake["age_sec"] = 3.0
        if state == "needs_action":
            fake["pending"] = {"kind": "question", "tool": "ask_user_question",
                               "text": "要不要先只改固件那套？两套都改会牵动协议、服务端和测试。",
                               "options": ["只改固件", "两套都改", "先不动"]}
            fake["todo"] = {"total": 8, "done": 5, "in_progress": 1, "pending": 2}
        elif state == "error":
            fake["error"] = "429: The model service is temporarily rate limited"
        else:
            fake.pop("pending", None)
        snap["state"] = state
        snap["attention"] = state in ("needs_action", "error")
        snap["session"] = fake
        text = compose_text(fake, state, {"available": False}, snap["waiting"])
        snap.update({k: text[k] for k in ("label", "glyph", "color", "detail", "strip", "strip_left", "strip_right", "tooltip", "tooltip_full", "tip_lines")})
        print(json.dumps(snap, ensure_ascii=False))
        return 0

    if args.states:
        for k, v in STATES.items():
            print(json.dumps({"state": k, "label": v[0], "glyph": v[1], "color": v[2]}, ensure_ascii=False))
        return 0

    if not args.watch:
        snap = snapshot(args.sessions, now(), not args.no_balance, args.balance_interval)
        if args.state_only:
            print(snap["state"])
        elif args.json:
            print(json.dumps(snap, ensure_ascii=False))
        else:
            print(render_pretty(snap))
        return 0

    want_balance = not args.no_balance
    token = refresh_token()
    while True:
        ts = now()
        fresh = refresh_token()
        force = fresh != token
        token = fresh
        try:
            snap = snapshot(args.sessions, ts, want_balance, args.balance_interval, force)
            sys.stdout.write(json.dumps(snap, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception as exc:
            err = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "state": "unknown", "generated_at": ts}
            sys.stdout.write(json.dumps(err, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        time.sleep(max(0.2, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
