#!/usr/bin/env python3
"""dsh-status 冒烟测试：把 README 承诺的每条命令和状态栏契约跑成断言。

  python smoke_test.py           引擎 + CLI 契约（无副作用，可随时复跑）
  python smoke_test.py --build   额外做 Release 编译，断言 0 警告 0 错误
  python smoke_test.py --gui     额外启动 DshBar.exe，在 Win32 层校验任务栏停靠

--gui 会真的往任务栏里挂一个状态栏，结束时用 taskkill /f 收掉（优雅关闭当前不可用，
见 check_gui 的注释）。--build 需要 .NET 10 SDK。
"""

from __future__ import annotations

import compression.zstd as zstd  # noqa: F401  # 提前失败：低于 3.14 直接报清晰错误
import collections
import ctypes
import ctypes.wintypes as wt
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") != "utf8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dsh_state as ds  # noqa: E402

BAR_EXE = os.path.join(HERE, "bar", "bin", "Release", "net10.0-windows", "DshBar.exe")
SNAPSHOT_KEYS = (
    "ok", "state", "label", "glyph", "color", "detail", "strip", "strip_left",
    "strip_right", "tooltip", "tooltip_full", "tip_lines", "attention",
    "session", "waiting", "balance", "recent", "sessions_scanned", "app_running",
    "sessions",
)
FAILS: list[str] = []
PASSED = 0


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASSED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILS.append(f"{name}：{detail}")
        print(f"  ✗ {name} —— {detail}")
    return cond


def run(*args: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", os.path.join(HERE, "dsh_state.py"), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, cwd=HERE,
    )


def parse_line(line: str) -> dict | None:
    try:
        return json.loads(line)
    except Exception:
        return None


# ---------------------------------------------------------------- 单元与端到端

def check_unit_tests() -> None:
    print("== 引擎测试套件 ==")
    for extra in ([], ["--live"]):
        p = subprocess.run(
            [sys.executable, "-X", "utf8", os.path.join(HERE, "test_dsh_state.py"), *extra],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300, cwd=HERE,
        )
        label = "真实数据冒烟 --live" if extra else "合成用例"
        check(f"{label} 全绿", p.returncode == 0,
              f"退出码 {p.returncode}：{(p.stdout + p.stderr)[-400:]}")
        check(f"{label} 无编码崩溃", "UnicodeEncodeError" not in (p.stdout + p.stderr),
              "控制台编码不支持输出字符")


def check_cli() -> None:
    print("\n== CLI 契约 ==")
    p = run("--state-only", "--no-balance")
    check("--state-only 输出合法状态码", p.returncode == 0 and p.stdout.strip() in ds.STATES,
          f"退出码 {p.returncode} 输出 {p.stdout.strip()!r}")

    p = run("--no-balance")
    check("人类可读快照可用", p.returncode == 0 and "状态" in p.stdout, p.stdout[:200])

    p = run("--json", "--no-balance")
    snap = parse_line(p.stdout.strip().splitlines()[0]) if p.stdout.strip() else None
    ok = p.returncode == 0 and isinstance(snap, dict)
    check("--json 单行可解析", ok, f"退出码 {p.returncode}")
    if ok:
        miss = [k for k in SNAPSHOT_KEYS if k not in snap]
        check("快照字段齐全", not miss, f"缺少 {miss}")
        check("tooltip 不超 NotifyIcon 上限", len(snap["tooltip"]) <= 63,
              f"{len(snap['tooltip'])} 字符")
        check("tip_lines 非空", bool(snap["tip_lines"]), "空列表")
        check("禁用余额时标记 skipped", snap["balance"].get("skipped") is True,
              str(snap["balance"]))
        rows = snap.get("sessions")
        # 本机有没有会话文件决定这组断言能不能跑：与 test_dsh_state.run_live() 用同一个跳过口径
        # （同一行说明、同样不计入 FAILS）。少了这层，干净机器／CI 上 `python smoke_test.py`
        # 会因为「扫不到会话」假红——而那条 0 < len(rows) 本来只是想要一个非空样本行。
        session_files = glob.glob(os.path.expanduser(ds.SESSION_GLOB))
        if not session_files:
            print("  （跳过：本机没有 dsh 会话文件，sessions 行级断言无样本）")
        else:
            check("sessions 数组存在且不超上限",
                  isinstance(rows, list) and 0 < len(rows) <= ds.SESSIONS_IN_SNAPSHOT,
                  f"{type(rows).__name__} len={len(rows) if isinstance(rows, list) else '-'}"
                  f"（本机有 {len(session_files)} 个会话文件，扫出 0 行是真缺陷）")
            if rows:
                need = {"key", "project", "title", "state", "turn", "step", "age_sec",
                        "last_event", "last_tool", "end_reason", "records", "todo",
                        "usage_total", "pending"}
                miss = need - set(rows[0])
                check("sessions 行字段齐全", not miss, f"缺少 {sorted(miss)}")
                # 白名单是双向的：spec §4 原文「path 与 cwd 不在其中」，一次覆盖两个键。
                banned = sorted(k for k in ("path", "cwd") if k in rows[0])
                check("sessions 行不含 path/cwd", not banned,
                      f"行里出现了 {banned}（硬闸：spec §4 的白名单排除这两项，无消费者）")
                check("sessions 状态码全部合法",
                      all(r["state"] in ds.STATES for r in rows),
                      str({r["state"] for r in rows} - set(ds.STATES)))

    p = run("--states")
    lines = [l for l in p.stdout.splitlines() if l.strip()]
    check("--states 覆盖全部状态", p.returncode == 0 and len(lines) == len(ds.STATES),
          f"{len(lines)}/{len(ds.STATES)} 行")

    p = run("--balance")
    bal = parse_line(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else None
    check("--balance 无 Key 时优雅降级",
          p.returncode == 0 and (bal is None or bal.get("available") is False),
          f"退出码 {p.returncode}：{p.stdout[:200]}")


def check_demos() -> None:
    print("\n== --demo 全状态 ==")
    for state in ds.STATES:
        p = run("--demo", state, "--json", "--no-balance")
        snap = parse_line(p.stdout.strip()) if p.stdout.strip() else None
        good = p.returncode == 0 and isinstance(snap, dict) and snap.get("state") == state
        check(f"demo {state}", good,
              f"退出码 {p.returncode}，实得 {(snap or {}).get('state')}")
        if isinstance(snap, dict):
            check(f"demo {state} 配色与图例一致",
                  snap.get("color") == ds.STATES[state][2] and snap.get("glyph") == ds.STATES[state][1],
                  f"{snap.get('color')}/{snap.get('glyph')}")
    p = run("--demo", "no_such_state", "--json")
    check("未知状态退出码 2", p.returncode == 2 and "未知状态" in (p.stdout + p.stderr),
          f"退出码 {p.returncode}")


def check_watch() -> None:
    print("\n== --watch NDJSON 流 ==")
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", os.path.join(HERE, "dsh_state.py"),
         "--watch", "--interval", "1", "--no-balance"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", cwd=HERE,
    )
    lines = []
    t0 = time.time()
    try:
        while len(lines) < 3 and time.time() - t0 < 20:
            line = proc.stdout.readline()
            if not line:
                break
            if line.strip():
                lines.append(line.strip())
    finally:
        proc.kill()
        proc.wait(timeout=10)
    check("watch 至少产出 3 帧", len(lines) >= 3, f"只有 {len(lines)} 帧")
    bad = [l for l in lines if parse_line(l) is None or not parse_line(l).get("ok")]
    check("每帧都是合法快照", not bad, f"异常帧：{bad[:1]}")


