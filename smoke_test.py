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
def capture_bar(hwnd: int, out_path: str) -> tuple[int, int, bytes]:
    """PrintWindow 抓状态栏自身像素：任务栏被全屏应用盖住时也能验，且直接证明
    分层窗口真的合成了内容（README 记过「命中看得到、画面看不到」这一类坑）。"""
    u, g = _user32(), ctypes.windll.gdi32

    class BI(ctypes.Structure):
        _fields_ = [("s", wt.DWORD), ("w", wt.LONG), ("h", wt.LONG), ("pl", wt.WORD),
                    ("bc", wt.WORD), ("comp", wt.DWORD), ("size", wt.DWORD),
                    ("x", wt.LONG), ("y", wt.LONG), ("cm", wt.DWORD), ("imp", wt.DWORD)]

    left, top, right, bottom = rect_of(hwnd)
    w, h = right - left, bottom - top
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


def check_panel_shell() -> dict[str, int]:
    print("\n== 控制台面板 ==")
    shots: dict[str, int] = {}
    if not os.path.isfile(BAR_EXE):
        print("  （跳过：没有编译产物）")
        return shots
    if bar_processes():
        check("启动前无残留实例", False, "已有 DshBar 在跑")
        return shots
    for page in PANEL_PAGES:
        shot = os.path.join(ds.state_dir(), f"panel-{page}.png")
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
        check("冷打开：请求文件已消费", not os.path.exists(req), req)
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
                deadline = time.time() + 20.0
                while True:
                    want = (parse_line(run("--json", "--no-balance").stdout.strip()) or {}).get("color", "")
                    try:
                        tgt = bytes(int(want[j:j + 2], 16) for j in (1, 3, 5))
                    except Exception:
                        tgt = b""
                    cw2, ch2, rgb2 = capture_bar(h, shot)
                    if cw2 and ch2:
                        rgb = rgb2
                    near = sum(1 for i in range(0, len(rgb), 3)
                               if tgt and all(abs(rgb[i + k] - tgt[k]) <= 12 for k in range(3)))
                    if near >= 20 or time.time() >= deadline:
                        break
                    time.sleep(1.0)
                top = collections.Counter(rgb[i:i + 3] for i in range(0, len(rgb), 3))
                check(f"状态色 {want} 已画在圆点上", near >= 20,
                      f"{near} 像素命中（等满 20 秒状态栏也没追上引擎报的这个色），"
                      f"画面主色 {[c[0].hex() for c in top.most_common(4)]}")
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
        panel_shots = check_panel_shell()
        check_gui()
        check_panel_window()
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