def check_key_storage() -> None:
    print("\n== 余额 Key 存取 ==")
    env_key = os.environ.get("DEEPSEEK_BALANCE_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if env_key:
        print("  （跳过：环境变量里已有 Key，会盖住 DPAPI 分支）")
        return
    path = os.path.join(ds.state_dir(), "balance.protected")
    backup = open(path, "rb").read() if os.path.isfile(path) else None
    token = os.path.join(ds.state_dir(), "refresh.token")
    token_backup = open(token, "rb").read() if os.path.isfile(token) else None
    probe = "SMOKE-TEST-NOT-A-REAL-KEY-0000"
    try:
        p = subprocess.run(
            [sys.executable, "-X", "utf8", os.path.join(HERE, "dsh_state.py"), "--save-balance-key"],
            input=probe, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, cwd=HERE,
        )
        check("--save-balance-key 成功", p.returncode == 0 and "已保存" in p.stdout,
              f"退出码 {p.returncode}：{p.stdout[:200]}")
        raw = open(path, "rb").read() if os.path.isfile(path) else b""
        check("落盘非明文", bool(raw) and probe.encode() not in raw,
              f"{len(raw)} 字节")
        key, source = ds.balance_key()
        check("DPAPI 回读一致", key == probe and source == "dpapi", f"实得 {source}/{key!r}")

        p = subprocess.run(
            [sys.executable, "-X", "utf8", os.path.join(HERE, "dsh_state.py"), "--clear-balance-key"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, cwd=HERE,
        )
        check("--clear-balance-key 删除", p.returncode == 0 and not os.path.isfile(path),
              f"退出码 {p.returncode}：{p.stdout[:200]}")
        p = subprocess.run(
            [sys.executable, "-X", "utf8", os.path.join(HERE, "dsh_state.py"), "--clear-balance-key"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, cwd=HERE,
        )
        check("重复清除幂等", p.returncode == 0 and "没有已保存" in p.stdout, p.stdout[:200])
    finally:
        if backup is not None:
            open(path, "wb").write(backup)
        elif os.path.isfile(path):
            os.remove(path)
        if token_backup is not None:
            open(token, "wb").write(token_backup)


def check_engine_copy() -> None:
    print("\n== 产物一致性 ==")
    if not os.path.isfile(BAR_EXE):
        check("DshBar.exe 存在", False, "先跑 --build 或 build.cmd")
        return
    out = os.path.join(os.path.dirname(BAR_EXE), "dsh_state.py")
    check("产物目录带引擎", os.path.isfile(out), out)
    if os.path.isfile(out):
        check("产物引擎与源码同内容",
              open(out, "rb").read() == open(os.path.join(HERE, "dsh_state.py"), "rb").read(),
              "改了源码但没重新编译")


LICENSE_MUST_CONTAIN = (
    "MIT", "Wildcreator",                      # 本项目自身
    "lepoco", "Leszek Pomianowski",            # WPF-UI
    "AmorFate", "AF-Media-Bar",                # 设计参照
    "Bäumlisberger",                           # VirtualizingWrapPanel（WPF-UI 传递依赖）
    "fluentui-system-icons", "microsoft-ui-xaml", "dotnet/wpf",
    "Segoe Fluent Icons",
)

# 标准 SPDX MIT 正文——去掉标题行与版权行之后剩下的那部分。权威参照逐字取自本机
# %USERPROFILE%\.nuget\packages\wpf-ui\4.2.0\LICENSE.md 的正文（已核实与本项目 LICENSE 一致）。
# 关键词存在性检查（LICENSE_MUST_CONTAIN）拦不住「看着像 MIT 却少了半句」的残缺正文——
# 简报最初给的那版就少了 AN ACTION OF CONTRACT, TORT OR OTHERWISE, 而它照样能过全部关键词断言。
# 本任务的目的恰恰是「让本项目可被认证为 MIT」，所以正文一律与本常量逐字比对；
# 唯一的容差是第三方声明各段末行的那一个句点，理由见 notice_mit_body() 的注释，LICENSE 不享有该容差。
MIT_BODY = """\
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""

MIT_PROBE = "Permission is hereby granted, free of charge"  # 用来认出「这段声称是 MIT」
_MIT_TITLE = re.compile(r"^\s*(the\s+)?mit\s+(li[c]ense)(\s*\(mit\))?\s*$", re.I)
_MIT_COPYRIGHT = re.compile(r"^\s*copyright\b", re.I)
_MIT_RESERVED = re.compile(r"^\s*all\s+rights\s+reserved\.?\s*$", re.I)
# 末行尾部的空白 + 至多一个句点（见 notice_mit_body 的注释）。锚在 \Z，全串最多匹配一处。
_MIT_TAIL = re.compile(r"[ \t]*\.?[ \t]*\Z")


def read_utf8(path: str) -> tuple[str | None, str]:
    """读 UTF-8 文本，返回 (内容, 失败原因)。缺失/无权限/非 UTF-8 都只给原因，不抛异常。

    用 utf-8-sig 是为了不把 BOM 当成正文的一部分；正文比对本身仍是逐字的。
    """
    if not os.path.isfile(path):
        return None, f"文件不存在：{path}"
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return fh.read(), ""
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"无法按 UTF-8 读取 {path}：{exc}"


def mit_body(text: str) -> str:
    """取一段 MIT 文本的「正文」：剥掉行尾差异、许可证标题行与版权告示块，剩下的必须逐字等于 MIT_BODY。

    版权告示块 = `Copyright (c) …` 那一行，以及紧跟其后的 `All rights reserved.` 一行
    （dotnet/wpf 的原文里有这两行）。它们都不属于 MIT 的条款正文，各家写法本来就不同；
    条款正文则一个字都不许差。
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    def skip_blank(i: int) -> int:
        while i < len(lines) and not lines[i].strip():
            i += 1
        return i

    i = skip_blank(0)
    if i < len(lines) and _MIT_TITLE.match(lines[i]):
        i = skip_blank(i + 1)
    if i < len(lines) and _MIT_COPYRIGHT.match(lines[i]):
        i = skip_blank(i + 1)
        if i < len(lines) and _MIT_RESERVED.match(lines[i]):
            i += 1
    return "\n".join(lines[i:]).strip()


def notice_mit_body(text: str) -> str:
    r"""第三方声明里各 MIT 段比对前的正文：在 mit_body() 之上只多一条容差——末行尾部的空白与单个句点。

    块整体的首尾空白 mit_body() 已经去掉；除此之外零容差，条款正文任何一处被改坏、截断、
    换词、多一个标点，都照样报红。

    为什么独独放宽这一个标点：本文件 §2.4（microsoft-ui-xaml）的原文来自上游
    `%USERPROFILE%\.nuget\packages\wpf-ui\4.2.0\ThirdPartyNotices.txt` 第 116 行，
    那份源文件最末一句 `…DEALINGS IN THE SOFTWARE` 后面**本来就没有句号**
    （正文已与上游逐字核对，行尾则按本仓库既有的纯 LF 约定——上游那份整份是 CRLF，
    这不是改写了来源，只是本仓库的统一行尾；看似笔误、实为源文件原文）。
    第三方许可证声明的唯一职责是逐字忠于它的来源，
    合规文件不得为了迁就断言去改写来源——所以让步的是断言，不是 §2.4 的正文。
    （§1 那份 LICENSE.md、§2.1–2.3 与 §3 各段的来源都带句号，容差对它们是空操作，不放松任何约束。）

    `LICENSE` 不走这条容差：那是我们自己的文件，没有「忠于上游」的豁免，仍用 mit_body() 严格逐字比对。
    """
    return _MIT_TAIL.sub("", mit_body(text), count=1)


# notice_mit_body() 一侧的参照：同样只去掉末行那一个句点，两侧口径对称。
_MIT_BODY_TOLERANT = _MIT_TAIL.sub("", MIT_BODY, count=1)

# THIRD-PARTY-NOTICES.md §2.4（microsoft-ui-xaml）那段的小节标题里含这个词，靠它认出那一段。
XAML_NOTICE_HEADING_KEY = "microsoft-ui-xaml"


def notice_section_last_line(blocks: list[tuple[str, str]], heading_key: str) -> tuple[str, str]:
    """在待比对的 MIT 段里找标题含 heading_key 的那一段，返回 (小节标题, 该段末行非空文本)。

    找不到那一段时返回 ("", "")——调用处据此记 ✗，不让硬闸因为段落被删/被改名而静默失效。
    """
    for heading, block in blocks:
        if heading_key in heading:
            for line in reversed(block.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
                if line.strip():
                    return heading, line.strip()
            return heading, ""
    return "", ""


def fenced_blocks(md: str) -> list[tuple[str, str]]:
    """按出现顺序返回 Markdown 围栏代码块 (所属小节标题, 块内容)。"""
    out: list[tuple[str, str]] = []
    heading = ""
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        if lines[i].startswith("#"):
            heading = lines[i].lstrip("#").strip() or heading
        if lines[i].strip().startswith("```"):
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            out.append((heading, "\n".join(body)))
        i += 1
    return out


def first_diff(got: str, want: str) -> str:
    """两处文本的第一个差异，报字符偏移和上下文，便于改坏时一眼定位。"""
    for i, (a, b) in enumerate(zip(got, want)):
        if a != b:
            lo = max(0, i - 24)
            return (f"第 {i} 个字符起不同：实得 …{got[lo:i + 24]!r}，应为 …{want[lo:i + 24]!r}")
    return (f"长度不同：实得 {len(got)} 字符、应为 {len(want)} 字符，"
            f"差的部分：{(want[len(got):] if len(got) < len(want) else got[len(want):])[:120]!r}")


def check_licenses() -> None:
    print("\n== 开源合规 ==")
    root = HERE
    lic = os.path.join(root, "LICENSE")
    tpn = os.path.join(root, "THIRD-PARTY-NOTICES.md")
    readme_p = os.path.join(root, "README.md")
    csproj_p = os.path.join(root, "bar", "DshBar.csproj")
    check("LICENSE 存在", os.path.isfile(lic), lic)
    check("THIRD-PARTY-NOTICES.md 存在", os.path.isfile(tpn), tpn)
    # 缺文件 / 非 UTF-8 一律记 ✗：合规段会被后续任务的 --all 复用，不能让整段 traceback 退出。
    lic_text, lic_err = read_utf8(lic)
    tpn_text, tpn_err = read_utf8(tpn)
    readme, readme_err = read_utf8(readme_p)
    csproj, csproj_err = read_utf8(csproj_p)
    check("四份合规文件均可按 UTF-8 读取",
          not (lic_err or tpn_err or readme_err or csproj_err),
          "；".join(e for e in (lic_err, tpn_err, readme_err, csproj_err) if e))
    blob = (lic_text or "") + (tpn_text or "")
    missing = [k for k in LICENSE_MUST_CONTAIN if k not in blob]
    check("必要归属条目齐全", not missing, f"缺 {missing}")
    check("LICENSE 正文逐字等于标准 MIT",
          not lic_err and mit_body(lic_text or "") == MIT_BODY,
          lic_err or first_diff(mit_body(lic_text or ""), MIT_BODY))
    blocks = [(h, b) for h, b in fenced_blocks(tpn_text or "") if MIT_PROBE in b]
    check("THIRD-PARTY-NOTICES 里声称 MIT 的段落数符合预期", len(blocks) >= 6,
          f"应有 6 段（WPF-UI、其 4 项 MIT 传递依赖、AF-Media-Bar），实得 {len(blocks)}")
    for heading, block in blocks:
        check(f"MIT 原文与标准 MIT 正文一致（末行句号与行尾空白除外，余皆逐字）：{heading}",
              notice_mit_body(block) == _MIT_BODY_TOLERANT,
              first_diff(notice_mit_body(block), _MIT_BODY_TOLERANT))
    # 单向硬闸：上面那条容差是**双向**的（`SOFTWARE` 与 `SOFTWARE.` 都能过），
    # 所以「有人把 THIRD-PARTY-NOTICES.md §2.4 那个句号补回去」并不会让门禁变红——
    # 而那正是轮 1 犯过的错（上游 ThirdPartyNotices.txt 第 116 行的原文末行就没有句号，
    # 声明文件照原样保留才是对的）。这里只反着钉 §2.4 那一段：末行出现句点即 ✗。
    # 容差本身保持双向不动，其余 5 段与 `LICENSE` 都不受本条影响。
    xaml_heading, xaml_last = notice_section_last_line(blocks, XAML_NOTICE_HEADING_KEY)
    if not xaml_heading:
        xaml_detail = ("THIRD-PARTY-NOTICES.md 里没找到标题含 microsoft-ui-xaml 的 MIT 段，"
                       "硬闸失去对象——§2.4 那段被删、被改名或正文不再声称 MIT 都会走到这里")
    else:
        xaml_detail = (f"段落「{xaml_heading}」的末行实得 {xaml_last!r}；上游 "
                       "ThirdPartyNotices.txt 第 116 行的末行是不带句号的 'SOFTWARE'，"
                       "这里带上句点就等于替上游改写了来源——请把句号去掉")
    check("§2.4 microsoft-ui-xaml 末行不得补句号（上游原文本就缺这个句号，声明文件照原样保留）",
          bool(xaml_heading) and not xaml_last.endswith("."),
          xaml_detail)
    check("README 不再声称编译不需要联网",
          not readme_err and "不需要联网" not in readme,
          readme_err or "仍有「不需要联网」")
    check("README 有开源协议章节", not readme_err and "## 开源协议" in readme,
          readme_err or "缺章节")
    check("csproj 声明了许可证元数据",
          not csproj_err and "PackageLicenseExpression" in csproj and "Copyright" in csproj,
          csproj_err or "缺 PackageLicenseExpression / Copyright")


# ---------------------------------------------------------------- Win32 探测

class RECT(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG), ("right", wt.LONG), ("bottom", wt.LONG)]


def _user32():
    return ctypes.windll.user32


def phys_width(hwnd: int) -> int:
    """窗口实际像素宽：探测进程未必 DPI 感知，按窗口自己的 DPI 把逻辑宽换算回物理宽。"""
    u = _user32()
    r = RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    dpi = u.GetDpiForWindow(hwnd) or 96
    return int(round((r.right - r.left) * dpi / 96))


def rect_of(hwnd: int) -> tuple[int, int, int, int]:
    u = _user32()
    r = RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def settled_width(hwnd: int, seconds: float = 8.0) -> int:
    """首帧快照要等引擎起来才到，宽度会补间撑开；取窗口期内的最大值。"""
    best = phys_width(hwnd)
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(0.5)
        best = max(best, phys_width(hwnd))
    return best


def _png(path: str, width: int, height: int, rgb: bytes) -> None:
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += rgb[y * width * 3:(y + 1) * width * 3]
    body = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(body)
def read_png_rgba(path: str) -> tuple[int, int, bytes] | None:
    """极简 PNG 解码：只认 PngBitmapEncoder 从 Pbgra32 出的那种 8 位 RGBA 非交错图。

    离屏表格门禁要看的是**像素结构**（表格有没有把页面铺满、行分隔线有没有成排），
    不是一个拍出来的色数；而本仓库只有 stdlib（Python 3.14），所以这里自带解码，
    不引 Pillow。_png() 是写、这个是读，两个凑一对。
    读不了（缺文件/不是 PNG/位深或色型不对/压缩流坏了/长度不够）一律返回 None，
    由调用处记 ✗ —— 不能抛异常把整轮冒烟带崩。
    """
    import struct
    import zlib

    try:
        raw = open(path, "rb").read()
    except OSError:
        return None
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    pos, ihdr, idat = 8, b"", bytearray()
    while pos + 12 <= len(raw):
        ln = struct.unpack(">I", raw[pos:pos + 4])[0]
        tag = raw[pos + 4:pos + 8]
        body = raw[pos + 8:pos + 8 + ln]
        if tag == b"IHDR":
            ihdr = body
        elif tag == b"IDAT":
            idat += body      # 分片要拼回去
        elif tag == b"IEND":
            break
        pos += 12 + ln
    if len(ihdr) != 13:
        return None
    w, h, depth, color, _comp, _filt, interlace = struct.unpack(">IIBBBBB", ihdr)
    if depth != 8 or color != 6 or interlace != 0 or w <= 0 or h <= 0:
        return None  # 只支持 8 位 RGBA；哪天出图格式变了，这里返回 None 让门禁记 ✗ 而不是假绿
    ch, stride = 4, w * 4
    try:
        data = zlib.decompress(bytes(idat))
    except zlib.error:
        return None
    if len(data) < h * (stride + 1):
        return None
    out = bytearray(h * stride)
    prev = bytes(stride)
    for y in range(h):
        base = y * (stride + 1)
        ft = data[base]
        line = bytearray(data[base + 1:base + 1 + stride])
        if ft == 0:
            pass
        elif ft == 1:      # Sub
            for x in range(ch, stride):
                line[x] = (line[x] + line[x - ch]) & 0xFF
        elif ft == 2:      # Up
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif ft == 3:      # Average
            for x in range(stride):
                left = line[x - ch] if x >= ch else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xFF
        elif ft == 4:      # Paeth
            for x in range(stride):
                a = line[x - ch] if x >= ch else 0
                b = prev[x]
                c = prev[x - ch] if x >= ch else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 0xFF
        else:
            return None
        out[y * stride:(y + 1) * stride] = line
        prev = bytes(line)
    return w, h, bytes(out)


def shot_table_stats(path: str) -> dict | None:
    """从离屏 PNG 数出表格的结构证据：绘制区、不透明像素、非白像素、成排的行分隔线、
    靠右条带里最长的竖色连续段（滚动条滑块）。

    「不透明」= alpha==255。占位页（PageBase 那一行文字）在 RenderTargetBitmap 上
    是**整页透明**（本机实测 opaque=0/558000），只有文字那几个像素带 alpha；
    表格会把 Background 铺满自己的区域，所以 opaque 是「画没画出一整块表」的分水岭，
    比色数可靠。行分隔线按「同一行里被同一种非白不透明色占据 ≥600 像素」认定，
    600 ≈ 六列列宽之和（150+110+60+80+130+80=610），比 600 窄就不像表头的列骨架。
    """
    got = read_png_rgba(path)
    if got is None:
        return None
    w, h, buf = got
    stride = w * 4
    white = b"\xff\xff\xff\xff"
    painted = ink = hlines = 0
    x0, x1, y0, y1 = w, -1, h, -1
    p = 0
    for y in range(h):
        row = buf[p:p + stride]
        p += stride
        cnt = collections.Counter()
        first = last = -1
        for x in range(w):
            c = row[x * 4:x * 4 + 4]
            cnt[c] += 1
            if c[3] == 255:
                if first < 0:
                    first = x
                last = x
        op = sum(n for c, n in cnt.items() if c[3] == 255)
        if op:
            painted += op
            ink += op - cnt.get(white, 0)
            x0, x1 = min(x0, first), max(x1, last)
            y0, y1 = min(y0, y), max(y1, y)
        wide = [(c, n) for c, n in cnt.items() if n >= 600 and c != white and c[3] == 255]
        if wide:
            hlines += 1
    # 滚动证据：靠右 24px 条带（x ∈ [w-24, w-3)，绕开最右那 1px 描边）里
    # 「同一个不透明非白色竖着连续」的最长像素数。滚动条滑块是一根几像素宽、
    # 几百像素高的竖条；行分隔线只有孤立的 1~2 行高，两者在这一项上差两个数量级。
    # 视口无限高（外层套 StackPanel）时表格根本不需要滚动条，这项实测 1~2。
    vscroll = 0
    for x in range(max(0, w - 24), max(0, w - 3)):
        run, prev = 0, None
        for y in range(h):
            i = (y * w + x) * 4
            c = buf[i:i + 4]
            solid = c[3] == 255 and c != white
            run = run + 1 if (solid and c == prev) else (1 if solid else 0)
            prev = c if solid else None
            vscroll = max(vscroll, run)
    return {
        "w": w, "h": h, "total": w * h, "painted": painted, "ink": ink,
        "x0": x0, "x1": x1, "y0": y0, "y1": y1, "hlines": hlines,
        "vscroll": vscroll,
    }


def capture_bar(hwnd: int, out_path: str, scale: float = 1.0) -> tuple[int, int, bytes]:
    """PrintWindow 抓窗口自身像素：任务栏被全屏应用盖住时也能验，且直接证明
    分层窗口真的合成了内容（README 记过「命中看得到、画面看不到」这一类坑）。

    `scale` 是给 DPI 感知窗口用的：本探测进程不感知 DPI，`GetWindowRect` 拿到的是**虚拟**
    尺寸（960×640），而面板按 125% 实际占 1200×800 物理像素 —— 按虚拟尺寸建位图就只截到
    左上角那 64%，右侧的滚动条和底下几行全在画面外（本机实测踩过）。状态栏那条窗口本来就是
    按虚拟尺寸抓的，所以默认 1.0 不动它。
    """
    u, g = _user32(), ctypes.windll.gdi32

    class BI(ctypes.Structure):
        _fields_ = [("s", wt.DWORD), ("w", wt.LONG), ("h", wt.LONG), ("pl", wt.WORD),
                    ("bc", wt.WORD), ("comp", wt.DWORD), ("size", wt.DWORD),
                    ("x", wt.LONG), ("y", wt.LONG), ("cm", wt.DWORD), ("imp", wt.DWORD)]

    left, top, right, bottom = rect_of(hwnd)
    w, h = int(round((right - left) * scale)), int(round((bottom - top) * scale))
    if w <= 0 or h <= 0:
        return 0, 0, b""
    wdc = u.GetWindowDC(hwnd)
    mdc = g.CreateCompatibleDC(wdc)
    bmp = g.CreateCompatibleBitmap(wdc, w, h)
    g.SelectObject(mdc, bmp)
    bgra = b""
    try:
        for flag in (2, 0):  # 分层窗口要 PW_RENDERFULLCONTENT，普通窗口反而要用 0
            if not u.PrintWindow(hwnd, mdc, flag):
                continue
            info = BI(s=ctypes.sizeof(BI), w=w, h=-h, pl=1, bc=32, comp=0, size=w * h * 4)
            buf = ctypes.create_string_buffer(w * h * 4)
            if not g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(info), 0):
                continue
            raw = buf.raw
            hues = len({raw[i:i + 4] for i in range(0, len(raw), 4)})
            if hues > 1:
                bgra = raw
                break
        if not bgra:
            return 0, 0, b""
    finally:
        u.ReleaseDC(hwnd, wdc)
        g.DeleteDC(mdc)
        g.DeleteObject(bmp)
    rgb = bytearray(w * h * 3)
    for i in range(w * h):
        rgb[i * 3] = bgra[i * 4 + 2]
        rgb[i * 3 + 1] = bgra[i * 4 + 1]
        rgb[i * 3 + 2] = bgra[i * 4]
    _png(out_path, w, h, bytes(rgb))
    return w, h, bytes(rgb)


def find_bar_windows() -> list[int]:
    u = _user32()
    tb = u.FindWindowW("Shell_TrayWnd", None)
    if not tb:
        return []
    found: list[int] = []
    proto = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, buf, 256)
        if buf.value.startswith("HwndWrapper[DshBar"):
            found.append(hwnd)
        return True

    u.EnumChildWindows(tb, proto(cb), 0)
    return found


def top_window(title: str) -> int:
    u = _user32()
    hits = []
    proto = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _l):
        if not u.IsWindowVisible(h):
            return True
        n = u.GetWindowTextLengthW(h)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, buf, n + 1)
            if buf.value == title:
                hits.append(h)
        return True

    u.EnumWindows(proto(cb), 0)
    return hits[0] if hits else 0


def bar_processes() -> set[int]:
    out = subprocess.run(["tasklist", "/fi", "IMAGENAME eq DshBar.exe", "/fo", "csv", "/nh"],
                         capture_output=True, text=True, errors="replace").stdout
    return {int(p.split('","')[1]) for p in out.splitlines() if p.count('"') >= 3}


def python_pids() -> set[int]:
    out = subprocess.run(["tasklist", "/fi", "IMAGENAME eq python.exe", "/fo", "csv", "/nh"],
                         capture_output=True, text=True, errors="replace").stdout
    return {int(p.split('","')[1]) for p in out.splitlines() if p.count('"') >= 3}


PANEL_PAGES = ("overview", "notify", "appearance", "runtime", "balance", "about")


def run_shot(page: str, out_path: str):
    """跑一次 --panel-shot；卡死也要记成 ✗，不能让 TimeoutExpired 把整轮冒烟带崩。"""
    try:
        return subprocess.run([BAR_EXE, "--panel-shot", page, out_path],
                              capture_output=True, text=True, errors="replace",
                              timeout=120, cwd=os.path.dirname(BAR_EXE))
    except subprocess.TimeoutExpired:
        return None


def fake_engine_source(rows: int, watch: bool = False) -> str:
    """造一个假引擎源码，sessions 里放 rows 行。

    注入点是编译产物 bar/bin/Release/net10.0-windows/dsh_state.py（csproj 从仓库根拷的那份），
    所以生产代码里不需要任何测试钩子、dsh_state.py 源码全程未修改。
    rows==0 走 --watch 异常帧的形状：ok:false 且不带 sessions 键 → Snapshot.Sessions 是 null；
    rows>0 每行都给成能落笔的真实形状（项目/工具/轮次/记录数都有字），
    「非白像素」「行分隔线」两个统计量才和本机真实数据同口径。
    行数由冒烟指定，本机 ~/.dsh 里是 0 个还是 29 个会话都跟断言无关。

    `watch=True` 出的是**持续吐帧**的版本：状态栏起引擎用的是 `--watch`，
    一次性写一行就退出会让面板停在空表、且父进程立刻重启引擎（真实窗口那段要等帧喂进来）。
    每 0.5 秒重发同一帧、管道断了才收 —— 与真引擎的节律一致，也不会自己跑掉。
    """
    if rows <= 0:
        return 'import sys\nsys.stdout.write(\'{"ok": false, "error": "smoke-injected"}\\n\')\n'
    body = (
        'rows = [{\n'
        '    "key": f"smoke-{i}", "project": f"proj-{i}", "title": f"会话 {i}",\n'
        '    "state": "idle", "turn": i, "step": i, "age_sec": 60.0 + i,\n'
        '    "last_event": "result", "last_tool": f"tool{i}", "end_reason": "success",\n'
        '    "records": i, "todo": None, "usage_total": None, "pending": None,\n'
        '} for i in range(%d)]\n'
        'frame = json.dumps({"ok": True, "app_running": True, "state": "idle",\n'
        '    "label": "冒烟注入", "glyph": "·", "color": "#6B7280", "strip_left": "冒烟注入",\n'
        '    "strip_right": "--", "tooltip": "冒烟注入", "tip_lines": ["冒烟注入"],\n'
        '    "session": None, "balance": None, "waiting": [], "recent": [],\n'
        '    "sessions_scanned": len(rows), "sessions": rows}, ensure_ascii=False)\n'
    ) % rows
    if not watch:
        return 'import json, sys\n' + body + 'sys.stdout.write(frame + "\\n")\n'
    return ('import json, sys, time\n' + body +
            'try:\n'
            '    for _ in range(900):\n'
            '        sys.stdout.write(frame + "\\n")\n'
            '        sys.stdout.flush()\n'
            '        time.sleep(0.5)\n'
            'except (BrokenPipeError, ValueError, OSError):\n'
            '    pass\n')


def shot_dims(head: str) -> tuple[int, int, int]:
    """解析 C# 侧的 `SHOT <page> <w> <h> <colors>`，解析不了给 (-1, -1, -1)。"""
    parts = head.split()
    if len(parts) != 5:
        return -1, -1, -1
    try:
        return int(parts[2]), int(parts[3]), int(parts[4])
    except ValueError:
        return -1, -1, -1


def shot_bg_samples(path: str) -> list[bytes] | None:
    """appearance 页离屏图的四角背景取样（含 alpha）。

    取 (2,2)、(w-3,2)、(2,h-3)、(w-3,h-3) 四个角像素 —— 那是页面底色必然覆盖、
    而文字/控件永远到不了的位置。占位页整页透明，四角全 (0,0,0,0)，深浅两张一模一样；
    外观页把主题刷画在页面上之后，深浅两张的角像素才可能不同。
    这条门禁（Task 5 Step 0 #1 / Task 3 复核 N3）判的就是「theme 字段 →
    ApplicationThemeManager.Apply → 主题字典 → 页面真的换色」整条链是不是通的：
    字典合并被删、或 Apply 没调用时，SetResourceReference 解析不到东西 / 主题恒为浅色，
    两张取样重新变回一模一样 —— 门禁当场变红，不是守形式。
    含 alpha 是硬要求（与 C# 侧色数统计同理）：Fluent 刷多为「白色 + 低 alpha」，
    丢了 alpha 深浅两张的 RGB 可能同为 FFFFFF 而假绿。
    """
    got = read_png_rgba(path)
    if got is None:
        return None
    w, h, buf = got
    def px(x: int, y: int) -> bytes:
        i = (y * w + x) * 4
        return buf[i:i + 4]
    return [px(2, 2), px(w - 3, 2), px(2, h - 3), px(w - 3, h - 3)]


def shot_opaque_count(path: str) -> int:
    """图里 alpha==255 的像素数；读不出来给 -1。"""
    got = read_png_rgba(path)
    if got is None:
        return -1
    _w, _h, buf = got
    return sum(1 for i in range(3, len(buf), 4) if buf[i] == 255)


def fmt_rgba(px: bytes) -> str:
    return f"#{px[0]:02X}{px[1]:02X}{px[2]:02X}{px[3]:02X}"


def shot_with_fake_engine(source: str, out_path: str,
                          label: str) -> tuple[int, str, dict | None]:
    """把产物里的引擎临时换成 source，出一张概览页离屏图：返回 (退出码, stdout 首行, 像素统计)。

    换进去和还原都在这一个函数里，还原完当场断言按仓库根字节一致 ——
    check_gui 与 check_engine_copy 都拿那份拷贝起真引擎，没还原干净不能往下走。
    """
    eng = os.path.join(os.path.dirname(BAR_EXE), "dsh_state.py")
    if not check(f"{label}：注入点就位", os.path.isfile(BAR_EXE) and os.path.isfile(eng), eng):
        return -1, "", None
    with open(os.path.join(HERE, "dsh_state.py"), "rb") as fh:
        real = fh.read()
    try:
        with open(eng, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(source)
        if os.path.isfile(out_path):
            os.remove(out_path)
        p = run_shot("overview", out_path)
        if p is None:
            check(f"{label}：--panel-shot", False, "120 秒没返回（离屏渲染卡死）")
            return -1, "", None
        head = ((p.stdout or "").strip().splitlines() or [""])[0]
        return p.returncode, head, (shot_table_stats(out_path) if os.path.isfile(out_path) else None)
    finally:
        with open(eng, "wb") as fh:
            fh.write(real)
        check(f"{label}：引擎拷贝已按仓库根还原", open(eng, "rb").read() == real, eng)
        if os.path.isfile(out_path):
            os.remove(out_path)


def row_data_gate(st: dict, base: dict) -> bool:
    """「表里真的有数据行」：与空表那张做**差**，不钉绝对值。

    注入实测（同一台机器、同一次运行、固定 96 DPI 离屏）：1 行 → 横线 +1 / 非白 +1576，
    3 行 → +3 / +4594，29 行 → +15 / +25002。判据按最松的 1 行情形再留一半余量：
    横线至少多 1 条、非白至少多 600 像素。
    旧版钉的是 hlines >= 6 / ink >= 5000：前者在本机实测等价于「至少 4 个会话」
    （hlines ≈ 2 + min(可见行数, 15)），只在零会话时才跳过，1~3 个会话的机器必假红；
    后者真正的邻居是**负样本**基线（空表实测 3966，只差 25%），不是正样本 36432。
    做差之后会话数、DPI、列宽三个变量一次性抵消。
    """
    return st["hlines"] - base["hlines"] >= 1 and st["ink"] - base["ink"] >= 600


def check_panel_shell(empty_base: dict | None) -> dict[str, int]:
    print("\n== 控制台面板 ==")
    shots: dict[str, int] = {}
    if not os.path.isfile(BAR_EXE):
        print("  （跳过：没有编译产物）")
        return shots
    if bar_processes():
        check("启动前无残留实例", False, "已有 DshBar 在跑")
        return shots
    shot_paths: dict[str, str] = {}
    for page in PANEL_PAGES:
        shot = os.path.join(ds.state_dir(), f"panel-{page}.png")
        shot_paths[page] = shot
        if os.path.isfile(shot):
            os.remove(shot)
        p = run_shot(page, shot)
        if p is None:
            check(f"--panel-shot {page}", False, "120 秒没返回（离屏渲染卡死）")
            shots[page] = -1
            continue
        head = ((p.stdout or "").strip().splitlines() or [""])[0]
        # C# 侧输出：SHOT <page> <w> <h> <colors>
        parts = head.split()
        # 数字解析不了就记 ✗，别 ValueError 崩整轮：`and` 短路只挡长度，不挡 parts[2..4] 的内容。
        w_px = h_px = colors = -1
        if len(parts) == 5 and parts[0] == "SHOT" and parts[1] == page:
            try:
                w_px, h_px, colors = int(parts[2]), int(parts[3]), int(parts[4])
            except ValueError:
                pass
        check(f"--panel-shot {page}",
              p.returncode == 0 and colors >= 0 and w_px >= 720 and h_px >= 480,
              f"退出码 {p.returncode} 输出 {head!r} err={(p.stderr or '')[-160:]!r}")
        check(f"{page} 落盘 PNG", os.path.isfile(shot) and os.path.getsize(shot) > 2000, shot)
        shots[page] = colors
        print(f"  {page} 离屏颜色种类 {colors}")

    # 概览页的门禁：表格骨架到底立起来没有，看像素结构，不看色数。
    # 简报 Step 1 钉的是「离屏颜色种类 > 200」，那个数没有实测依据：本机实测 6 个占位页
    # 各 7 色，填上表格骨架（表头 + 可见行 + 水平网格线，真实 29 行数据）之后是 40 色
    # —— 一张灰阶抗锯齿的表本来就上不去 200。照原样钉是永久 ✗，为了让它过而给页面加装饰
    # 更糟。所以换成三条量得出来的结构证据（口径见 shot_table_stats），阈值裁决交回控制器，
    # 实测数与两种口径的对比见 task-4-report.md。
    # Task 5 后参照物更新：概览页早已不是 7 色（Task 4 轮 1 后实测 70），appearance 也
    # 从占位变成真页（实测 240+ 色），「六页恒为 7」只剩 notify/runtime/balance/about 四页成立。
    ov_shot = shot_paths.get("overview", "")
    st = shot_table_stats(ov_shot) if ov_shot and os.path.isfile(ov_shot) else None
    if st is None:
        check("概览页离屏 PNG 可读（表格门禁的前提）", False,
              f"{ov_shot} 读不出像素，色数门禁会一起变成无从判定")
    else:
        pct = st["painted"] / st["total"]
        span = st["x1"] - st["x0"] + 1
        colors = shots.get("overview", 0)
        print(f"  概览页离屏实测：颜色 {colors} 种，不透明 {st['painted']}/{st['total']}（{pct:.1%}），"
              f"非白 {st['ink']} 像素，绘制区 x {st['x0']}..{st['x1']} y {st['y0']}..{st['y1']}，"
              f"≥600px 横线 {st['hlines']} 条")
        # 占位页在这三条上的实测值：7 色 / 0.0% 不透明（整页透明）/ 横向跨度不成立。
        # 空表（Sessions 为 null 时）实测 41.9% 不透明、铺满 900 宽，所以这条不依赖数据量，
        # 见 check_panel_empty_sessions()。
        check("概览页已画出表格（不再是一行占位文字）",
              pct >= 0.35 and span >= st["w"] - 2 and colors > 20,
              f"colors={colors} painted={pct:.1%} x 跨度={span}/{st['w']}"
              f"（占位页实测 7 色、0.0% 不透明）")
        # 行分隔线成排 = 真的有数据行渲染出来，而不只是一块白底 + 表头。
        # 与 check_cli() 用同一个跳过口径：没有会话文件就没有样本行，这条不该假红。
        # 阈值是**相对空表那张**的量（见 row_data_gate），不再钉 hlines>=6 / ink>=5000：
        # 那两个数把门禁和本机的会话数焊死在一起，1~3 个会话的机器必然假红。
        session_files = glob.glob(os.path.expanduser(ds.SESSION_GLOB))
        if not session_files:
            print("  （跳过：本机没有 dsh 会话文件，概览页行分隔线断言无样本）")
        elif empty_base is None:
            check("空表基线可用（数据行门禁的前提）", False,
                  "上一轮假引擎注入没出图，相对阈值无从计算")
        else:
            check("概览页表格有数据行：行分隔线与非白像素都高出空表基线",
                  row_data_gate(st, empty_base),
                  f"本机 {len(session_files)} 个会话：横线 {empty_base['hlines']}→{st['hlines']}、"
                  f"非白 {empty_base['ink']}→{st['ink']}"
                  f"（判据：至少多 1 条横线、至少多 600 非白像素）")

    # 非法页名必须非零退出。旧实现把未知 key 静默当 overview：`--panel-shot nonsense`
    # 照样回 `SHOT nonsense 900 620 <概览的色数>` 且退 0，上面 parts[1] == page 只比回声，
    # 看不出渲染其实跑偏去了别的页。
    p = run_shot("nonsense", "-")
    if p is None:
        check("--panel-shot 非法页名非零退出", False, "120 秒没返回")
    else:
        check("--panel-shot 非法页名非零退出", p.returncode != 0,
              f"退出码 {p.returncode} 输出 {((p.stdout or '').strip().splitlines() or [''])[0][:80]!r}")
    return shots


def check_panel_empty_sessions(empty: tuple[int, str, dict | None]) -> None:
    """Sessions 缺席/null 时概览页要照常出图，不能崩 —— 空表是合法状态。

    两条真实来源：
      ① --watch 的异常帧（ok:false）根本不带 sessions 键，反序列化后 Snapshot.Sessions 是 null；
      ② 面板在第一帧快照到达之前就打开（App.Latest 还是 null），Refresh(null) 走同一条路。
    注入点：--panel-shot 取快照的引擎路径写死为 AppContext.BaseDirectory\\dsh_state.py
    （那里没有才退回仓库根），而那份拷贝是编译产物 —— 所以冒烟可以把它临时换成一个假引擎，
    让它只吐一行 `{"ok": false}`，或者干脆什么都不吐。生产代码里因此不需要任何测试钩子，
    还原由 shot_with_fake_engine 的 finally 当场断言。
    情形 ① 那张图同时是**空表基线**（empty 参数）：所有「有没有数据行」的门禁都拿它做差，
    所以这里不再重复注入一次。
    """
    print("\n== 概览页对空 sessions 的容忍 ==")
    if not os.path.isfile(BAR_EXE):
        # 与 check_panel_shell 同一口径：没产物是「跑不了」，不是「跑坏了」。
        print("  （跳过：没有编译产物）")
        return
    if bar_processes():
        # 同口径的另一半：已有实例是**真事故**（上一轮没收拾干净），必须记 ✗ 而不是静默跳过。
        check("启动前无残留实例", False, "已有 DshBar 在跑")
        return
    rc, head, st = empty
    # 断言的不是色数，是「没崩 + 表壳还在 + 没有数据行」：
    # hlines 在真实 29 行数据下实测 17，这里必须掉下来，否则说明假引擎根本没生效。
    colors = shot_dims(head)[2]
    check("空表注入：ok:false 异常帧（无 sessions 键） 仍出图且退 0",
          rc == 0 and colors > 20 and st is not None,
          f"退出码 {rc} 输出 {head!r}")
    if st:
        check("空表注入：ok:false 异常帧（无 sessions 键） 表壳仍在但没有数据行",
              st["painted"] / st["total"] >= 0.35 and st["hlines"] < 6,
              f"不透明 {st['painted']}/{st['total']} 横线 {st['hlines']} 条"
              f"（真实 29 行数据实测 17 条，没掉下来就是注入没生效）")
    # 假引擎 ②：什么都不输出 → LoadOneShotSnapshot 直接 return → _last 仍是 null
    rc2, head2, st2 = shot_with_fake_engine('import sys\nsys.stdout.write("")\n',
                                            os.path.join(ds.state_dir(), "panel-overview-null.png"),
                                            "空表注入：空 stdout")
    colors2 = shot_dims(head2)[2]
    check("空表注入：空 stdout（快照整个是 null） 仍出图且退 0",
          rc2 == 0 and colors2 > 20 and st2 is not None, f"退出码 {rc2} 输出 {head2!r}")
    if st2:
        check("空表注入：空 stdout（快照整个是 null） 表壳仍在但没有数据行",
              st2["painted"] / st2["total"] >= 0.35 and st2["hlines"] < 6,
              f"不透明 {st2['painted']}/{st2['total']} 横线 {st2['hlines']} 条"
              f"（真实 29 行数据实测 17 条，没掉下来就是注入没生效）")


def check_panel_row_data(empty_base: dict | None) -> None:
    """概览页表格的两条硬约束，都用**注入的固定行数**验，本机 ~/.dsh 里有几个会话都无关。

    ① 「有数据行」必须是相对空表那张的量：1 / 3 / 29 行都得比基线高出一截。
       旧版钉 hlines>=6 / ink>=5000 的绝对值，等价于要求本机至少 4 个会话；
    ② 滚动视口必须**高度受限**：--panel-shot 给页面的是 900×620 的有限约束，
       行数从 3 涨到 29 时绘制区高度必须还钉在视口下沿。
       Content 外面套一层竖向 StackPanel 会把这层约束丢掉（StackPanel 给子元素无限高度），
       DataGrid 于是按内容长到 ~1150px 再被 620 裁掉：画面看起来是满的、门禁完全隐形，
       但表格自己的 ScrollViewer 拿不到有限高度 → 永不滚动，第 16~60 行
       （SESSIONS_IN_SNAPSHOT 上限 60）在真实面板里只能靠整页滚动、表头也一起滚出视野。
    """
    print("\n== 概览页表格：数据行与滚动视口 ==")
    if not os.path.isfile(BAR_EXE):
        print("  （跳过：没有编译产物）")
        return
    if bar_processes():
        check("启动前无残留实例", False, "已有 DshBar 在跑")
        return
    if empty_base is None:
        check("空表基线可用（本段两条门禁的前提）", False, "上一轮 --panel-shot 注入没出图")
        return
    out = os.path.join(ds.state_dir(), "panel-overview-rows.png")
    got: dict[int, tuple[int, str, dict | None]] = {}
    for n in (1, 3, 29):
        got[n] = shot_with_fake_engine(fake_engine_source(n), out, f"{n} 行注入")
        st = got[n][2]
        check(f"概览页 {n} 行注入出图", st is not None and shot_dims(got[n][1])[2] > 20,
              f"退出码 {got[n][0]} 输出 {got[n][1]!r}")
        if st:
            print(f"  {n:2d} 行：不透明 {st['painted']}/{st['total']}"
                  f"（{st['painted'] / st['total']:.1%}）非白 {st['ink']}，"
                  f"绘制区 y {st['y0']}..{st['y1']}（高 {st['y1'] - st['y0'] + 1}px），"
                  f"横线 {st['hlines']} 条、右侧最长竖段 {st['vscroll']}px"
                  f" [空表基线 {empty_base['hlines']} 条 / {empty_base['ink']} 非白]")
    # ① 数据行门禁在三种数据量下都不许假红（1 个会话的机器 / 3 个 / 满屏）
    for n in (1, 3, 29):
        st = got[n][2]
        if st is None:
            check(f"概览页 {n} 行的数据行证据高出空表基线", False, "那张图没读出来")
            continue
        check(f"概览页 {n} 行的数据行证据高出空表基线", row_data_gate(st, empty_base),
              f"横线 {empty_base['hlines']}→{st['hlines']}（+{st['hlines'] - empty_base['hlines']}）、"
              f"非白 {empty_base['ink']}→{st['ink']}（+{st['ink'] - empty_base['ink']}），"
              f"判据：至少多 1 条横线、至少多 600 非白像素")
    # 先证明确实多画了行，否则下面「两张一样高」会因为两张都是空表而假绿
    st3, st29 = got[3][2], got[29][2]
    check("注入确实画出了更多行（29 行的横线严格多于 3 行）",
          bool(st3 and st29) and st29["hlines"] > st3["hlines"],
          f"3 行 {st3 and st3['hlines']} 条 / 29 行 {st29 and st29['hlines']} 条")
    # ② 视口高度受限：两张都必须铺满到视口下沿，且高度不随行数变
    viewport = shot_dims(got[29][1])[1]
    if st3 and st29 and viewport > 0:
        h3 = st3["y1"] - st3["y0"] + 1
        h29 = st29["y1"] - st29["y0"] + 1
        check("概览页表格的滚动视口高度受限：行数从 3 到 29 不把画面撑长",
              h3 >= viewport - 2 and h29 >= viewport - 2 and abs(h29 - h3) <= 2,
              f"3 行绘制区高 {h3}px、29 行 {h29}px，视口 {viewport}px："
              f"两张都该钉在视口下沿（多出的行走表格自己的 ScrollViewer）；"
              f"3 行那张矮一截就是外层面板给了无限高度、表格按行数线性撑开再被裁掉")
        # 装不下就得有滚动条：这是「多出的行落在滚动里」最直白的像素证据。
        # 阈值 100 的两侧：修后 29 行实测滑块 302px；未修版（视口无限高，
        # 表格压根不需要滚动条）与修后 3 行（内容装得下）都只有 1~2px 的描边。
        check("概览页 29 行时表格右侧画出滚动条（装不下的行落在滚动里）",
              st29["vscroll"] >= 100,
              f"右侧 24px 条带里最长的同色竖段 {st29['vscroll']}px（滚动条滑块应有几百 px）；"
              f"3 行那张 {st3['vscroll']}px 作对照 —— 竖段掉到个位数就是视口没被限住、"
              f"行是被裁掉而不是被滚动")
    else:
        check("概览页表格的滚动视口高度受限：行数从 3 到 29 不把画面撑长", False,
              f"样本不全：3 行={st3 is not None} 29 行={st29 is not None} 视口={viewport}")


def check_bar_null_records() -> None:
    """引擎可以出 `"records": null`（dsh_state.py 的 _session_rows 走 s.get("records")），
    那时 `SessionRow.Records` 若是**非可空 int**，System.Text.Json 是在**整帧**上抛的，
    而 StateClient.ReadStdout 的 catch 只记一行「JSON 解析失败」就 continue ——
    表现是状态栏从此静默冻结，不是变红，谁也不会注意到。

    所以 Records 必须是 int?（null 时表格那一格留白，与 Turn 同行为）。
    验法：把产物里的引擎换成一个**只吐 records:null 帧**的 --watch 假引擎，起真状态栏，
    假引擎第一帧圆点 #7C3AED、之后一直是 #16A34A；状态栏画得出第二帧的色，
    才说明这一类帧既被收下、又还在持续被收下（不是碰巧第一帧侥幸）。
    与 check_gui 同一条口径：抓帧失败就停，不拿旧帧空转到超时。
    """
    print("\n== records 为 null 的帧不整帧丢弃 ==")
    if not os.path.isfile(BAR_EXE):
        print("  （跳过：没有编译产物）")
        return
    if bar_processes():
        check("启动前无残留实例", False, "已有 DshBar 在跑")
        return
    eng = os.path.join(os.path.dirname(BAR_EXE), "dsh_state.py")
    if not check("假引擎注入点就位", os.path.isfile(eng), eng):
        return
    with open(os.path.join(HERE, "dsh_state.py"), "rb") as fh:
        real = fh.read()
    log = os.path.join(ds.state_dir(), "bar.log")
    log_offset = os.path.getsize(log) if os.path.isfile(log) else 0
    before = python_pids()
    shot = os.path.join(ds.state_dir(), "smoke-nullrec-bar.png")
    # (0x16,0xA3,0x4A)：假引擎第二帧的圆点色，真实状态表里没有这个值
    target = (22, 163, 74)
    hit, frames, capped = 0, 0, False
    try:
        fake = (
            'import json, sys, time\n'
            'row = {"key": "smoke-nullrec", "project": "冒烟空记录", "title": "records 是 null",\n'
            '       "state": "idle", "turn": 3, "step": 1, "age_sec": 12.5,\n'
            '       "last_event": "result", "last_tool": "Bash", "end_reason": None,\n'
            '       "records": None, "todo": None, "usage_total": None, "pending": None}\n'
            'def frame(color):\n'
            '    return {"ok": True, "app_running": True, "state": "idle",\n'
            '            "label": "冒烟空记录", "glyph": "·", "color": color,\n'
            '            "strip_left": "冒烟空记录", "strip_right": "--",\n'
            '            "tooltip": "冒烟空记录", "tip_lines": ["冒烟空记录"],\n'
            '            "session": None, "balance": None, "waiting": [], "recent": [],\n'
            '            "sessions_scanned": 1, "sessions": [row]}\n'
            'def emit(color):\n'
            '    sys.stdout.write(json.dumps(frame(color), ensure_ascii=False) + "\\n")\n'
            '    sys.stdout.flush()\n'
            'try:\n'
            '    emit("#7C3AED")\n'
            '    for _ in range(240):\n'
            '        emit("#16A34A")\n'
            '        time.sleep(0.5)\n'
            'except (BrokenPipeError, ValueError, OSError):\n'
            '    pass\n'
        )
        with open(eng, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(fake)
        proc = subprocess.Popen([BAR_EXE], cwd=os.path.dirname(BAR_EXE))
        hwnds: list[int] = []
        deadline = time.time() + 25
        while time.time() < deadline:
            hwnds = find_bar_windows()
            if hwnds and proc.poll() is None:
                break
            time.sleep(0.4)
        if not check("records:null 场景：状态栏起来了", bool(hwnds) and proc.poll() is None,
                     f"窗口 {len(hwnds)} 个，进程退出码 {proc.returncode}"):
            return
        deadline = time.time() + 20.0
        while True:
            cw, ch, rgb = capture_bar(hwnds[0], shot)
            frames += 1
            if not (cw and ch):
                capped = True      # 抓不到画面就停，绝不能拿上一帧的 rgb 继续比
                break
            hit = sum(1 for i in range(0, len(rgb), 3)
                      if all(abs(rgb[i + k] - target[k]) <= 12 for k in range(3)))
            if hit >= 20 or time.time() >= deadline:
                break
            time.sleep(1.0)
        tail = log_tail(log_offset)
        check("records:null 帧仍被状态栏接收（圆点画成第二帧的 #16A34A）",
              hit >= 20,
              f"{hit} 像素命中（抓帧 {frames} 次"
              + ("，capture_bar 失败已停手" if capped else "，等满 20 秒也没追上第二帧")
              + f"）；日志新增 {len(tail)} 字，JSON 解析失败出现 "
                f"{'1 次以上' if 'JSON 解析失败' in tail else '0 次'}")
        check("records:null 帧不触发整帧 JSON 解析失败",
              "JSON 解析失败" not in tail,
              f"日志尾巴：{tail[-240:]!r}（非可空 int 撞上 null 就是这一行，然后整帧被丢掉）")
    finally:
        subprocess.run(["taskkill", "/f", "/im", "DshBar.exe"], capture_output=True)
        with open(eng, "wb") as fh:
            fh.write(real)
        check("records:null 注入后引擎拷贝已按仓库根还原",
              open(eng, "rb").read() == real, eng)
        if os.path.isfile(shot):
            os.remove(shot)
        left_pids = python_pids() - before
        t0 = time.time()
        while left_pids and time.time() - t0 < 20:
            time.sleep(1)
            left_pids = python_pids() - before
        check("records:null 场景：退出后无引擎孤儿子进程", not left_pids, f"仍在跑 {sorted(left_pids)}")
        check("records:null 场景：退出后 DshBar 已消失", not bar_processes(), str(bar_processes()))


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]


class _SENDINPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


def send_wheel(delta: int, clicks: int) -> int:
    """SendInput 发几次竖向滚轮（MOUSEEVENTF_WHEEL=0x0800），返回成功次数。

    只发滚轮、不动光标 —— 光标由调用处 SetCursorPos 放到目标上，WPF 按光标命中路由滚轮。
    delta 是 DWORD 字段，负数必须按无符号塞进去（本机踩过：直接传 -360 ctypes 会拒）。
    """
    u = _user32()
    ok = 0
    for _ in range(clicks):
        ev = _SENDINPUT(0, _INPUTUNION(_MOUSEINPUT(0, 0, ctypes.c_uint(delta).value, 0x0800, 0, None)))
        if u.SendInput(1, ctypes.byref(ev), ctypes.sizeof(ev)):
            ok += 1
        time.sleep(0.18)
    return ok


def row_hashes(rgb: bytes, w: int, h: int) -> list[str]:
    """逐行指纹：判断「滚动之后画面哪些行变了」，两帧一比就知道从哪一行开始动。"""
    return [hashlib.md5(rgb[y * w * 3:(y + 1) * w * 3]).hexdigest() for y in range(h)]


def real_window_stats(rgb: bytes, w: int, h: int) -> dict:
    """真实窗口一帧的结构量：表格顶沿、表头文字那条墨迹带、成排的横向分隔线、
    最右条带里的整页滚动条滑块像素。

    横向探测带取 30%~90%：左边避开 188 逻辑像素的侧栏，右边避开窗口最右那 1px 描边。
    顶沿用「纯白占多数」认定 —— 那是 Pages.cs 里写死的 `Background = Brushes.White`，
    Task 5 Step 0 把它换成主题刷时这条口径要跟着改；找不到顶沿返回 -1，
    调用处记 ✗，不会静默假绿。

    「分隔线」= 这一行被同一种非白色占掉一半以上，且上下 4 物理像素内**都**有白底行，
    连续的线行归并成一条。两侧都要贴白底是必需的：表格下面那一大片主题底色
    （本机 #FAFAFA）也是整行同色，不加这道限定空表会被数成 26 条「线」，
    等待条件就成了摆设（本机实测）。125% 缩放下 1 逻辑像素的线会糊成两行
    （实测 y=111 是 #F7F7F7、y=112 是 #E2E2E2），所以按连续段归并、允许几像素缓冲。

    表头下沿**不能**拿「顶沿往下第一条线」当基准 —— 本机实测表头与第一行数据之间
    根本不画线（GridLinesVisibility=Horizontal 只画行与行之间），第一条线在第一行
    数据下面（y=111），拿它当基准会把第一行也圈进「不许动」，修好了照样红。
    改成量表头**文字的墨迹带**：顶沿往下第一段连续墨迹（容 3 行空隙、撞线即停），
    它的下沿才是判据②的基准线（本机实测 y=38..52）。
    """
    x0, x1 = int(w * 0.30), int(w * 0.90)
    band = max(1, x1 - x0)
    white, dom = [0] * h, [(b"", 0)] * h
    for y in range(h):
        base = y * w * 3
        cnt: collections.Counter = collections.Counter()
        for x in range(x0, x1):
            cnt[rgb[base + x * 3:base + x * 3 + 3]] += 1
        white[y] = cnt.get(b"\xff\xff\xff", 0)
        del cnt[b"\xff\xff\xff"]
        dom[y] = cnt.most_common(1)[0] if cnt else (b"", 0)

    def is_white(y: int) -> bool:
        return 0 <= y < h and white[y] * 5 >= band * 2

    def is_line(y: int) -> bool:
        return 0 <= y < h and dom[y][1] * 2 >= band

    def near_white(y: int) -> bool:
        return (any(is_white(y - k) for k in (2, 3, 4))
                and any(is_white(y + k) for k in (2, 3, 4)))

    def has_ink(y: int) -> bool:
        """有文字墨迹：非白像素成把，但不是整行的线。"""
        return 0 <= y < h and not is_line(y) and band - white[y] >= 40

    top = next((y for y in range(h) if is_white(y)), -1)
    groups, y = [], top + 1 if top >= 0 else h
    while y < h:
        if is_line(y) and near_white(y):
            groups.append(y)
            while y < h and is_line(y):
                y += 1
            continue
        y += 1
    head0 = next((y for y in range(max(0, top), h) if has_ink(y)), -1)
    head1, gap = head0, 0
    y = head0
    while head0 >= 0 and y + 1 < h:
        y += 1
        if has_ink(y):
            head1, gap = y, 0
        elif is_line(y) or gap >= 3:
            break
        gap += 1
    gray, gray_x0 = 0, -1
    for y in range(max(0, top), h):
        base = y * w * 3
        for x in range(max(x0, w - 20), w - 2):
            r0, g0, b0 = rgb[base + x * 3], rgb[base + x * 3 + 1], rgb[base + x * 3 + 2]
            if abs(r0 - g0) <= 8 and abs(g0 - b0) <= 8 and 0x55 <= r0 <= 0xD0:
                gray += 1
                gray_x0 = x if gray_x0 < 0 else gray_x0
    return {"top": top, "lines": len(groups), "first_line": groups[0] if groups else -1,
            "head0": head0, "head1": head1, "gray": gray, "gray_x0": gray_x0}


def check_panel_real_scroll() -> None:
    """真实窗口这一侧：外壳必须给页面**有限高度**，让表头固定、只有表体滚动。

    为什么离屏那两条（check_panel_row_data）守不住这件事：`--panel-shot` 是直接把页面
    Measure/Arrange 到 900×620 上出图的，**压根不经过 PanelWindow 的外壳**。外壳一旦把
    Host 包在 ScrollViewer 里，页面拿到的竖向约束就是无限高，表格按行数长到 ~1150px
    再由整页滚动兜着 —— 表头跟着一起滚出视野、排版成本随行数线性上升，而离屏门禁全绿。
    所以这一段起的是真窗口（`--panel`）、看的是真窗口里滚轮滚过之后的画面。

    三条判据（本机实测数字见 task-4-report.md 轮 3）：
      ① 往表体里发滚轮，画面必须真的动起来 —— 它是②的防空转：只看②的话
         「什么都没滚」也满足「表头没变」，那正是假绿；
      ② 动的那一段必须**从表头下方才开始**：表格顶沿到「表头文字墨迹带的下沿」
         这一整条带一个像素都不许变。未修版实测第一条变化在 y=19（表头那一带），
         修后是 y=85（第一行数据里）—— 整页滚动一动表头就花，这正是复核说的那半条；
      ③ 外壳最右侧条带里不许有整页滚动条滑块 —— 页面拿到有限高度时它没有存在的理由。
         未修版实测 2486 个中灰滑块像素（贴着窗口右沿 x=1189），修后 0。
    """
    print("\n== 真实窗口：表头固定、只有表体滚动 ==")
    if not os.path.isfile(BAR_EXE):
        print("  （跳过：没有编译产物）")
        return
    if bar_processes():
        check("启动前无残留实例", False, "已有 DshBar 在跑")
        return
    eng = os.path.join(os.path.dirname(BAR_EXE), "dsh_state.py")
    if not check("真实窗口注入点就位", os.path.isfile(eng), eng):
        return
    with open(os.path.join(HERE, "dsh_state.py"), "rb") as fh:
        real = fh.read()
    before = python_pids()
    shot = os.path.join(ds.state_dir(), "smoke-panel-real.png")
    u = _user32()
    home = wt.POINT()
    u.GetCursorPos(ctypes.byref(home))
    try:
        with open(eng, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(fake_engine_source(29, watch=True))
        subprocess.Popen([BAR_EXE, "--panel"], cwd=os.path.dirname(BAR_EXE))
        hwnd, t0 = 0, time.time()
        while time.time() - t0 < 25:
            hwnd = top_window("dsh 控制台")
            if hwnd:
                break
            time.sleep(0.4)
        if not check("真实窗口：面板已打开（--panel 起真窗口）", bool(hwnd),
                     "找不到标题为 dsh 控制台 的顶层窗口"):
            return
        # 滚轮消息 Windows 是发给**前台窗口**的，面板没到前台时整个实验会静默不动。
        # Task 5 起本探测环境（agent 后台终端，前台锁归 IDE）里裸 SetForegroundWindow
        # 会返回 0 —— 红因正是 Task 4 §12.7 顾虑 2 预言的「面板抢不到前台 → 判据①
        # 一行都没动」。补一层标准兜底：先 keybd_event 模拟一次 ALT 输入（Windows 允许
        # 「刚收到输入事件的进程」改前台），再抢一次。只动送达，不动任何判据与阈值。
        if not u.SetForegroundWindow(hwnd):
            k = ctypes.windll.user32.keybd_event
            k(0x12, 0, 0, 0)
            k(0x12, 0, 2, 0)
            u.SetForegroundWindow(hwnd)
        time.sleep(0.6)
        l, t, r, b = rect_of(hwnd)
        scale = (u.GetDpiForWindow(hwnd) or 96) / 96.0
        st0: dict | None = None
        w = h = 0
        rgb = b""
        deadline = time.time() + 20
        while time.time() < deadline:
            w, h, rgb = capture_bar(hwnd, shot, scale=scale)
            if w:
                st0 = real_window_stats(rgb, w, h)
                if st0["top"] >= 0 and st0["head0"] > st0["top"] and st0["lines"] >= 4:
                    break
            time.sleep(1.0)
        # 先证明「表格真的画上了数据行 + 表头那条墨迹带找得到」，
        # 否则后面「滚不动」说不清是谁的锅，判据②也没有基准线。
        if not check("真实窗口：概览页画出了数据行（滚动实验的前提）",
                     bool(st0) and st0["top"] >= 0 and st0["head0"] > st0["top"]
                     and st0["lines"] >= 4,
                     f"抓取 {w}x{h}（缩放 {scale}），表格顶沿 y={st0 and st0['top']}、"
                     f"表头墨迹带 y={st0 and st0['head0']}..{st0 and st0['head1']}、"
                     f"横向分隔线 {st0 and st0['lines']} 条：等满 20 秒也没成排，"
                     f"说明假引擎那 29 行没喂进面板"):
            return
        sig0 = row_hashes(rgb, w, h)
        px, py = l + int((r - l) * 0.6), t + int((b - t) * 0.6)
        u.SetCursorPos(px, py)
        time.sleep(0.5)
        sent = send_wheel(-120 * 3, 3)
        time.sleep(1.0)
        w2, h2, rgb2 = capture_bar(hwnd, shot, scale=scale)
        if not check("真实窗口：滚动前后画面同尺寸可比",
                     sent == 3 and (w2, h2) == (w, h) and bool(rgb2),
                     f"滚轮发出 {sent}/3 次，抓帧 {w2}x{h2}（基线 {w}x{h}）"):
            return
        sig1 = row_hashes(rgb2, w2, h2)
        changed = [y for y, (a, c) in enumerate(zip(sig0, sig1)) if a != c]
        first_diff = changed[0] if changed else -1
        in_head = sum(1 for y in changed if st0["top"] <= y <= st0["head1"])
        st1 = real_window_stats(rgb2, w2, h2)
        print(f"  基线：表格顶沿 y={st0['top']}、表头墨迹带 y={st0['head0']}..{st0['head1']}、"
              f"分隔线 {st0['lines']} 条、最右条带滑块像素 {st0['gray']}（x0={st0['gray_x0']}）")
        print(f"  滚轮 3 次 ×{-120 * 3} 后：变化行 {len(changed)} 条、第一条变化在 y={first_diff}、"
              f"落在表头带 [y{st0['top']}, y{st0['head1']}] 里的有 {in_head} 条、"
              f"分隔线 {st1['lines']} 条、最右条带滑块像素 {st1['gray']}")
        check("真实窗口：滚轮在表体上滚得动（画面确实动了，不是什么都没发生）",
              len(changed) >= 100,
              f"{len(changed)} 行变化（阈值 100 行 ≈ 表体的一大截）；"
              f"一行都没动就是滚轮被吞或没送达 —— 那 29 行在真实面板里根本够不着")
        check("真实窗口：滚轮滚过之后表头没有被滚出视野",
              first_diff > st0["head1"],
              f"第一条变化的行在 y={first_diff}，判据要求 > 表头墨迹带下沿 y={st0['head1']}"
              f"（顶沿 y={st0['top']}、表头文字 y={st0['head0']}..{st0['head1']}）："
              f"顶沿到表头下沿之间就是表头，整页滚动一动它就花；"
              f"变化行为 -1 就是压根没滚起来（见上一条）")
        check("真实窗口：外壳没有整页滚动条（页面拿到的是有限高度）",
              st0["gray"] <= 60,
              f"最右侧 20 物理像素条带里 {st0['gray']} 个中灰滑块像素"
              f"（最早出现在 x={st0['gray_x0']}，窗口宽 {w}）："
              f"外壳把页面包进 ScrollViewer 时整页滚动条就贴着窗口右沿；"
              f"有限高度下页面不需要它，那一条带只剩主题底色")
    finally:
        # 这一段会 SetCursorPos 把光标挪到表格上（滚轮要按光标命中路由），
        # 不管前面走到哪一步、有没有抛，都得把用户的光标还回去。
        u.SetCursorPos(home.x, home.y)
        subprocess.run(["taskkill", "/f", "/im", "DshBar.exe"], capture_output=True)
        with open(eng, "wb") as fh:
            fh.write(real)
        check("真实窗口场景：注入后引擎拷贝已按仓库根还原",
              open(eng, "rb").read() == real, eng)
        if os.path.isfile(shot):
            os.remove(shot)
        left_pids = python_pids() - before
        t2 = time.time()
        while left_pids and time.time() - t2 < 20:
            time.sleep(1)
            left_pids = python_pids() - before
        check("真实窗口场景：退出后无引擎孤儿子进程", not left_pids, f"仍在跑 {sorted(left_pids)}")
        check("真实窗口场景：退出后 DshBar 已消失", not bar_processes(), str(bar_processes()))


def check_panel_window() -> None:
    print("\n== 控制台窗口 ==")
    if not os.path.isfile(BAR_EXE) or bar_processes():
        print("  （跳过：无产物或已有实例）")
        return
    proc = subprocess.Popen([BAR_EXE, "--panel"], cwd=os.path.dirname(BAR_EXE))
    try:
        u = _user32()
        found, t0 = None, time.time()
        while time.time() - t0 < 25:
            found = top_window("dsh 控制台")
            if found:
                break
            time.sleep(0.4)
        check("面板窗口已出现", bool(found), "找不到标题为 dsh 控制台 的顶层窗口")
        if found:
            style = u.GetWindowLongW(found, -16)
            l, t, r, b = rect_of(found)
            check("面板是正常顶层窗口", not (style & 0x40000000) and bool(style & 0x10000000),
                  hex(style))
            check("面板尺寸合理", (r - l) >= 720 and (b - t) >= 480, f"{r-l}x{b-t}")
        # 关窗应当只是隐藏，并能再次唤起（spec §3）
        # 用 PostMessageW：user32.dll 只导出 PostMessageA/PostMessageW，
        # 不带后缀的 PostMessage 是 Win32 头文件里的宏，ctypes 取不到（AttributeError）。
        u.PostMessageW(found, 0x0010, 0, 0)  # WM_CLOSE
        time.sleep(1.5)
        check("关窗后进程仍在（只是隐藏，没销毁）", bool(bar_processes()), "进程跟着退出了")
        check("关窗后面板不再可见", not top_window("dsh 控制台"), "窗口还看得见")
        subprocess.run([BAR_EXE, "--panel", "about"], cwd=os.path.dirname(BAR_EXE),
                       timeout=30)
        t1 = time.time()
        while time.time() - t1 < 20 and not top_window("dsh 控制台"):
            time.sleep(0.4)
        check("再次请求能重新打开面板", bool(top_window("dsh 控制台")), "面板没能再打开")
    finally:
        subprocess.run(["taskkill", "/f", "/im", "DshBar.exe"], capture_output=True)
        time.sleep(3)
        check("退出后无残留", not bar_processes(), str(bar_processes()))


def check_settings_roundtrip() -> None:
    """settings.json 的 theme/showBar 必须真的被读回、被外观页用起来，并且主题真的改像素。

    Task 3 复核 N3：`ControlsDictionary`+`ThemesDictionary` 的合并当时没有任何能红的门禁
    （删掉合并调用全套照绿）。这条链在这里补上：
    theme=dark 与 theme=light 各出一张 appearance 离屏图，四角背景取样必须不同。
    占位页（Task 4 基线）整页透明、深浅两张一模一样，所以这条在未修版上是红的；
    把 App.ApplyFluentTheme 里的字典合并删掉、或不挂 ScrollViewer 的主题底色，它也会翻红
    —— 它守的是「theme 字段 → Apply → 主题字典 → 页面换色」整条链，不是形式。
    showBar=False 一并写进每份临时 settings：shot 路径会构造外观页、ToggleSwitch 会读它，
    顺带钉住「隐藏状态条时离屏渲染不崩」（ApplyBarVisibility 在没有真窗口时只记日志）。
    """
    print("\n== 设置往返 ==")
    if not os.path.isfile(BAR_EXE):
        print("  （跳过：没有编译产物）")
        return
    f = os.path.join(ds.state_dir(), "settings.json")
    backup = open(f, encoding="utf-8").read() if os.path.isfile(f) else None
    bg: dict[str, list[bytes] | None] = {}
    colors: dict[str, int] = {}
    opaque: dict[str, int] = {}
    try:
        for theme in ("dark", "system", "light"):
            with open(f, "w", encoding="utf-8") as fh:
                json.dump({"interval": 3, "notify": False, "repeatSec": 45,
                           "theme": theme, "showBar": False}, fh)
            out = os.path.join(ds.state_dir(), f"panel-appearance-{theme}.png")
            if os.path.isfile(out):
                os.remove(out)
            p = run_shot("appearance", out)
            rc = -1 if p is None else p.returncode
            head = "" if p is None else ((p.stdout or "").strip().splitlines() or [""])[0]
            tail = "" if p is None else (p.stdout + p.stderr)[-160:]
            check(f"theme={theme} 能被读回并渲染", rc == 0 and "SHOT" in head,
                  f"退出码 {rc} {tail!r}" if p else "120 秒没返回（离屏渲染卡死）")
            if theme in ("dark", "light"):
                bg[theme] = shot_bg_samples(out)
                colors[theme] = shot_dims(head)[2]
                opaque[theme] = shot_opaque_count(out)
            if os.path.isfile(out):
                os.remove(out)

        # 非法 theme：Load 回退 light。除了不崩，还要求它的四角取样和 theme=light 一致——
        # 「回退浅色」如果只回退了字段而渲染照旧，上面那条 SHOT 断言看不出来。
        with open(f, "w", encoding="utf-8") as fh:
            fh.write('{"theme": "nonsense", "interval": 0}')
        out_bad = os.path.join(ds.state_dir(), "panel-appearance-bad.png")
        p = run_shot("appearance", out_bad)
        rc = -1 if p is None else p.returncode
        head = "" if p is None else ((p.stdout or "").strip().splitlines() or [""])[0]
        check("非法 theme 回退浅色且不崩", rc == 0 and "SHOT" in head,
              f"退出码 {rc} {(p.stdout + p.stderr)[-160:]!r}" if p else "120 秒没返回")
        bg_bad = shot_bg_samples(out_bad)
        if os.path.isfile(out_bad):
            os.remove(out_bad)
        if check("非法 theme 场景的离屏图可解码（回退比样的前提）", bg_bad is not None, out_bad):
            check("非法 theme 的渲染结果与 theme=light 一致（回退真的落到浅色）",
                  bg_bad == bg.get("light"),
                  f"非法={','.join(fmt_rgba(c) for c in bg_bad)} vs "
                  f"light={','.join(fmt_rgba(c) for c in bg.get('light') or [b''])}")

        dark_s, light_s = bg.get("dark"), bg.get("light")
        if check("深浅两张 appearance 离屏图都可解码（主题生效门禁的前提）",
                 dark_s is not None and light_s is not None,
                 f"dark={dark_s is not None} light={light_s is not None}"):
            check("theme=dark 与 theme=light 的 appearance 背景色不同（主题真的生效）",
                  dark_s != light_s,
                  f"dark={','.join(fmt_rgba(c) for c in dark_s)} vs "
                  f"light={','.join(fmt_rgba(c) for c in light_s)}"
                  "（占位页四角全透明、深浅一模一样，这条在 Task 4 基线必红；"
                  "删掉 Fluent 字典合并或 ApplyTheme 挂掉时也会翻红 —— N3 的补口）")
            # 色数与不透明只作地板值（Task 4 的裁决口径）：证明这张页真的画了东西，
            # 而不是一块主题底色糊出来 3 个色。
            check("appearance 页已画出内容（色数 > 20，Task 5 Step 5 的地板值）",
                  colors.get("light", -1) > 20 and colors.get("dark", -1) > 20,
                  f"light={colors.get('light')} dark={colors.get('dark')}")
            check("appearance 页不再是透明占位（两张都画出了不透明像素）",
                  opaque.get("dark", -1) > 0 and opaque.get("light", -1) > 0,
                  f"不透明像素 dark={opaque.get('dark')} light={opaque.get('light')}"
                  "（占位页实测 0）")
    finally:
        if backup is None:
            if os.path.isfile(f):
                os.remove(f)
        else:
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(backup)


def check_showbar_hidden() -> None:
    """showBar=false 的真实行为：状态条从任务栏消失，但进程/托盘/watchdog 全部活着，
    面板照常可被唤起；改回 true 再打开时重新停靠。

    托盘图标本身没法从 Win32 层枚举（Win11 的托盘是 XAML 岛，没有逐图标 HWND），
    所以「托盘仍在」用三重间接证据：进程活着 + watchdog 仍在消费 panel.request
    （和托盘菜单「打开面板」走的是同一个 OpenPanel）+ 面板窗口仍在并可查询。
    """
    print("\n== showBar=false 隐藏状态条 ==")
    if not os.path.isfile(BAR_EXE) or bar_processes():
        print("  （跳过：无产物或已有实例）")
        return
    f = os.path.join(ds.state_dir(), "settings.json")
    backup = open(f, encoding="utf-8").read() if os.path.isfile(f) else None
    req = os.path.join(ds.state_dir(), "panel.request")
    try:
        with open(f, "w", encoding="utf-8") as fh:
            json.dump({"interval": 2, "notify": False, "repeatSec": 90,
                       "theme": "light", "showBar": False}, fh)
        proc = subprocess.Popen([BAR_EXE, "--panel", "appearance"], cwd=os.path.dirname(BAR_EXE))
        hwnd, t0 = 0, time.time()
        while time.time() - t0 < 25:
            hwnd = top_window("dsh 控制台")
            if hwnd:
                break
            time.sleep(0.4)
        check("showBar=false：面板仍能打开（--panel 直达）", bool(hwnd),
              "找不到标题为 dsh 控制台 的顶层窗口")
        # 内容证据：--panel appearance 必须真的切到外观页。Task 3 的 Navigate 把
        # SelectedIndex 赋值和渲染一起压掉了（导航条亮「外观」、Host 里还是概览页），
        # 概览页是整块 #FFFFFF 白底表格，外观页浅底下没有成片的纯白 —— 按纯白像素分得开。
        if hwnd:
            u = _user32()
            scale = (u.GetDpiForWindow(hwnd) or 96) / 96.0
            shot2 = os.path.join(ds.state_dir(), "smoke-showbar-panel.png")
            cw, chh, rgb = capture_bar(hwnd, shot2, scale=scale)
            whites = 0
            if cw:
                for y in range(int(chh * 0.06), int(chh * 0.96)):
                    base = y * cw * 3
                    for x in range(int(cw * 0.25), int(cw * 0.95)):
                        if rgb[base + x * 3:base + x * 3 + 3] == b"\xff\xff\xff":
                            whites += 1
            check("面板真的切到了 appearance 页（不是停在概览白底表）",
                  bool(cw) and whites < 5000,
                  f"内容区纯白像素 {whites}（概览页表格实测成片几十万；外观页只有单选圈/滑块"
                  f"高光这类零星纯白），抓帧 {cw}x{chh}")
            if os.path.isfile(shot2):
                os.remove(shot2)
        bars = find_bar_windows()
        check("showBar=false：任务栏里没有停靠的状态条（真的消失了）", not bars,
              f"Shell_TrayWnd 下仍挂着 {len(bars)} 个 DshBar 子窗口")
        check("showBar=false：进程仍活着（托盘与 watchdog 都在）", proc.poll() is None,
              f"退出码 {proc.returncode}")
        # 隐藏期间二次实例的转交必须仍被消费——它和托盘菜单「打开面板」是同一个 OpenPanel。
        if os.path.isfile(req):
            os.remove(req)
        subprocess.run([BAR_EXE, "--panel", "about"], cwd=os.path.dirname(BAR_EXE), timeout=30)
        t1 = time.time()
        while time.time() - t1 < 10 and os.path.isfile(req):
            time.sleep(0.4)
        check("showBar=false：panel.request 仍被隐藏中的进程消费（唤起通路活着）",
              not os.path.isfile(req), "10 秒后请求文件还在，watchdog 没干活")
        check("showBar=false：面板窗口仍在（能从唤起回到面板）", bool(top_window("dsh 控制台")),
              "请求消费了但窗口不在了")
        subprocess.run(["taskkill", "/f", "/im", "DshBar.exe"], capture_output=True)
        t2 = time.time()
        while time.time() - t2 < 20 and bar_processes():
            time.sleep(0.5)
        # 改回 showBar=true 再打开：必须重新停靠进任务栏。
        with open(f, "w", encoding="utf-8") as fh:
            json.dump({"interval": 2, "notify": False, "repeatSec": 90,
                       "theme": "light", "showBar": True}, fh)
        subprocess.Popen([BAR_EXE], cwd=os.path.dirname(BAR_EXE))
        hwnds: list[int] = []
        t3 = time.time()
        docked = False
        while time.time() - t3 < 25:
            hwnds = find_bar_windows()
            if hwnds:
                u = _user32()
                tb = u.FindWindowW("Shell_TrayWnd", None)
                style = u.GetWindowLongW(hwnds[0], -16)
                if style & 0x10000000 and u.GetParent(hwnds[0]) == tb:
                    docked = True
                    break
            time.sleep(0.4)
        check("showBar=true 再打开时状态条重新停靠", docked,
              f"找到 {len(hwnds)} 个子窗口，等满 25 秒也没出现可见且父子关系正确的状态条")
    finally:
        subprocess.run(["taskkill", "/f", "/im", "DshBar.exe"], capture_output=True)
        time.sleep(3)
        if backup is None:
            if os.path.isfile(f):
                os.remove(f)
        else:
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(backup)
        check("showBar 场景：退出后无残留", not bar_processes(), str(bar_processes()))


def check_panel_cold_open() -> None:
    """冷打开：常驻实例是不带 --panel 起的、面板从来没创建过。

    check_panel_window() 用 `DshBar.exe --panel` 起的正是常驻实例本身，
    PanelWindow.Instance 在它的构造函数里就被置上了，所以那条链路从没覆盖过
    「Instance 还是 null、只能惰性创建」的路径 —— 而日常最常见的情形恰恰是它：
    开机自启的普通 DshBar.exe + 用户后来敲一次 `DshBar.exe --panel`。
    """
    print("\n== 面板冷打开 ==")
    if not os.path.isfile(BAR_EXE) or bar_processes():
        print("  （跳过：无产物或已有实例）")
        return
    req = os.path.join(ds.state_dir(), "panel.request")
    if os.path.isfile(req):
        os.remove(req)  # 上一轮残留的请求会让常驻实例一起来就自己把面板弹出来
    check("冷打开前无残留请求文件", not os.path.exists(req), req)
    before = python_pids()
    proc = subprocess.Popen([BAR_EXE], cwd=os.path.dirname(BAR_EXE))
    try:
        t0 = time.time()
        while time.time() - t0 < 25 and not find_bar_windows():
            time.sleep(0.4)
        hwnds = find_bar_windows()
        check("常驻实例已起（不带 --panel）", proc.poll() is None and bool(hwnds),
              f"存活={proc.poll() is None} 状态栏窗口={hwnds}")
        time.sleep(3)  # 让 watchdog 至少跑过一轮，确认面板不是它自己冒出来的
        check("冷打开前面板不存在", not top_window("dsh 控制台"), "状态栏一起来面板就在")
        subprocess.run([BAR_EXE, "--panel", "balance"], cwd=os.path.dirname(BAR_EXE),
                       timeout=30)
        found, t1 = 0, time.time()
        while time.time() - t1 < 25 and not (found := top_window("dsh 控制台")):
            time.sleep(0.4)
        check("冷打开：--panel 唤起面板", bool(found),
              "常驻实例的 PanelWindow.Instance 是 null，请求文件被删掉却没人开窗")
        # 「先开窗、开成了才删」是 Task 3 定的语义，于是文件天生比窗口晚一步落地：
        # 本机 tight-poll 实测窗口可见 → 文件消失稳定差 0.21~0.23s（Activate + 复核身份 + Delete）。
        # 原来这里在看见窗口的那一刻就采样一次，0.4s 的轮询相位一撞进这 0.2s 的空档就假红
        # ——断言要的是「请求最终被消费掉」，那就等到它没了为止，超时才算失败。
        t2 = time.time()
        while time.time() - t2 < 10 and os.path.exists(req):
            time.sleep(0.2)
        check("冷打开：请求文件已消费", not os.path.exists(req),
              f"{req}（开窗后 {time.time() - t2:.1f} 秒仍未删除；"
              f"实测正常删除就在开窗后 0.2 秒上下）")
    finally:
        subprocess.run(["taskkill", "/f", "/im", "DshBar.exe"], capture_output=True)
        deadline = time.time() + 20
        while time.time() < deadline and (bar_processes() or python_pids() - before):
            time.sleep(1)
        check("冷打开后无残留", not bar_processes() and not (python_pids() - before),
              f"DshBar={sorted(bar_processes())} python={sorted(python_pids() - before)}")


def lock_against_delete(path: str) -> int:
    """占住文件：自己能读、别的进程删不掉，制造一个确定性的「删除失败」。

    共享模式给 FILE_SHARE_READ|WRITE 但不给 FILE_SHARE_DELETE —— 另一个进程的
    File.ReadAllText（.NET 用 FileShare.Read）照样成功，File.Delete 必然撞
    ERROR_SHARING_VIOLATION。不能用 Python 的 open()/os.open()：共享模式由 UCRT
    决定，带不带 FILE_SHARE_DELETE 不可控，注入就成了薛定谔的注入。
    返回 0 表示占用失败。
    """
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = wt.HANDLE
    k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                wt.DWORD, wt.DWORD, wt.HANDLE]
    h = k32.CreateFileW(path, 0x80000000, 0x00000003, None, 3, 0x80, None)
    return 0 if (h is None or int(h) in (0, -1, 0xFFFFFFFFFFFFFFFF)) else int(h)


def unlock(path_handle: int) -> None:
    if path_handle:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(path_handle))


def request_write(path: str, page: str) -> int:
    """写一条面板请求，返回写完之后立刻看到的 last-access 时间（读 oracle 的基线）。"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return os.stat(path).st_atime_ns


def wait_request_read(path: str, baseline_ns: int, limit: float) -> tuple[float | None, int | None]:
    """等别的进程把这个文件读走。返回 (观察到的时刻, 新的 last-access 值)，没读到 (None, None)。

    NTFS 的 last-access 时间会随一次真正的读刷新（这台机器 DisableLastAccess=2，
    系统盘照常更新），而 os.stat / GetFileAttributesEx 只看元数据、不动它。
    所以它是「Tick 刚把请求读进内存」这件事唯一的外部可见痕迹 ——
    没有它就没法知道自己是不是把第二条请求写进了「读→删」那段窗口里。
    """
    t0 = time.time()
    while time.time() - t0 < limit:
        try:
            at = os.stat(path).st_atime_ns
        except OSError:
            return None, None  # 文件先没了：这一次读无从观察
        if at != baseline_ns:
            return time.time(), at
        time.sleep(0.0002)
    return None, None


def log_tail(offset: int) -> str:
    """读 bar.log 从 offset 起新增的部分（检查函数自己那段时间窗）。"""
    log = os.path.join(ds.state_dir(), "bar.log")
    try:
        with open(log, encoding="utf-8-sig", errors="replace") as fh:
            fh.seek(offset)
            return fh.read()
    except OSError:
        return ""


def check_panel_request_guard() -> None:
    """开窗兜底的源码门禁：委托体必须自带 try，且请求文件必须在开窗成功之后才删。

    为什么用源码形状而不是行为：`new PanelWindow()` 的构造只吃已经在内存里的程序集
    和内嵌 BAML，请求文件里的页名再离谱也会被 PanelPages.IndexOf 兜回概览页 —— 外部
    没有任何「必然让开窗抛」的注入点，而往生产代码塞测试钩子是不允许的。行为侧能注入
    的是「删不掉」，那条走 check_panel_request_containment()；这一条把「委托体没 try
    就红」钉住（把 try 删掉立刻变红，见 Task 3 报告的第 2 轮反证）。
    """
    print("\n== 开窗兜底门禁 ==")
    src = os.path.join(HERE, "bar", "App.cs")
    if not os.path.isfile(src):
        check("App.cs 可读", False, src)
        return
    with open(src, encoding="utf-8-sig", errors="replace") as fh:
        lines = fh.read().splitlines()
    # 站点定位：bar/App.cs 里有三处 BeginInvoke(new Action(...))，前两处是引擎快照回调
    # （单行写法、本来就不该有 try），只有面板请求那一站是「排队到下一轮的开窗委托」。
    # 所以从 TryConsumePanelRequest 之后找，别把 no-op 的那两处当成它。
    site = next((i for i, l in enumerate(lines)
                 if "private static void TryConsumePanelRequest(" in l), -1)
    idx = next((i for i in range(site, min(site + 60, len(lines)))
                if "Dispatcher.BeginInvoke(new Action(" in lines[i]), -1) if site >= 0 else -1
    if not check("找到开窗委托站点", idx >= 0,
                 "TryConsumePanelRequest 里没有 Dispatcher.BeginInvoke(new Action(...))"):
        return
    # 委托体 = BeginInvoke 那行到形如 `}));` 的行。修前那种单行写法没有结束标记，
    # 扫到 40 行也找不到 —— 正好记 ✗，因为「没有独立成块的委托体」= 「委托体没被 try 包住」。
    end = next((j for j in range(idx, min(idx + 40, len(lines)))
                if lines[j].strip() == "}));"), -1)
    block = "\n".join(lines[idx:end + 1]) if end >= 0 else ""
    check("开窗委托体自带 try + catch + Log（N1）",
          bool(block) and "try" in block and "catch (Exception" in block and "Log(" in block,
          "委托体没有独立块或没有 try/catch/Log" if block else "委托体没找到结束标记 `}));`")
    o, d = block.find("OpenPanel("), block.find("TryDeleteRequest(")
    check("请求文件在开窗成功之后才删（N2）",
          o >= 0 and d >= 0 and o < d, f"OpenPanel@{o} TryDeleteRequest@{d}")
    # OpenPanel 自己也得兜住：--panel 启动和托盘菜单两个调用点同样没有外层 try。
    oi = next((i for i, l in enumerate(lines) if l.strip().startswith("static bool OpenPanel(")), -1)
    ob = "\n".join(lines[oi:oi + 18]) if oi >= 0 else ""
    check("OpenPanel 内部兜住异常并返回布尔（N1）",
          "catch (Exception" in ob and "Log(" in ob and "return false;" in ob,
          "没找到 static bool OpenPanel(...) 或它没有 try/catch/Log/return false")

    # ---- 重试预算 PanelOpenTries ----
    # 这三条是**形状断言**，不是行为断言，原因和第 2 轮 N1 那半边一样：
    # OpenPanel 只在 new PanelWindow() 抛的时候才返回 false，而那口锅只有程序集内嵌的
    # BAML 和已在内存的字典能掀 —— 仓库外没有任何「必然让开窗失败」的注入点，
    # 往生产代码塞测试钩子又是不允许的。所以「失败重试最多 2 次」这一支只能钉形态。
    # 会假红的改动（语义没变也得连这条一起改）：把常数改名、把 2 内联进比较式、
    # 给放弃那一支换写法（例如把 Log/删除挪进另一个方法）、把 _reqTries++ 挪到 BeginInvoke 之后。
    # 守不住的改动（形状断言的天花板）：把 2 改成 3 并且连这条一起改 —— 它只钉住「预算是 2」
    # 这个决定，不保证 2 是最佳值；以及在别处把 _reqTries 悄悄重置回 0（预算永远花不完），
    # 那需要「开窗必然失败」的行为注入，而这一支恰恰没有可注入的点。
    text = "\n".join(lines)
    m = re.search(r"private const int PanelOpenTries\s*=\s*(\d+)", text)
    check("重试预算是编译期常数且为 2（形状断言）", bool(m) and m.group(1) == "2",
          "没找到 private const int PanelOpenTries = 2" if not m
          else f"实得 PanelOpenTries = {m.group(1)}（改预算要连同这条一起改，别让它悄悄长大）")
    gi = next((i for i, l in enumerate(lines) if "_reqTries >= PanelOpenTries" in l), -1)
    gb = ""
    if gi >= 0:
        ind = len(lines[gi]) - len(lines[gi].lstrip())
        for j in range(gi + 1, min(gi + 14, len(lines))):
            s = lines[j]
            if s.strip() == "}" and len(s) - len(s.lstrip()) == ind:
                gb = "\n".join(lines[gi:j + 1])
                break
    check("预算用完就收手：记一行 + 标记放弃 + 删文件，且不再开窗（形状断言）",
          bool(gb) and "Log(" in gb and "_reqGivenUp = true" in gb and "TryDeleteRequest(" in gb
          and "BeginInvoke" not in gb and "OpenPanel(" not in gb,
          "没找到 if (_reqTries >= PanelOpenTries) 那一支，或它缺了 Log/放弃标记/删文件，"
          "或者用完还在开窗（那就是每 2 秒抢一次焦点）" if gb else "没有预算用完了那一支")
    seg = "\n".join(lines[site:idx + 1]) if site >= 0 and idx > site else ""
    check("预算按请求身份收敛：新身份清零、每次尝试先自增再排窗（形状断言）",
          "_reqTries = 0" in seg and "_reqTries++" in seg and seg.find("_reqTries++") < seg.find("BeginInvoke"),
          "没看到「身份一变就 _reqTries = 0」，或自增排到了 BeginInvoke 之后（那样第一条请求永远花不掉预算）")


def check_panel_request_containment() -> None:
    """消费失败的反证：请求文件删不掉时，面板要照开、进程要照活、且不许无限重试。

    修前的顺序是先删后开：删除一失败就 `本次不唤起`，于是「面板从没开过 + 每 2 秒
    再撞一次删除」；修后开窗排在删除之前，删除失败只影响清理，不影响请求被满足。
    这条断言盯的就是这个差别，顺带盯住重试收敛（同一请求只开一次窗，不抢焦点）。
    """
    print("\n== 面板请求消费失败 ==")
    if not os.path.isfile(BAR_EXE) or bar_processes():
        print("  （跳过：无产物或已有实例）")
        return
    req = os.path.join(ds.state_dir(), "panel.request")
    if os.path.isfile(req):
        os.remove(req)
    check("消费失败测试前无残留请求", not os.path.exists(req), req)
    log_offset = os.path.getsize(os.path.join(ds.state_dir(), "bar.log")) \
        if os.path.isfile(os.path.join(ds.state_dir(), "bar.log")) else 0
    before = python_pids()
    proc = subprocess.Popen([BAR_EXE], cwd=os.path.dirname(BAR_EXE))
    holder = 0
    try:
        t0 = time.time()
        while time.time() - t0 < 25 and not find_bar_windows():
            time.sleep(0.4)
        hwnds = find_bar_windows()
        check("常驻实例已起（不带 --panel）", proc.poll() is None and bool(hwnds),
              f"存活={proc.poll() is None} 状态栏窗口={hwnds}")
        holder = 0
        for _ in range(6):
            # 写完再占是有一条毫秒级竞态的：watchdog 恰好在这一瞬读到请求就会把它消费掉，
            # 于是 CreateFileW(OPEN_EXISTING) 失败。失败了就重写一遍，别把竞态留给门禁。
            with open(req, "w", encoding="utf-8") as fh:
                fh.write("about")
            holder = lock_against_delete(req)
            if holder and os.path.exists(req):
                break
            unlock(holder)
            holder = 0
            time.sleep(0.2)
        if not check("请求文件已占成删不掉", holder != 0 and os.path.exists(req),
                     "CreateFileW 占用失败，注入没成立"):
            return
        found, t1 = 0, time.time()
        while time.time() - t1 < 25 and not (found := top_window("dsh 控制台")):
            time.sleep(0.4)
        check("删不掉请求文件时面板仍然打开（先开窗后删除，N2）", bool(found),
              "删除排在开窗前面时，这条请求会被整个吞掉")
        # 失败必须记账，但不能反复：只等第一条，再多看几轮确认没有第二条。
        marker = "面板已打开但请求文件删不掉"
        t2 = time.time()
        while time.time() - t2 < 15 and marker not in log_tail(log_offset):
            time.sleep(0.5)
        tail = log_tail(log_offset)
        check("消费失败走 Log 有记录（不抛异常）", marker in tail, tail[-200:] or "无新日志")
        time.sleep(8)  # 再跑 4 轮 watchdog
        tail2 = log_tail(log_offset)
        check("同一请求只开一次窗，没有每 2 秒反复弹（重试已收敛）",
              tail2.count(marker) == 1, f"命中 {tail2.count(marker)} 次")
        check("开窗失败的请求不会被永久丢弃（文件仍在，等清理）",
              os.path.exists(req), "请求文件不见了，下一轮没法重试")
        check("消费失败后进程仍存活（N1）", proc.poll() is None, f"退出码 {proc.returncode}")
        check("消费失败后状态栏仍在任务栏里（N1）", bool(find_bar_windows()),
              "Shell_TrayWnd 里已经没有 DshBar 子窗口")
        check("消费失败后面板仍可见", bool(top_window("dsh 控制台")), "面板跟着没了")
        unlock(holder)
        holder = 0
        t3 = time.time()
        while time.time() - t3 < 15 and os.path.exists(req):
            time.sleep(0.5)
        check("锁释放后残留请求被安静清理", not os.path.exists(req), req)
        tail3 = log_tail(log_offset)
        check("清理残留也记了一行", "已清理" in tail3, tail3[-200:] or "无新日志")
        check("清理后进程仍存活", proc.poll() is None, f"退出码 {proc.returncode}")
    finally:
        if holder:
            unlock(holder)  # 顺序不能反：先释放句柄，否则请求文件删不掉
        subprocess.run(["taskkill", "/f", "/im", "DshBar.exe"], capture_output=True)
        deadline = time.time() + 20
        while time.time() < deadline and (bar_processes() or python_pids() - before):
            time.sleep(1)
        if os.path.exists(req):
            os.remove(req)  # 放弃路径只在能删时才删；测试自己不留垃圾给下一轮
        check("消费失败测试后无残留",
              not bar_processes() and not (python_pids() - before) and not os.path.exists(req),
              f"DshBar={sorted(bar_processes())} python={sorted(python_pids() - before)} req={os.path.exists(req)}")


def check_panel_request_race() -> None:
    """冷开窗期间到达的第二条请求，不能被第一条那次删除顺带吞掉。

    Tick 把请求读进内存之后，还要跳一次 dispatcher、再把整个 PanelWindow 冷构造出来
    （本机实测：读到 → 删掉之间 221ms）。第二个实例趁这段时间写进来的新请求，就是
    「此刻躺在文件里的那一条」。不比对身份就 File.Delete，删掉的是一条从来没打开过的
    请求：面板停在第一页，日志一行都没有。

    时序不靠猜：
      · 读到 = 文件 last-access 时间被别的进程刷新（见 wait_request_read）。
      · 第二条确实写进了窗口 = 它写在「面板出现」之前，而 Show() 在删除之前，
        所以 写第二条 < 面板出现 ≤ 删除，三段全是观测到的先后，不是估的。
      · 没被吞掉 = 文件活到读后 1.5 秒还在。下一条最早只能在读后 2 秒（watchdog 周期）
        被读到，所以 1.5 秒仍在那儿 = 那次删除没碰它。
    """
    print("\n== 冷开窗期间的第二条请求 ==")
    if not os.path.isfile(BAR_EXE) or bar_processes():
        print("  （跳过：无产物或已有实例）")
        return
    req = os.path.join(ds.state_dir(), "panel.request")
    if os.path.isfile(req):
        os.remove(req)
    check("竞态测试前无残留请求", not os.path.exists(req), req)
    log_path = os.path.join(ds.state_dir(), "bar.log")
    log_offset = os.path.getsize(log_path) if os.path.isfile(log_path) else 0
    before = python_pids()
    proc = subprocess.Popen([BAR_EXE], cwd=os.path.dirname(BAR_EXE))
    try:
        t0 = time.time()
        while time.time() - t0 < 25 and not find_bar_windows():
            time.sleep(0.4)
        check("常驻实例已起（不带 --panel）", proc.poll() is None and bool(find_bar_windows()),
              f"存活={proc.poll() is None} 状态栏窗口={find_bar_windows()}")
        time.sleep(3)  # 让 watchdog 跑过一轮：面板不是它自己冒出来的，窗口期才真的是冷的
        if not check("竞态测试前面板不存在（开窗必然是冷构造）", not top_window("dsh 控制台"),
                     "面板已经在了，OpenPanel 不再冷构造，这段窗口本来就不存在"):
            return
        base = request_write(req, "about")
        t_read, _ = wait_request_read(req, base, 10.0)
        if not check("第一条请求被读到（last-access 时间当读 oracle）", t_read is not None,
                     "10 秒内没观察到任何读事件：这台机器关掉了访问时间更新，竞态无从判定"):
            return
        request_write(req, "notify")  # 第二条：赶在那次删除之前落地
        t_second = time.time()
        t_panel = t_gone = 0.0
        still_there = False
        t_end, probed = t_read + 8.0, False
        while time.time() < t_end:
            now = time.time()
            if not t_panel and top_window("dsh 控制台"):
                t_panel = now  # 记下的是「看到的时刻」，不是句柄：后面要拿它跟写入时刻比先后
            if not probed and now - t_read >= 1.5:
                still_there, probed = os.path.exists(req), True
            if not t_gone and not os.path.exists(req):
                t_gone = now
            if t_panel and probed and t_gone:
                break
            time.sleep(0.0005)
        if not probed:
            still_there = os.path.exists(req)
        ms = lambda t: f"{(t - t_read) * 1000:.0f}ms" if t else "从未"  # noqa: E731
        check("第一条请求起了冷面板", bool(t_panel), "读后 8 秒里面板没出现，删不删都无从谈起")
        check("第二条请求写在冷面板出现之前（确实撞进那次开窗）",
              bool(t_panel) and t_second < t_panel,
              f"第二条写于读后 {ms(t_second)}，面板出现于读后 {ms(t_panel)}")
        check("第二条请求没有被那次开窗顺带删掉（删除前比对身份）", still_there,
              f"文件在读后 {ms(t_gone)} 就消失了，而下一条最早也要读后 2 秒（watchdog 周期）"
              "才会被读到 —— 删掉的是一条从来没打开过的请求，且日志没有任何交代")
        check("第二条请求随后被消费，没有悬挂在磁盘上", t_gone != 0.0,
              f"等满 8 秒文件还在（{req}），下一轮的预算会被这条吃掉")
        tail = log_tail(log_offset)
        check("让位给新请求时记了一行（不静默放行）", "被新请求覆盖" in tail,
              tail[-260:] or "无新日志")
        check("竞态后进程仍存活", proc.poll() is None, f"退出码 {proc.returncode}")
        check("竞态后状态栏仍在任务栏里", bool(find_bar_windows()),
              "Shell_TrayWnd 里已经没有 DshBar 子窗口")
        check("竞态后面板可见", bool(top_window("dsh 控制台")), "面板跟着没了")
    finally:
        subprocess.run(["taskkill", "/f", "/im", "DshBar.exe"], capture_output=True)
        deadline = time.time() + 20
        while time.time() < deadline and (bar_processes() or python_pids() - before):
            time.sleep(1)
        if os.path.exists(req):
            os.remove(req)
        check("竞态测试后无残留",
              not bar_processes() and not (python_pids() - before) and not os.path.exists(req),
              f"DshBar={sorted(bar_processes())} python={sorted(python_pids() - before)} req={os.path.exists(req)}")


def check_gui() -> None:
    print("\n== 状态栏 GUI ==")
    if not os.path.isfile(BAR_EXE):
        print("  （跳过：没有编译产物，先跑 --build）")
        return
    if bar_processes():
        check("启动前无残留实例", False, "已有 DshBar 在跑，先退出它再冒烟")
        return
    before = python_pids()
    log = os.path.join(ds.state_dir(), "bar.log")
    log_offset = os.path.getsize(log) if os.path.isfile(log) else 0
    t_launch = time.time()
    proc = subprocess.Popen([BAR_EXE], cwd=os.path.dirname(BAR_EXE))
    try:
        deadline = time.time() + 25
        hwnds = []
        while time.time() < deadline:
            hwnds = find_bar_windows()
            if hwnds and proc.poll() is None:
                break
            time.sleep(0.5)
        check("进程存活", proc.poll() is None, f"退出码 {proc.returncode}")
        check("任务栏里挂上了状态栏窗口", bool(hwnds), "Shell_TrayWnd 无 DshBar 子窗口")
        if hwnds:
            u = _user32()
            h = hwnds[0]
            tb = u.FindWindowW("Shell_TrayWnd", None)
            bl, bt, br, bb = rect_of(tb)
            # 刚 Show() 出来的那一帧还没被 PlaceNow 落位，可能在任务栏下方，等它归位
            dock_ms = -1.0
            while time.time() - t_launch < 15:
                style = u.GetWindowLongW(h, -16)
                _, top, _, bottom = rect_of(h)
                if style & 0x10000000 and bt <= top and bottom <= bb + 2:
                    dock_ms = (time.time() - t_launch) * 1000
                    break
                time.sleep(0.2)
            style = u.GetWindowLongW(h, -16)
            exstyle = u.GetWindowLongW(h, -20)
            left, top, right, bottom = rect_of(h)
            check("已停靠为任务栏子窗口", bool(style & 0x40000000) and u.GetParent(h) == tb,
                  f"WS_CHILD={bool(style & 0x40000000)} parent={u.GetParent(h)}")
            check("带 WS_EX_LAYERED（像素合成的前提）", bool(exstyle & 0x80000), hex(exstyle))
            check("可见", bool(style & 0x10000000) and bool(u.IsWindowVisible(h)), hex(style))
            check("落在任务栏带内", bt <= top and bottom <= bb + 2,
                  f"窗口 {top}-{bottom}，任务栏 {bt}-{bb}")
            print(f"  从启动到落位 {('%.0fms' % dock_ms) if dock_ms >= 0 else '从未落位'}")
            w = settled_width(h)
            check("宽度足够显示正文（≥200 物理像素）", w >= 200, f"{w}px")
            check("未溢出任务栏", bl <= left and right <= br + 2, f"{left}-{right} vs {bl}-{br}")
            shot = os.path.join(ds.state_dir(), "smoke-bar.png")
            cw, ch, rgb = capture_bar(h, shot)
            check("PrintWindow 抓到状态栏画面", cw > 0 and ch > 0, f"{cw}x{ch}")
            if cw and ch:
                hues = {rgb[i:i + 3] for i in range(0, len(rgb), 3)}
                check("画面已合成出内容（非纯色）", len(hues) > 20,
                      f"只有 {len(hues)} 种颜色")
                # 「先抓帧、后取 want」是条必然撞上的竞态：状态栏的色来自 --watch 的最新帧，
                # 落后引擎一个 interval（默认 2 秒），而本机这些会话的状态是被**正在干活的别的
                # agent 会话**推着变的。Task 4 那一轮就红在这里：抓帧那一刻圆点是
                # #0D9488（tool_running，实测 137 px），隔 2 秒探针取到的是 #D97706（thinking）
                # —— 画面没错，是断言在拿 T1 的期望比 T0 的像素。
                # 所以改成「取 want → 现抓 → 比对 → 没中就等一秒再来」，给状态栏最多 20 秒
                # （约 10 个 interval）追平引擎；追不平才记 ✗。断言强度没降：圆点仍然必须真的
                # 是引擎报出的那个色，只是不再靠抓帧撞上稳定窗口的那次运气。
                want, near = "", 0
                lost = False
                deadline = time.time() + 20.0
                while True:
                    want = (parse_line(run("--json", "--no-balance").stdout.strip()) or {}).get("color", "")
                    try:
                        tgt = bytes(int(want[j:j + 2], 16) for j in (1, 3, 5))
                    except Exception:
                        tgt = b""
                    cw2, ch2, rgb2 = capture_bar(h, shot)
                    if not (cw2 and ch2):
                        # 抓不到就停手。旧写法 `if cw2 and ch2: rgb = rgb2` 在失败那一轮会**沿用上一帧**
                        # 继续比，最坏拿旧帧空转到 20 秒超时，报出来的 ✗ 说的是「没追上引擎报的色」，
                        # 而现场其实是「PrintWindow 这一轮失败了」——红因写错了比不红更糟。
                        lost = True
                        break
                    rgb = rgb2
                    near = sum(1 for i in range(0, len(rgb), 3)
                               if tgt and all(abs(rgb[i + k] - tgt[k]) <= 12 for k in range(3)))
                    if near >= 20 or time.time() >= deadline:
                        break
                    time.sleep(1.0)
                top = collections.Counter(rgb[i:i + 3] for i in range(0, len(rgb), 3))
                check(f"状态色 {want} 已画在圆点上", near >= 20,
                      (f"{near} 像素命中（capture_bar 这一轮失败，已停手不再拿旧帧比对），"
                       if lost else f"{near} 像素命中（等满 20 秒状态栏也没追上引擎报的这个色），")
                      + f"画面主色 {[c[0].hex() for c in top.most_common(4)]}")
                print(f"  画面存到 {shot}")
        check("引擎子进程已拉起", not python_pids() <= before, "没有新增 python 进程")
        try:
            with open(log, encoding="utf-8-sig", errors="replace") as fh:
                fh.seek(log_offset)
                tail = fh.read()
        except OSError:
            tail = ""
        check("日志记录引擎启动", "引擎已启动" in tail, tail[-200:] or "无新日志")
        check("日志无异常", "异常" not in tail and "失败" not in tail, tail[-200:])
    finally:
        # 优雅 taskkill 对 WS_CHILD 窗口无效（WM_CLOSE 送不到），只能强杀。
        subprocess.run(["taskkill", "/f", "/im", "DshBar.exe"], capture_output=True)
        # 引擎靠 stdout 管道断裂退出，父进程死后还要一两秒才收尾，轮询而不是定死等待。
        left_pids = python_pids() - before
        deadline = time.time() + 20
        while left_pids and time.time() < deadline:
            time.sleep(1)
            left_pids = python_pids() - before
        check("退出后无引擎孤儿子进程", not left_pids, f"仍在跑 {sorted(left_pids)}")
        check("退出后 DshBar 已消失", not bar_processes(), str(bar_processes()))


def check_build() -> None:
    print("\n== Release 编译 ==")
    if not shutil.which("dotnet"):
        check("dotnet 可用", False, "PATH 里没有 dotnet")
        return
    p = subprocess.run(
        ["dotnet", "build", "-c", "Release", "--nologo", "-t:Rebuild"],
        capture_output=True, text=True, errors="replace", timeout=600,
        cwd=os.path.join(HERE, "bar"),
    )
    warns = [l for l in p.stdout.splitlines() if ": warning " in l]
    errs = [l for l in p.stdout.splitlines() if ": error " in l]
    check("编译成功", p.returncode == 0, (p.stdout + p.stderr)[-400:])
    check("0 错误", not errs, "\n".join(errs[:3]))
    check("0 警告", not warns, "\n".join(dict.fromkeys(w.split(": warning ")[1][:120] for w in warns)))


def main() -> int:
    args = sys.argv[1:]
    full = "--all" in args
    check_unit_tests()
    check_cli()
    check_demos()
    check_watch()
    check_key_storage()
    if full or "--build" in args:
        check_build()
    check_engine_copy()
    check_licenses()
    # 纯读源码、无副作用，所以不放 --gui：默认那轮也要钉住「开窗委托体自带 try」
    # 和「请求文件在开窗之后才删」这两条（外部没法让 PanelWindow 构造必然抛，
    # 见 check_panel_request_guard 的注释）。
    check_panel_request_guard()
    panel_shots: dict[str, int] = {}
    if full or "--gui" in args:
        # 「空表那张」先用假引擎量好：它是本段所有「有没有数据行」门禁的基线
        # （check_panel_shell 的绝对阈值换成做差，见 row_data_gate），
        # check_panel_empty_sessions 也直接复用这张图、不再注入第二次。
        empty_shot = shot_with_fake_engine(
            fake_engine_source(0),
            os.path.join(ds.state_dir(), "panel-overview-empty.png"), "空表基线")
        empty_base = empty_shot[2]
        panel_shots = check_panel_shell(empty_base)
        check_panel_empty_sessions(empty_shot)
        # 行数与视口那两条也借同一份产物目录注入假引擎，
        # 所以整段都在 check_gui 之前 —— check_gui 会真的用这份拷贝起引擎。
        check_panel_row_data(empty_base)
        # records:null 的帧要在**状态栏**上验（静默冻结是这条缺陷的表现，不是红），
        # 同样只碰那份引擎拷贝，还原完才轮到 check_gui。
        check_bar_null_records()
        # 真实窗口那一侧的「有限高度」也借这份拷贝注入 29 行假引擎，
        # 所以同样排在 check_gui 之前（check_gui 要拿真引擎起状态栏）。
        check_panel_real_scroll()
        check_gui()
        check_panel_window()
        # theme/showBar 的落盘往返 + 「主题真的改像素」的取样门禁（Task 5 Step 0 #1，补 N3 缺口）。
        # 这两段都会改写 settings.json 并在 finally 里按备份还原，串行跑互不污染。
        check_settings_roundtrip()
        check_showbar_hidden()
        check_panel_cold_open()
        check_panel_request_containment()
        check_panel_request_race()
    else:
        print("\n  （GUI 冒烟未跑，加 --gui）")

    print(f"\n结果：{PASSED} 项通过" + ("" if not FAILS else f"，{len(FAILS)} 项失败"))
    if panel_shots:
        # 颜色门禁将来红了的时候，这行就是现场：哪一页掉到几色一眼可见。
        print("  各页离屏色数 " + "  ".join(f"{k}={v}" for k, v in panel_shots.items()))
    for f in FAILS:
        print(f"  ✗ {f}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
