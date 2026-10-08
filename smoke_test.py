#!/usr/bin/env python3
"""Vigil（原 dsh-status）冒烟测试：把 README 承诺的每条命令和状态栏契约跑成断言。

  python smoke_test.py           引擎 + CLI 契约（无副作用，可随时复跑）
  python smoke_test.py --build   额外做 Release 编译，断言 0 警告 0 错误 + 解释器解析
  python smoke_test.py --setup   编译安装向导 + 开一次 GUI 读控件 + 静默装到 %TEMP% 再卸载
  python smoke_test.py --package 校验 dist/ 分发产物（随附解释器跑真引擎 + 版本对齐）
  python smoke_test.py --gui     额外启动 Vigil.exe，在 Win32 层校验任务栏停靠

--gui 会真的往任务栏里挂一个状态栏，结束时用 taskkill /f 收掉（优雅关闭当前不可用，
见 check_gui 的注释）。--build 需要 .NET 10 SDK。
"""

from __future__ import annotations

import compression.zstd as zstd  # noqa: F401  # 提前失败：低于 3.14 直接报清晰错误
import collections
import colorsys
import contextlib
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
import winreg

if sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") != "utf8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dsh_state as ds  # noqa: E402

BAR_EXE = os.path.join(HERE, "bar", "bin", "Release", "net10.0-windows", "Vigil.exe")
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


def find_zstdless_python() -> str | None:
    """找一个**确实没有** `compression.zstd` 的解释器，用来毒化 PATH。

    本机 uv 装的 cpython-3.12 就是现成的靶子（`py -0p` 列出来的第二项）。这条门禁的意义
    全在这个靶子上：没有它，「PATH 第一个 python 抢跑」这个真 bug（旧 `ResolvePython`
    会选中 3.12，`import compression` 即失败）就没人看着。找不到靶子就记 ✗，别默默放过。
    """
    root = os.path.expandvars(r"%APPDATA%\uv\python")
    for exe in sorted(glob.glob(os.path.join(root, "*", "python.exe"))):
        r = subprocess.run([exe, "-c", "import compression.zstd"], capture_output=True, timeout=30)
        if r.returncode != 0:
            return exe
    return None


def probe_python(env: dict | None = None) -> dict[str, str]:
    """跑 `Vigil.exe --engine-probe`，把 stdout 那几行 `k=v` 收成 dict（含 `_rc`）。

    用 Popen + 超时后 kill，而不是 `subprocess.run(timeout=)`：`--engine-probe` 没被实现
    认出来之前，Vigil 会把它当普通参数、直接起状态栏并且**永不退出**，`run()` 抛
    `TimeoutExpired` 会把整轮冒烟带崩（本文件一贯的口径是不让 traceback 收场）。
    """
    p = subprocess.Popen([BAR_EXE, "--engine-probe"], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, errors="replace",
                         cwd=HERE, env=env)
    try:
        out, _ = p.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
        return {"_rc": "timeout",
                "_note": "--engine-probe 不被识别，Vigil 当成常规启动把状态栏拉起来了（60 秒没退）"}
    kv = dict(l.split("=", 1) for l in (out or "").splitlines() if "=" in l)
    kv["_rc"] = str(p.returncode)
    return kv


def check_python_resolution() -> None:
    print("\n== 解释器解析 ==")
    if not os.path.isfile(BAR_EXE):
        check("Vigil.exe 存在", False, "先跑 --build 或 build.cmd")
        return
    kv = probe_python()
    check("--engine-probe 解析成功并吐出三行",
          kv.get("_rc") == "0" and kv.get("zstd") == "ok"
          and os.path.isabs(kv.get("python", "")) and os.path.isfile(kv.get("engine", "")),
          f"实得 {kv}")
    if kv.get("python") and kv.get("python") != "none":
        q = subprocess.run([kv["python"], "-c", "import compression.zstd"],
                           capture_output=True, timeout=30)
        check("报告的解释器真能 import compression.zstd", q.returncode == 0,
              q.stderr.decode("utf-8", "replace")[:200])

    poison = find_zstdless_python()
    check("找到无 zstd 的解释器作为毒化靶子（找不到这条就失效）", poison is not None,
          f"扫 {os.path.expandvars('%APPDATA%\\uv\\python')} 没找到 3.13 及以下的解释器")
    if poison:
        pdir = os.path.dirname(poison)
        env = dict(os.environ, PATH=pdir + os.pathsep + os.environ.get("PATH", ""))
        kv2 = probe_python(env)
        got = os.path.normcase(os.path.dirname(kv2.get("python", "")))
        check("把无 zstd 的解释器插到 PATH 最前面，解析结果不被它带走",
              kv2.get("_rc") == "0" and got != os.path.normcase(pdir),
              f"毒化目录 {pdir}，实选 {kv2.get('python')}")


SETUP_EXE = os.path.join(HERE, "setup", "bin", "Release", "net48", "Vigil-Setup.exe")
BAR_OUT = os.path.join(HERE, "bar", "bin", "Release", "net10.0-windows")
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Vigil"
E2E_ROOT = os.path.join(os.environ.get("TEMP", HERE), "vigil-e2e")
E2E_PKG = os.path.join(E2E_ROOT, "pkg")             # 模拟分发目录：Vigil-Setup.exe + app\
E2E_TARGET = os.path.join(E2E_ROOT, "installed")    # 安装目标
E2E_LNK = os.path.join(os.environ.get("APPDATA", HERE),
                       "Microsoft", "Windows", "Start Menu", "Programs", "Vigil.lnk")


def reg_kv(key: str, value: str) -> str | None:
    """读 HKCU 任意键的任意值。既有的 reg_run_value() 只管 Run 键那一项，卸载项要另读。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            v, _ = winreg.QueryValueEx(k, value)
            return v
    except OSError:
        return None


def long_path(p: str) -> str:
    r"""把 8.3 短路径还原成长路径再比。

    这台机器的 `%TEMP%` 就是 `C:\Users\WILDCR~1\AppData\...` 那种短形式，而
    `WScript.Shell` 读回来的 `TargetPath` 是长形式，不还原就对不上（实测红过一次）。
    """
    if not p:
        return ""
    buf = ctypes.create_unicode_buffer(4096)
    n = ctypes.windll.kernel32.GetLongPathNameW(p, buf, 4096)
    return os.path.normcase(buf.value if n else p)


def shortcut_target(path: str) -> str | None:
    """用 WScript.Shell 把 .lnk 读回来验 TargetPath —— 只验文件在等于没验。"""
    if not os.path.isfile(path):
        return None
    ps = ("$w=(New-Object -ComObject WScript.Shell).CreateShortcut('%s'); $w.TargetPath"
          % path.replace("'", "''"))
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, errors="replace", timeout=60)
    return r.stdout.strip() or None


def setup_window_dump(*extra_args: str) -> dict[str, str] | None:
    """开一次向导 GUI，用 UIA 把界面上的文字与按钮名读回来，读完收干净。

    失败（等不到窗口 / UIA 报错）返回 None，由调用处记 ✗。
    """
    if not os.path.isfile(SETUP_EXE):
        return None
    proc = subprocess.Popen([SETUP_EXE, *extra_args], cwd=os.path.dirname(SETUP_EXE))
    try:
        deadline = time.time() + 30
        hwnd = 0
        while time.time() < deadline and not hwnd:
            hwnd = top_window("Vigil 安装")
            time.sleep(0.5)
        if not hwnd:
            return None
        res = uia(hwnd, "text")
        if not uia_ok(res):
            return None
        return {"names": "\n".join(res.get("TEXT", [])),
                "buttons": "|".join(res.get("BUTTONS", [])),
                "edits": str(len(res.get("EDITS", [])))}
    finally:
        proc.kill()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pass
        # 窗口杀完进程可能还在（WPF 的 ShutdownMode 与子进程），统一再收一次。
        subprocess.run(["taskkill", "/f", "/im", "Vigil-Setup.exe"], capture_output=True)


def check_panel_backdrop() -> None:
    """三档材质下控制台都必须开得出窗口。

    回归的来路（2026-10-05 实测定位）：`PanelWindow.ApplyMaterial()` 给 FluentWindow 赋
    `WindowBackdropType`（只会是 Acrylic 或 Mica），而 WPF-UI 4.2 要求先
    `ExtendsContentIntoTitleBar=true` 才允许非 None 的背衬，否则在**构造函数里**抛
    `InvalidOperationException`。`SafeBackdrop` 的默认值又是 "acrylic"，所以只要设置里
    没这一项、或者选了 mica/acrylic 任一档，控制台在任何机器上都开不出来。
    离屏的 `--panel-shot` 不建窗口，所以这条一直没人撞到 —— 必须走真窗口。
    """
    print("\n== 三档材质都能开窗 ==")
    if not os.path.isfile(BAR_EXE):
        check("Vigil.exe 存在", False, "先跑 --build")
        return
    if bar_processes():
        check("启动前无残留实例", False, "已有 Vigil 在跑")
        return
    f = os.path.join(ds.state_dir(), "settings.json")
    backup = open(f, encoding="utf-8").read() if os.path.isfile(f) else None
    log = os.path.join(ds.state_dir(), "bar.log")
    before = open(log, encoding="utf-8", errors="replace").read() if os.path.isfile(log) else ""
    try:
        for bd in ("acrylic", "mica", "glass"):
            write_settings(interval=2, notify=True, repeatSec=0, theme="light",
                           showBar=True, backdrop=bd)
            subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
            time.sleep(1)
            proc = subprocess.Popen([BAR_EXE, "--panel", "overview"], cwd=HERE)
            try:
                deadline = time.time() + 25
                hwnd = 0
                while time.time() < deadline and not hwnd:
                    hwnd = top_window("Vigil 控制台")
                    time.sleep(0.5)
                check(f"backdrop={bd}：控制台窗口开得出", bool(hwnd),
                      "25 秒内没等到「Vigil 控制台」（多半是 ApplyMaterial 在构造里抛了）")
            finally:
                proc.kill()
                subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
                time.sleep(1)
        tail = open(log, encoding="utf-8", errors="replace").read()[len(before):]
        check("三档材质都没往日志里写「打开面板失败」",
              "打开面板失败" not in tail, tail.strip().splitlines()[-1][:160] if "打开面板失败" in tail else "")
    finally:
        if backup is not None:
            open(f, "w", encoding="utf-8").write(backup)
        elif os.path.isfile(f):
            os.remove(f)


def check_about_page() -> None:
    """关于页要能回答"这台机器上它到底认了哪个解释器"——换电脑排障的第一个问题。"""
    print("\n== 关于页 ==")
    src = os.path.join(HERE, "bar", "Panel", "Pages", "AboutPage.cs")
    if not os.path.isfile(src):
        check("AboutPage.cs 存在（占位类还留在 Pages.cs 里）", False, src)
    else:
        text = open(src, encoding="utf-8").read()
        for label in ("安装位置", "版本", "检测引擎"):
            check(f"关于页有「{label}」这一行", label in text, "缺该文案")
        check("关于页的颜色走主题键（不写死刷子）",
              "Ui.Ref(" in text and "Brushes.Black" not in text,
              "写死颜色切到深色主题就是黑底黑字")
        check("关于页不自己拼版本数字，取 App.VersionText", "App.VersionText" in text,
              "自己拼就会和 csproj 的 <Version> 漂移")

    if not os.path.isfile(BAR_EXE):
        check("Vigil.exe 存在", False, "先跑 --build")
        return
    # 走离屏出图而不是真窗口：`--panel about` 那条冷启动通路目前被一个**既有故障**挡着
    # （PanelWindow 的 WPF-UI backdrop 抛 "Cannot apply backdrop effect if
    # ExtendsContentIntoTitleBar is false"，在 7dd8610 基线 worktree 上同样红，与安装功能无关）。
    # 判据沿用 check_pages_filled 的口径：占位页实测 7 色 / 0 不透明像素。
    if bar_processes():
        check("离屏出图前无残留实例", False, "已有 Vigil 在跑")
        return
    _fp, colors, opaque = shot_fp("about", "about")
    check("关于页已画出真实内容（离屏色数 > 20，占位页实测 7）", colors > 20,
          f"色数 {colors}、不透明 {opaque}")
    check("关于页不再是透明占位（不透明像素 > 0）", opaque > 0, f"不透明像素 {opaque}")


def notices_entries() -> list[dict]:
    """从 THIRD-PARTY-NOTICES.md 解析出许可清单条目。

    这是**判据侧**的解析器，实现侧（C#）另有一份 —— 两条独立通路量同一个事实，
    对不上就红。spec 增补 V5 §2 的原话是「清单从 THIRD-PARTY-NOTICES.md 解析出来，
    而不是在 C# 里再手抄一份（手抄必然漂移）」，所以这里刻意不读任何 .cs。

    条目 = 带 `- **用途**：` 的 `## N.` / `### N.M` 小节（2 / 4 是容器，本身没有字段行）。
    """
    path = os.path.join(HERE, "THIRD-PARTY-NOTICES.md")
    if not os.path.isfile(path):
        return []
    text = open(path, encoding="utf-8").read()
    entries: list[dict] = []
    cur: dict | None = None

    def flush():
        # 「有任一字段行」才算条目，不是「有用途行」：4.3 CPython embeddable 那一节写的
        # 是版本下限/许可证/原文随附，偏偏没有用途行 —— 按用途判就会把这条**随产物分发**
        # 的项整个漏掉。判据侧与 Credits.cs 必须同规则，但各写各的代码。
        if cur and (cur.get("purpose") or cur.get("license") or cur.get("url")):
            entries.append(cur)

    for line in text.splitlines():
        head = re.match(r"^#{2,3}\s+(\d+(?:\.\d+)?)\.?\s*(.+?)\s*$", line)
        if head:
            flush()
            title = head.group(2)
            # 「WPF-UI 4.2.0 — MIT」/「… — 微软专有字体许可（**非 MIT**）」
            name = re.split(r"\s+[—-]\s+", title)[0].strip()
            cur = {"id": head.group(1), "name": name, "license": "", "url": "", "purpose": ""}
            continue
        if cur is None:
            continue
        field = re.match(r"^-\s+\*\*(.+?)\*\*\s*[：:]\s*(.*)$", line)
        if not field:
            continue
        key, val = field.group(1), field.group(2).strip()
        if key == "用途":
            cur["purpose"] = val
        elif key == "许可证":
            if not cur["license"]:
                cur["license"] = val
        elif key == "上游":
            m = re.search(r"https?://\S+", val)
            if m:
                cur["url"] = m.group(0).rstrip("｜|,，。")
    flush()
    return entries


def panel_text(hwnd: int) -> str:
    """把面板当前那一页的 UIA 文本全捞出来拼成一串（门禁按"含不含"判）。

    走 `text1` 不是 `text`：MIT 全文是**一个带换行的 TextBlock**，`text` 那条按行原样
    Write-Output，第二行起就没有 `TEXT=` 前缀了，被这里的按行解析直接丢掉 ——
    于是"渲染了 MIT 全文"这条会假红。`text1` 把名字里的换行折成 <NL>。
    """
    res = uia(hwnd, "text1")
    if not uia_ok(res):
        return ""
    return "\n".join(res.get("TEXT", []))


def check_about_v5() -> None:
    """关于页按 spec 增补 V5 的四块：身份区 / 许可清单 / 非 MIT 声明 / 数据诊断区。

    这一段是 2026-10-07 补的：之前那页只有「安装 / 版本 / 检测引擎」三行，
    V5 要求的 Logo 身份区、作者、MIT 全文、第三方清单**一块都没有**。
    """
    print("\n== 关于页（V5 四块）==")
    src = os.path.join(HERE, "bar", "Panel", "Pages", "AboutPage.cs")
    text = open(src, encoding="utf-8").read() if os.path.isfile(src) else ""

    entries = notices_entries()
    check("THIRD-PARTY-NOTICES.md 解析出条目（判据侧解析器有东西可判）", len(entries) >= 10,
          f"只解析出 {len(entries)} 条")
    check("随产物分发的 CPython/PSF 那一条在清单里（它没有「用途」行，最容易被规则漏掉）",
          any("CPython" in e["name"] for e in entries),
          "V5 §3 要单独标出的非 MIT 项，漏一条就是合规缺口")

    # ① 清单必须是解析出来的，不是抄进代码的：任何 .cs 里都不许出现上游版权人名。
    copied = []
    for root, _ds, fs in os.walk(os.path.join(HERE, "bar")):
        if "obj" in root or "bin" in root:
            continue
        for fn in fs:
            if fn.endswith(".cs"):
                body = open(os.path.join(root, fn), encoding="utf-8", errors="replace").read()
                for name in ("Pomianowski", "Bäumlisberger", "AmorFate"):
                    if name in body:
                        copied.append(f"{fn}:{name}")
    check("许可清单没有在 C# 里手抄（源码里搜不到上游版权人名）", not copied,
          "手抄必然与 notices 漂移：" + ", ".join(copied[:4]))
    check("关于页从 THIRD-PARTY-NOTICES.md 读清单", "THIRD-PARTY-NOTICES.md" in text,
          "没引用那份文件，清单就是硬编码的")
    check("THIRD-PARTY-NOTICES.md 随输出分发（csproj 里带 CopyToOutputDirectory）",
          _csproj_ships("THIRD-PARTY-NOTICES.md"),
          "运行时读不到那份文件，关于页只会显示空清单")
    check("LICENSE 随输出分发（关于页要渲染 MIT 全文）", _csproj_ships("LICENSE"),
          "关于页拿不到 MIT 全文")

    # ② 身份区：Logo + 作者 + 仓库链接，数字仍归 csproj 单一来源。
    check("身份区放 Logo（V6 的 256 大图）", "logo.png" in text, "缺 Logo")
    check("身份区写作者，且作者取 csproj 的 <Authors> 不硬编码",
          "Authors" in text or "AuthorText" in text,
          "作者写死在页面上，改了 csproj 就漂移")
    check("身份区有仓库链接，取 csproj 的 <RepositoryUrl>",
          "RepositoryUrl" in text or "RepoUrl" in text,
          "链接写死会在改仓库名时失效")

    # ③ 非 MIT 声明 + 数据诊断区
    check("非 MIT 那几项在页面上单独标出来", "非 MIT" in text or "专有" in text,
          "Segoe 字体那种专有许可混在 MIT 列表里没人看得出来")
    check("数据与诊断区复用运行页那两颗打开按钮的通路",
          "RevealInExplorer" in text or "打开日志" in text or "OpenLog" in text,
          "V5 §4 要求复用已有通路，不再另起一套")

    if not os.path.isfile(BAR_EXE):
        check("Vigil.exe 存在", False, "先跑 --build")
        return
    with isolated_settings(theme="light", backdrop="mica", showBar=False, notify=False,
                           interval=2, repeatSec=0):
        proc = subprocess.Popen([BAR_EXE, "--panel", "about"], cwd=os.path.dirname(BAR_EXE))
        try:
            hwnd = 0
            t0 = time.time()
            while time.time() - t0 < 25 and not hwnd:
                time.sleep(0.4)
                hwnd = top_window("Vigil 控制台")
            check("关于页真窗口开得出来", bool(hwnd), "等满 25 秒没有「Vigil 控制台」窗口")
            if not hwnd:
                return
            time.sleep(3.0)  # 等清单读完 + 首帧合成
            body = panel_text(hwnd)
            check("关于页读出 UIA 文本", bool(body), "UIA 一条文本都没抓到")
            missing = [e["name"] for e in entries if e["name"].replace(" ", "") not in body.replace(" ", "")]
            check("关于页的清单逐项等于 THIRD-PARTY-NOTICES.md", not missing,
                  f"{len(missing)} 条没显示：" + "、".join(missing[:4]))
            check("关于页渲染 MIT 全文", "Permission is hereby granted" in body,
                  "V5 §2 / spec §8 要求关于页在应用内渲染同一份清单与许可")
            check("关于页显示作者 Wildcreator", "Wildcreator" in body, "身份区缺作者")
            check("专有许可那一项在页面上被单独标出来", "非 MIT · 专有许可" in body,
                  "Segoe 那一格原文写的是「微软专有字体许可（非 MIT）」，混在 MIT 列表里看不出来")
            check("仓库链接不是系统紫（V2 明文不得出现紫色）",
                  "VigilAccentBrush" in text,
                  "HyperlinkButton 默认吃系统强调色，本机那一个是紫")

            # 「页面自己放 ScrollViewer」这条外壳契约，运行页在阈值行改矮之后已经撑不出来了
            # （见 check_panel_settings_live 里那条两分支判据）。关于页天生就长：
            # 9 条许可清单 + MIT 全文，最小尺寸下必然超视口，所以契约改钉在这一页。
            u = _user32()
            wl, wt_, wr, wb = rect_of(hwnd)
            u.SetWindowPos(hwnd, 0, wl, wt_, 760, 500, 0x0004 | 0x0010)
            sc = {}
            prev_h = -1
            deadline = time.time() + 20
            while time.time() < deadline:
                sc = uia(hwnd, "scroll")
                cur_h = int((sc.get("SVH") or ["-1"])[0])
                if uia_ok(sc) and cur_h > 0 and cur_h == prev_h:
                    break
                prev_h = cur_h
                time.sleep(0.8)
            check("关于页收到最小尺寸时可滚（清单 + MIT 全文超出视口，外壳不代劳整页滚动）",
                  uia_ok(sc) and (sc.get("VSCROLLABLE") or ["False"])[0] == "True",
                  f"VerticallyScrollable={sc.get('VSCROLLABLE')} SVH={sc.get('SVH')} {sc.get('ERR')}")
            u.SetWindowPos(hwnd, 0, wl, wt_, wr - wl, wb - wt_, 0x0004 | 0x0010)
        finally:
            # 还原前必须等进程真的没了：被强杀的实例还有一瞬活着，退出时会把内存里那份
            # showBar=false 落盘，盖在还原之后（isolated_settings 的注释里记着这次）。
            subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
            wait_no_vigil()


def _csproj_ships(fname: str) -> bool:
    """Vigil.csproj 是否把仓库根的那个文件拷进输出目录。"""
    proj = os.path.join(HERE, "bar", "Vigil.csproj")
    if not os.path.isfile(proj):
        return False
    body = open(proj, encoding="utf-8").read()
    return fname in body and "CopyToOutputDirectory" in body


def wait_no_vigil(seconds: float = 20.0) -> bool:
    """等到机器上没有 Vigil 进程。被 taskkill 的实例要一会儿才真的消失。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not bar_processes():
            return True
        time.sleep(0.4)
    return not bar_processes()


@contextlib.contextmanager
def isolated_settings(**fields):
    """临时把 settings.json 换成测试要的字段，出来时**等静默**再还原。

    为什么不能像老写法那样 taskkill 完立刻写回去：被强杀的实例还有一瞬活着，
    它退出时会把**内存里那份**（也就是测试刚写进去的 showBar=false）落盘，
    盖在还原之后 —— 于是 ambient 文件被污染，下一个"不带 --panel 起常驻实例、
    期待状态栏停靠"的门禁就红成一片，而它读到的"原始值"已经是脏的。
    2026-10-07 那次 14 条红全是这么来的，红因在取样侧不在产品。
    """
    path = os.path.join(ds.state_dir(), "settings.json")
    subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
    wait_no_vigil()
    backup = open(path, encoding="utf-8").read() if os.path.isfile(path) else None
    write_settings(**fields)
    try:
        yield
    finally:
        subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
        wait_no_vigil()
        if backup is not None:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(backup)
        elif os.path.isfile(path):
            os.remove(path)


def _shot_pixels(page: str, tag: str):
    """离屏出一张页，返回 (w, h, RGB 字节)；PNG 看完就删。读不出来给 None。"""
    out = os.path.join(ds.state_dir(), f"brand-{tag}.png")
    if os.path.isfile(out):
        os.remove(out)
    p = run_shot(page, out)
    if p is None:
        return None
    got = read_png_rgba(out)
    if os.path.isfile(out):
        os.remove(out)
    if got is None:
        return None
    w, h, buf = got
    return w, h, bytes(buf)


def _near(px, r, g, b, tol=10) -> bool:
    return abs(px[0] - r) <= tol and abs(px[1] - g) <= tol and abs(px[2] - b) <= tol


def check_brand_pixels() -> None:
    """品牌视觉的**像素**门禁：量产品，不量源码字面量。

    为什么非要有这一组：V2/V4 那两条（深浅各一档强调色、整套界面不许出现紫色）
    此前只有"扫 .cs 里的 hex 字面量"这一种判据 —— 而紫色真正的来路恰恰不是字面量，
    是控件从**系统强调色**派生的画刷（本机那一个是 #680081）。源码扫得再干净，
    控件模板一漏就画在屏幕上，2026-10-07 那次 HyperlinkButton 就是这么漏的
    （改之前那张图里有 1114 个紫色像素，而所有源码门禁全绿）。
    """
    print("\n== 品牌像素（深浅两档 × 六页）==")
    if not os.path.isfile(BAR_EXE):
        check("Vigil.exe 存在", False, "先跑 --build")
        return
    if bar_processes():
        check("离屏出图前无残留实例", False, "已有 Vigil 在跑")
        return
    pages = ("overview", "notify", "appearance", "runtime", "balance", "about")
    # 强调色两档各自的落点：appearance 页上有开关（轨道就是强调色）。
    accents = {"light": (0xD8, 0x7D, 0x44), "dark": (0xEA, 0x8C, 0x70)}
    # showBar 必须是 true：外观页那颗开关关掉时轨道走 ControlFillColorDefault，
    # 屏幕上就只剩两颗单选点是强调色（实测 184 像素），阈值会假红。
    # 离屏通路不建状态栏窗口，这里开 true 不会真往任务栏上 dock 东西。
    with isolated_settings(theme="light", backdrop="mica", showBar=True,
                           notify=False, interval=2, repeatSec=0):
        for theme in ("light", "dark"):
            write_settings(theme=theme, backdrop="mica", showBar=True,
                           notify=False, interval=2, repeatSec=0)
            purple_total = 0
            where = ""
            accent_px = 0
            card_px = 0
            page_px = 0
            for page in pages:
                got = _shot_pixels(page, f"{theme}-{page}")
                if got is None:
                    check(f"{theme}/{page} 离屏出图可读", False, "读不出来")
                    continue
                w, h, buf = got
                tr, tg, tb = accents[theme]
                for i in range(0, len(buf), 4):
                    if buf[i + 3] < 8:
                        continue
                    r, g, b = buf[i], buf[i + 1], buf[i + 2]
                    hh, ss, vv = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
                    if 245 <= hh * 360 <= 300 and ss > 0.25 and vv > 0.12:
                        purple_total += 1
                        if not where:
                            where = f"{theme}/{page} 首个紫色在 ({i // 4 % w}, {i // 4 // w})"
                    if page == "appearance" and _near((r, g, b), tr, tg, tb):
                        accent_px += 1
                    if page == "notify":
                        if _near((r, g, b), 0xFB, 0xF8, 0xF3):
                            card_px += 1
                        if _near((r, g, b), 0xF2, 0xEB, 0xE0):
                            page_px += 1
            check(f"{theme}：整套界面没有紫色相像素（245°~300°，V4 明文废除紫色）",
                  purple_total == 0, f"{purple_total} 个像素，{where}")
            if theme == "dark":
                # 浅档的 #D87D44 在深色底上会"发闷"，所以 V2 给深色另提了一档
                # #EA8C70（落日暮光）。这一条钉的就是"那一档真的落地了"。
                check(f"{theme}：强调色落在 V2 深档 #EA8C70（不是浅档那一个数）",
                      accent_px > 200, f"命中 {accent_px} 像素（判据 >200）")
            else:
                check(f"{theme}：强调色落在 V2 浅档 #D87D44",
                      accent_px > 200, f"命中 {accent_px} 像素（判据 >200）")
            if theme == "light":
                # V2 表上「卡片底 浅 = 暖纸白 #FBF8F3」，页面底比它深一档暖调。
                # 这条钉的是别把两者又倒过来（曾经页面=暖纸白、卡片=纯白）。
                check("light：卡片底是暖纸白 #FBF8F3（V2 那一格）", card_px > 2000,
                      f"命中 {card_px} 像素")
                check("light：页面底比卡片深一档 #F2EBE0", page_px > 2000,
                      f"命中 {page_px} 像素")


def check_balance_detail() -> None:
    """spec §5 余额页那一行要求「余额明细」，之前只有一句「当前余额 CNY x」。

    引擎 fetch_balance 早就吐了 total / granted / topped_up / currency / source /
    fetched_at / cached，是 C# 的 Balance 模型只接了五个字段中的三个。
    """
    print("\n== 余额明细 ==")
    sc = os.path.join(HERE, "bar", "StateClient.cs")
    body = open(sc, encoding="utf-8").read() if os.path.isfile(sc) else ""
    for f in ("granted", "topped_up"):
        check(f"StateClient.Balance 解析 {f}", f'JsonPropertyName("{f}")' in body,
              "引擎给了但模型没接，页面上就永远看不见这一格")
    pg = os.path.join(HERE, "bar", "Panel", "Pages", "BalancePage.cs")
    page = open(pg, encoding="utf-8").read() if os.path.isfile(pg) else ""
    check("余额页列出赠送/充值两笔明细", "赠送" in page and "充值" in page,
          "只有一行合计，spec §5 要的明细没有")
    check("余额页标出这一帧是不是缓存", "Cached" in body or "缓存" in page,
          "5 分钟 TTL 内看到的是缓存帧，不标出来用户会以为刚查过")


def check_runtime_row() -> None:
    """运行页「阈值（只读）」那一行：说明文字与右侧长取值挤成叠排。

    右列是 Auto + MaxWidth 320 右对齐换行，724 DIP 的页面里左侧说明只剩一半宽，
    两坨文字互相咬住（离屏截图实证）。取值那一长串本来就该独占一行左对齐。
    """
    print("\n== 运行页阈值行 ==")
    pg = os.path.join(HERE, "bar", "Panel", "Pages", "RuntimePage.cs")
    body = open(pg, encoding="utf-8").read() if os.path.isfile(pg) else ""
    row = re.search(r"Ui\.(Row|StackedRow)\(\s*\"阈值[^\"]*\"", body)
    check("阈值那一行走整行堆叠的排法（不再塞 Ui.Row 右列）",
          bool(row) and row.group(1) == "StackedRow",
          f"现在是 Ui.{row.group(1) if row else '（没找到阈值行）'}")
    check("取值文本块不再右对齐限宽",
          "MaxWidth = 320" not in body and "TextAlignment.Right" not in body,
          "右列限宽 + 右对齐就是叠排的来源")
    check("Ui 里有 StackedRow 这个工厂（其他长取值行可以复用）",
          "StackedRow" in open(os.path.join(HERE, "bar", "Panel", "Ui.cs"),
                               encoding="utf-8").read(),
          "排法要收在 Ui 工厂里，别在页面里手搓 Grid")


def check_engine_parity() -> None:
    """C++ 引擎与 Python 引擎的对照门禁（--engine，也进 --all）。

    为什么必须在切换**之前**装好：四个 compare_*.py 现在只是开发期工具，一旦
    vigil-engine.exe 替下 CPython，它就是唯一实现 —— 那时没有门禁盯着，C++ 与
    设计文档哪天悄悄分叉没人知道。所以先把判据挂上，再谈切换。

    引擎没编出来时记 ✗ 而不是静默跳过：静默跳过等于把这条门禁变成"看心情生效"，
    而 build.cmd 是纯手工步骤，最容易忘。
    """
    print("\n== C++ 引擎与 Python 引擎对照 ==")
    exe = os.path.join(HERE, "engine", "vigil-engine.exe")
    if not os.path.isfile(exe):
        check("vigil-engine.exe 已编（engine/build.cmd）", False,
              "引擎没编，下面四条对照无从谈起")
        return
    for name, why in (
        ("compare_classify", "单会话判定逐字段（真实会话）"),
        ("compare_snapshot", "整帧快照逐字段（同一时刻）"),
        ("compare_watch", "常驻 --watch 节奏与解析缓存"),
        ("compare_balance", "Key 存取双向互操作"),
    ):
        script = os.path.join(HERE, "engine", name + ".py")
        try:
            r = subprocess.run([sys.executable, "-X", "utf8", script],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=900, cwd=HERE)
            ok = r.returncode == 0
            tail = ((r.stdout or "").strip().splitlines() or [""])[-1]
            if not ok:
                tail = " | ".join(l for l in (r.stdout or "").splitlines() if "✗" in l)[:300]
        except (OSError, subprocess.TimeoutExpired) as ex:
            ok, tail = False, f"{type(ex).__name__}: {ex}"
        check(f"{name}：{why}", ok, tail[:300])


def check_setup() -> None:
    print("\n== 安装向导 ==")
    if not shutil.which("dotnet"):
        check("dotnet 可用", False, "PATH 里没有 dotnet")
        return
    log = os.path.join(HERE, "setup-build.log")
    with open(log, "w", encoding="utf-8") as f:      # 别接管道：管道会吞掉退出码
        p = subprocess.run(["dotnet", "build", "setup/Vigil.Setup.csproj", "-c", "Release",
                            "--nologo", "-t:Rebuild"], stdout=f, stderr=subprocess.STDOUT,
                           timeout=900, cwd=HERE)
    text = open(log, encoding="utf-8", errors="replace").read()
    warns = [l for l in text.splitlines() if ": warning " in l]
    errs = [l for l in text.splitlines() if ": error " in l]
    check("向导编译成功", p.returncode == 0, text[-400:])
    check("向导 0 错误", not errs, "\n".join(errs[:3]))
    check("向导 0 警告", not warns, "\n".join(dict.fromkeys(w.split(": warning ")[1][:120] for w in warns)))
    check("向导产物很小（靠系统自带的 .NET Framework，不是再来一份 155MB 运行时）",
          os.path.isfile(SETUP_EXE) and os.path.getsize(SETUP_EXE) < 200_000,
          f"{os.path.getsize(SETUP_EXE) if os.path.isfile(SETUP_EXE) else '缺文件'} 字节")
    check("输出里没有 PresentationFramework.dll（证明真的靠系统运行时）",
          not os.path.isfile(os.path.join(os.path.dirname(SETUP_EXE), "PresentationFramework.dll")), "")
    check("setup/ 里没有 .xaml（XAML 标记编译不在 dotnet SDK 里，带了就编不动）",
          not glob.glob(os.path.join(HERE, "setup", "**", "*.xaml"), recursive=True), "")

    # 两种安装状态各开一次窗口。不指 /D= 的话文案就取决于本机装没装过，门禁会随机器漂；
    # 指两个定死的目录，「安装」和「修复」这两条才同时可验。
    fresh = os.path.join(E2E_ROOT, "gui-fresh")
    same = os.path.join(E2E_ROOT, "gui-same")
    shutil.rmtree(E2E_ROOT, ignore_errors=True)
    os.makedirs(os.path.join(same))
    shutil.copy(os.path.join(BAR_OUT, "Vigil.exe"), os.path.join(same, "Vigil.exe"))

    dump = setup_window_dump("/D=" + fresh)
    check("双击即用的安装窗口（标题「Vigil 安装」，UIA 读得到）", dump is not None,
          "30 秒内没等到窗口，或 UIA 读不出内容")
    if dump:
        check("界面上有安装目录、开机自动启动、快捷方式三样东西",
              all(k in dump["names"] for k in ("安装目录", "开机自动启动", "快捷方式")),
              dump["names"][:400])
        check("未安装状态主按钮写「安装」（不是「修复」）",
              "安装" in dump["buttons"] and "修复" not in dump["buttons"],
              f"BUTTONS={dump['buttons']}")
        check("目录框是一个能改的编辑框（不是只读标签）", dump["edits"] != "0", dump["edits"])

    dump2 = setup_window_dump("/D=" + same)
    check("已装同版本时主按钮写「修复」",
          dump2 is not None and "修复" in dump2["buttons"],
          "" if dump2 else "没等到窗口")
    shutil.rmtree(E2E_ROOT, ignore_errors=True)


def check_package() -> None:
    """校验 dist/ 分发产物。跑的是 `package.cmd --verify-only`，不重新 publish。

    每次冒烟都重编 161MB 的自包含载荷不现实，所以校验逻辑落在 tools/verify_package.py，
    打包脚本和这里共用同一份 —— 两处各写一份就会有一处悄悄不生效。
    """
    print("\n== 分发产物 ==")
    dist = sorted(glob.glob(os.path.join(HERE, "dist", "Vigil-*-win-x64")))
    if not dist:
        check("dist/ 有产物（先跑 package.cmd）", False, "没有 dist\\Vigil-*-win-x64")
        return
    target = dist[-1]
    log = os.path.join(HERE, "verify-package.log")
    with open(log, "w", encoding="utf-8") as f:      # 别接管道：管道会吞掉退出码
        p = subprocess.run(["cmd", "/c", "package.cmd", "--verify-only"],
                           stdout=f, stderr=subprocess.STDOUT, timeout=1800, cwd=HERE)
    out = open(log, encoding="utf-8", errors="replace").read()
    check("package.cmd --verify-only 通过", p.returncode == 0, out[-600:])
    for line in out.splitlines():
        if line.strip().startswith("OK ") or line.startswith("  ·"):
            print("   " + line.strip())

    zips = glob.glob(os.path.join(HERE, "dist", "*.zip"))
    check("有可分发的 zip", bool(zips), "跑一次完整的 package.cmd")
    if zips:
        size = os.path.getsize(zips[0])
        check("zip 体积在合理区间（自包含载荷压缩后 40–200MB）", 40_000_000 < size < 200_000_000,
              f"{size} 字节")

    # 这条才是「零前置」的直接证据：把 PATH 削到只剩 System32（本机 python、py、uv 全都看不见），
    # 产物里的 Vigil 仍然必须认随附解释器。上面 verify_package.py 那条是在本机 PATH 下跑的，
    # 挡不住"其实靠的是宿主 python"这种情况。
    app = os.path.join(target, "app")
    exe = os.path.join(app, "Vigil.exe")
    if os.path.isfile(exe):
        sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        r = subprocess.run([exe, "--engine-probe"], capture_output=True, text=True,
                           errors="replace", timeout=180, cwd=app,
                           env={"PATH": sys32, "SystemRoot": os.environ.get("SystemRoot", ""),
                                "TEMP": os.environ.get("TEMP", ""), "TMP": os.environ.get("TMP", ""),
                                "USERPROFILE": os.environ.get("USERPROFILE", ""),
                                "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
                                "APPDATA": os.environ.get("APPDATA", "")})
        kv = dict(l.split("=", 1) for l in (r.stdout or "").splitlines() if "=" in l)
        check("PATH 被削到只剩 System32（模拟没装 Python 的机器），仍认随附解释器",
              os.path.normcase(kv.get("python", ""))
              == os.path.normcase(os.path.join(app, "runtime", "python", "python.exe")),
              f"rc={r.returncode} 实得 {kv.get('python')}")


def check_installer_e2e() -> None:
    """静默装到 %TEMP% 再卸掉：装/卸两条路径的唯一真证据。

    载荷用**框架依赖**的开发产物（6.7MB，`BAR_OUT`）而不是 185MB 自包含包：这段验的是
    "安装/卸载动作对不对"，随附解释器对不对由 verify_package.py 验。两件事分开，
    别让冒烟等一次 publish。

    这段会 taskkill /f 掉正在跑的状态栏（安装器 spec §5.2 第 1 步本来就要求这么干，
    `--gui` 段也是这么干的），所以**必须保存并恢复**：原 Run 值、原本是否在跑、跑的是
    哪个 exe。恢复放在 finally，中途断言失败也要还原。
    """
    print("\n== 安装器端到端 ==")
    if not os.path.isfile(SETUP_EXE):
        check("Vigil-Setup.exe 存在", False, "先跑 --setup 或 package.cmd")
        return
    if not os.path.isfile(os.path.join(BAR_OUT, "Vigil.exe")):
        check("演练用的开发产物就位", False, f"缺 {BAR_OUT}\\Vigil.exe，先跑 build.cmd")
        return

    saved_run = reg_run_value()
    was_running = bool(bar_processes())
    shutil.rmtree(E2E_ROOT, ignore_errors=True)
    os.makedirs(os.path.join(E2E_PKG, "app"))
    shutil.copytree(BAR_OUT, os.path.join(E2E_PKG, "app"), dirs_exist_ok=True)
    setup = os.path.join(E2E_PKG, "Vigil-Setup.exe")
    shutil.copy(SETUP_EXE, setup)

    try:
        r = subprocess.run([setup, "/S", f"/D={E2E_TARGET}", "/NO-START"],
                           capture_output=True, timeout=900)
        check("静默安装退出码 0", r.returncode == 0,
              f"rc={r.returncode}，见 {os.path.join(ds.state_dir(), 'setup.log')}")
        for rel in ("Vigil.exe", "Vigil.dll", "dsh_state.py", "Vigil-Setup.exe", "Wpf.Ui.dll"):
            check(f"装完有 {rel}", os.path.isfile(os.path.join(E2E_TARGET, rel)), E2E_TARGET)
        check("Run 值格式与 App.cs 的写法逐字一致（带引号的 exe 全路径）",
              reg_run_value() == '"' + os.path.join(E2E_TARGET, "Vigil.exe") + '"',
              f"实得 {reg_run_value()!r}")
        check("卸载注册项的 UninstallString 指向安装目录里那份向导",
              os.path.join(E2E_TARGET, "Vigil-Setup.exe")
              in (reg_kv(UNINSTALL_KEY, "UninstallString") or ""),
              reg_kv(UNINSTALL_KEY, "UninstallString") or "键不存在")
        check("卸载注册项写了 DisplayVersion（「设置 → 应用」里要有版本号）",
              bool(reg_kv(UNINSTALL_KEY, "DisplayVersion")), "")
        check("开始菜单 .lnk 存在且 TargetPath 指向装好的 exe",
              os.path.isfile(E2E_LNK)
              and long_path(shortcut_target(E2E_LNK) or "")
              == long_path(os.path.join(E2E_TARGET, "Vigil.exe")),
              f"lnk={os.path.isfile(E2E_LNK)} target={shortcut_target(E2E_LNK)}")
        check("安装目录里没有 .new/.old 残留",
              not os.path.exists(E2E_TARGET + ".new") and not os.path.exists(E2E_TARGET + ".old"), "")

        # 再装一次：走的是 Swap() 里"目标已存在 → 先改名成 .old 再换回来"那条分支。
        # 不验它的话，升级路径整个是空的，而它恰恰是换电脑后最常走的一条。
        again = subprocess.run([setup, "/S", f"/D={E2E_TARGET}", "/NO-START"],
                               capture_output=True, timeout=900)
        check("重复安装（升级/修复）退出码 0", again.returncode == 0,
              f"rc={again.returncode}，见 {os.path.join(ds.state_dir(), 'setup.log')}")
        check("重复安装后 exe 仍在", os.path.isfile(os.path.join(E2E_TARGET, "Vigil.exe")), "")
        check("重复安装后没有留下 .new/.old 残留",
              not os.path.exists(E2E_TARGET + ".new") and not os.path.exists(E2E_TARGET + ".old"),
              "残留就是 Swap 的回收步骤没跑完")
        check("载荷被压平到安装目录根，没有多出一层 app\\",
              not os.path.isfile(os.path.join(E2E_TARGET, "app", "Vigil.exe")),
              "多一层说明 CopyTree 把 PayloadDir 自己当文件复制了")

        u = subprocess.run([os.path.join(E2E_TARGET, "Vigil-Setup.exe"), "/UNINSTALL", "/S"],
                           capture_output=True, timeout=300)
        check("从安装目录静默卸载退出码 0", u.returncode == 0, f"rc={u.returncode}")
        deadline = time.time() + 30
        while os.path.isdir(E2E_TARGET) and time.time() < deadline:
            time.sleep(0.5)
        check("安装目录已删除", not os.path.isdir(E2E_TARGET), E2E_TARGET)
        check("Run 值已删", reg_run_value() is None, "")
        check("卸载注册项已删", reg_kv(UNINSTALL_KEY, "DisplayName") is None, "")
        check("开始菜单 .lnk 已删", not os.path.isfile(E2E_LNK), E2E_LNK)
        check("默认保留 %LOCALAPPDATA%\\Vigil 数据目录（卸载不毁设置）",
              os.path.isdir(ds.state_dir()), ds.state_dir())
    finally:
        # winreg 的 HKEY 对象没有 .DeleteValue()（那是 win32api 的写法），删值得用模块函数；
        # 而且卸载已经把它删了，这里要能容忍"本来就没有/已经被删"。
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if saved_run is None:
                try:
                    winreg.DeleteValue(k, RUN_VALUE)
                except FileNotFoundError:
                    pass
            else:
                winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, saved_run)
        if was_running and saved_run:
            exe = saved_run.strip('"')
            if os.path.isfile(exe):
                subprocess.Popen([exe], cwd=os.path.dirname(exe))
        shutil.rmtree(E2E_ROOT, ignore_errors=True)


def check_setup_contract() -> None:
    """bar/App.cs 与 setup/Installer.cs 之间那几处必须一致的字面量。

    向导编到 net48，不能引用主程序程序集，Run 键/值名/数据目录名是**抄的第二份**。
    漂了的代价不是编译错误，是面板「开机自动启动」的勾态变假、或者卸完载 Run 值还留着
    —— 下次开机弹「找不到 Vigil.exe」。这类东西没有别的门禁会红，只能钉在这里。
    """
    print("\n== 两个 exe 的契约 ==")
    app_cs = open(os.path.join(HERE, "bar", "App.cs"), encoding="utf-8").read()
    setup_path = os.path.join(HERE, "setup", "Installer.cs")
    if not os.path.isfile(setup_path):
        check("setup/Installer.cs 存在", False, setup_path)
        return
    inst_cs = open(setup_path, encoding="utf-8").read()
    check("Run 键路径两边各出现一次（改一处就要改另一处）",
          app_cs.count(r"Software\Microsoft\Windows\CurrentVersion\Run") == 1
          and inst_cs.count(r"Software\Microsoft\Windows\CurrentVersion\Run") == 1,
          f"App.cs {app_cs.count('CurrentVersion')} 次")
    check("值名 Vigil 两边都有",
          'RunValue = "Vigil"' in app_cs and 'RunKeyValueName = "Vigil"' in inst_cs, "")
    check("Run 值写法两边都是带引号的 exe 路径",
          r'Environment.ProcessPath}\"' in app_cs and r'MainExeName)}\"' in inst_cs,
          "任一边丢了那对引号，安装目录带空格时开机路径就断")
    check("数据目录名两边都是 Vigil",
          'StateDirName = "Vigil"' in app_cs and '"Vigil"' in inst_cs, "")


def check_engine_copy() -> None:
    print("\n== 产物一致性 ==")
    if not os.path.isfile(BAR_EXE):
        check("Vigil.exe 存在", False, "先跑 --build 或 build.cmd")
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
    # 随附分发的 embeddable 解释器：PSF 许可要求保留其版权声明，声明里必须有一节
    "Python 3.14.7", "Python Software Foundation",
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
    csproj_p = os.path.join(root, "bar", "Vigil.csproj")
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
    """从离屏 PNG 数出表格的结构证据：绘制区、真画在纸上的像素、非纸色像素、
    成排的行分隔线、靠右条带里最长的竖色连续段（滚动条滑块）。

    【Task 8 的口径变更：纸色不再钉死 #FFFFFF，改成按当前主题现量】
    概览页的表底原先是 `Background = Brushes.White`（Task 5 Step 0 #1 的明文豁免），
    本任务换成 `Ui.CardKey` 主题键 —— 实测离屏那张的纸色变成 `#FEFEFEB3`
    （CardBackgroundFillColorDefaultBrush 在浅底下就是半透明 #FEFEFE，alpha 0xB3=179），
    旧的「alpha==255 且逐字节 #FFFFFF」两判据一起失去落点：painted 从 434344 掉到 200、
    hlines 从 17 掉到 0、vscroll 从 263 掉到 0，四条门禁集体变成「永远红」。
    所以这一版：
      · `COVER_A = 128` —— 「这一笔真的画在纸上」的 alpha 门槛。实测半透明纸色是 179，
        纸上的墨迹/分隔线是 188~247，而透明底上残留的抗锯齿边是 6~15，128 落在两簇中间；
        深色主题下纸色同样在 179 一带（WPF-UI 的深浅两张卡片键都是 B3 alpha）。
      · 纸色 = 所有 covered 像素的**众数**，不写死。占位页整页透明 → 一簇都没有 →
        各量直接给 0（旧的「占位页 painted=0」形状在新口径下同向成立：实测占位页
        只有 alpha 228 的几行文字，covered 占比 0.4%，远够不到 35% 那条地板）。
      · 「非白」→「非纸色」：行分隔线、滚动条滑块、墨迹都按与纸色不同来认定。
    行分隔线仍按「同一行里被同一种非纸色 covered 色占据 ≥600 像素」认定，
    600 的原意是「至少像六列的列骨架」，列数从 6 涨到 8 后它仍然是**最松**的那一档
    （表宽实测 722px，一格都不到 600 的横段不构成一条线）。
    """
    got = read_png_rgba(path)
    if got is None:
        return None
    w, h, buf = got
    stride = w * 4
    cover = 0
    ink = hlines = 0
    x0, x1, y0, y1 = w, -1, h, -1
    colors: collections.Counter = collections.Counter()
    # 第一遍：数出 covered 像素里的众色 = 当前主题的纸色。
    for i in range(0, len(buf), 4):
        if buf[i + 3] >= COVER_A:
            colors[bytes(buf[i:i + 4])] += 1
    paper = colors.most_common(1)[0][0] if colors else b"\x00\x00\x00\x00"
    paper_n = colors[paper] if colors else 0
    # 第二遍：逐行统计（covered 数、纸色数、跨度、成排的线）。
    p = 0
    paper_per_row, row_covered, line_rows = [], [], []
    white_run = 0
    # 逐字节 #FFFFFFFF 的**最长横向连续段**。这是「表底写死成白」的直接度量：
    # 一块白底是一整条几百像素的横段（写死那版实测 ~700），而深色主题下**正文文字**
    # 本身就是 #FFFFFFFF（TextFillColorPrimaryBrush 在深底上是不透明白），
    # 它的抗锯齿芯只能连出十几像素（本机实测 24）。拿像素**总数**当判据会被文字骗过
    # —— 深色主题概览页实测 6861 个纯白像素、阈值 2000，那条门禁因此从「能红」
    # 变成「换个主题就红」。
    for y in range(h):
        row = buf[p:p + stride]
        p += stride
        cnt: collections.Counter = collections.Counter()
        first = last = -1
        run = 0
        for x in range(w):
            c = row[x * 4:x * 4 + 4]
            cnt[c] += 1
            if c == b"\xff\xff\xff\xff":
                run += 1
                white_run = max(white_run, run)
            else:
                run = 0
            if c[3] >= COVER_A:
                if first < 0:
                    first = x
                last = x
        op = sum(n for c, n in cnt.items() if c[3] >= COVER_A)
        paper_per_row.append(cnt.get(paper, 0))
        row_covered.append(op > 0)
        if op:
            cover += op
            ink += op - cnt.get(paper, 0)
            x0, x1 = min(x0, first), max(x1, last)
            y0, y1 = min(y0, y), max(y1, y)
        wide = [(c, n) for c, n in cnt.items() if n >= 600 and c != paper and c[3] >= COVER_A]
        if wide:
            hlines += 1
            line_rows.append(y)
    # 滚动证据：靠右 24px 条带（x ∈ [w-24, w-3)，绕开最右那 1px 描边）里
    # 「同一个 covered 非纸色竖着连续」的最长像素数。滚动条滑块是一根几像素宽、
    # 几百像素高的竖条；行分隔线只有孤立的 1~2 行高，两者在这一项上差两个数量级。
    # 视口无限高（外层套 StackPanel）时表格根本不需要滚动条，这项实测 1~2。
    vscroll = 0
    for x in range(max(0, w - 24), max(0, w - 3)):
        run, prev = 0, None
        for y in range(h):
            i = (y * w + x) * 4
            c = buf[i:i + 4]
            solid = c[3] >= COVER_A and c != paper
            run = run + 1 if (solid and c == prev) else (1 if solid else 0)
            prev = c if solid else None
            vscroll = max(vscroll, run)
    # 逐字节 #FFFFFF 的不透明像素数：Task 5 那张白底豁免的**直接**残留度量。
    # 豁免期内（写死 Brushes.White）实测 399904；换成 Ui.CardKey 后实测 0。
    pure_white = colors.get(b"\xff\xff\xff\xff", 0)
    # 纸色带（band）：连续含纸色的行段，容得下短线 —— 96 DPI 离屏下一根行分隔线只占
    # 1~2 行，而卡片之间那 18 DIP 空隙实测 26~44 行，8 行这条容差落在中间（两侧实测）。
    # 概览页填实后画面里有三条带：状态卡、会话表、等你处理卡（实测 (41,112)/(139,521)/
    # (566,580)），**表格带 = 最长的那条**。它是「表格有没有溢出自己那一行」的落点：
    # 溢出时表格会一路画到画布下沿、把下面那条空隙带和「等你处理」卡一起盖掉。
    paper_row = [n * 5 >= w * 2 for n in paper_per_row]
    bands, yy = [], 0
    while yy < h:
        if paper_row[yy]:
            s0 = e0 = yy
            yy += 1
            while yy < h:
                if paper_row[yy]:
                    e0 = yy
                    yy += 1
                    continue
                k = yy
                while k < h and not paper_row[k]:
                    k += 1
                if k - yy > BAND_GAP or k >= h:
                    break
                yy = k
            bands.append((s0, e0))
        else:
            yy += 1
    # 表格带 = 最长那条纸色带；`below_table` = 它下沿以下还有多少行落了笔。
    # 填实后的概览页下面是「等你处理」那张卡，它的上沿离表格下沿隔着 18 DIP 空隙，
    # 所以**表格溢出自己那一行**（外层容器给它无限高）时，那条空隙带会被表格画满、
    # 卡片被顶到底边距外 —— 实测正确形状 below_table=15（就是那张卡），溢出形状 30+。
    table_band = max(bands, key=lambda bl: bl[1] - bl[0]) if bands else (-1, -1)
    below_table = sum(1 for y in range(table_band[1] + 1, h) if row_covered[y])
    # 「行分隔线」必须**只在表格带里数**。整画布数法在概览页补上整页底色之后就不成立了：
    # 卡片之间那几段空隙是一整行同一种底色，每行都过「同一非纸色 ≥600 像素」这条，
    # 空表因此实测 130 条「线」（深浅主题一样，本机实测），而阈值是 6 ——
    # 门禁从「能红」变成「永远红」，和红得没道理一样糟。
    band_hlines = sum(1 for y in line_rows if table_band[0] <= y <= table_band[1])
    return {
        "w": w, "h": h, "total": w * h, "cover": cover, "ink": ink,
        "paper": paper, "paper_n": paper_n, "pure_white": pure_white,
        "white_run": white_run, "band_hlines": band_hlines,
        "x0": x0, "x1": x1, "y0": y0, "y1": y1, "hlines": hlines,
        "vscroll": vscroll, "bands": bands,
        "table_band": table_band,
        "table_h": (table_band[1] - table_band[0] + 1) if bands else 0,
        "below_table": below_table,
    }


BAND_GAP = 8
"""`shot_table_stats` 拼纸色带时允许跨过的非纸色行数（离屏 96 DPI）。
实测两侧：行分隔线 1~2 行、卡片之间那段空隙 26~44 行（实测三张带的空隙）。"""



COVER_A = 128
"""「这一笔真的画在纸上」的 alpha 门槛（`shot_table_stats` 的口径）。
实测两侧：主题卡片键的纸色是 0xB3=179，纸上的分隔线/墨迹是 188~247；
透明底上残留的抗锯齿边只有 6~15。128 落在两簇中间，深浅主题同一把尺。"""



PRINTWINDOW_FLAGS = (2, 0)
"""PrintWindow 的两条通路：2=PW_RENDERFULLCONTENT（分层/合成内容），0=普通 DC。
带系统背衬的面板**两条画出来的像素整体不同**：同刻各抓一帧，800 行里 800 行都对不上，
而结构量（表格顶沿 259、表头墨迹带 271..288、分隔线 8 条、纸色 #FEFEFE）一模一样
（本机实测，见 probe 记录）。所以跨通路的两帧**逐行不可比**。"""

_capture_flag: dict[int, int] = {}
"""每个窗口第一次成功抓帧走了哪条通路，之后就一直先走那条 —— 让同一段里的连续抓帧可比。"""

capture_flips: collections.Counter = collections.Counter()
"""通路被迫切换过几次的计数（切了 = 那两帧逐行对不上是取样的锅，不是画面的锅）。"""


def capture_path(hwnd: int) -> int:
    """这个窗口当前钉住的 PrintWindow 通路（还没抓过给 -1）。红因写在现场里用。"""
    return _capture_flag.get(hwnd, -1)


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
    # 通路按窗口钉住：一个面板实例从头到尾走同一条，连续抓帧才逐行可比（见 PRINTWINDOW_FLAGS）。
    order = PRINTWINDOW_FLAGS
    if hwnd in _capture_flag:
        keep = _capture_flag[hwnd]
        order = (keep,) + tuple(f for f in PRINTWINDOW_FLAGS if f != keep)
    try:
        for flag in order:  # 分层窗口要 PW_RENDERFULLCONTENT，普通窗口反而要用 0
            if not u.PrintWindow(hwnd, mdc, flag):
                continue
            info = BI(s=ctypes.sizeof(BI), w=w, h=-h, pl=1, bc=32, comp=0, size=w * h * 4)
            buf = ctypes.create_string_buffer(w * h * 4)
            if not g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(info), 0):
                continue
            raw = buf.raw
            hues = len({raw[i:i + 4] for i in range(0, len(raw), 4)})
            if hues > 1:
                if _capture_flag.get(hwnd) not in (None, flag):
                    capture_flips[hwnd] += 1
                _capture_flag[hwnd] = flag
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
        if buf.value.startswith("HwndWrapper[Vigil"):
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
    out = subprocess.run(["tasklist", "/fi", "IMAGENAME eq Vigil.exe", "/fo", "csv", "/nh"],
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
    和真引擎一样按命令行分诊：只有 `--watch` 才循环（`App.LoadOneShotSnapshot` 走的是
    `--json --no-balance`，吐完一帧必须自己退出，否则每次 `--panel-shot` 都要白等
    `WaitForExit(5000)` 那 5 秒 —— 一段要出六张参照图的门禁会被它拖成半分钟）。
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
            'if "--watch" not in sys.argv:\n'
            '    sys.stdout.write(frame + "\\n")\n'
            '    sys.exit(0)\n'
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


# ---------------------------------------------------------------- 真窗口 vs 离屏参照的配色比对
#
# 「面板画的是不是**请求的那一页**」（check_panel_direct_page）需要一个不靠写死颜色的正证据：
# 拿同一台机器、同一份 settings.json、同一个假引擎，把每页各出一张离屏参照
# （`--panel-shot`，进程内 RenderTargetBitmap），再和真窗口抓帧的页面取样框比配色分布。
# 两边由同一个 `PanelPages.TryCreate(key)` 造页、由同一套主题刷落笔，所以**换刷也不会失配**
# （Task 8 把概览页白底换成主题刷时，这条跟着一起走，不像旧的「内容区纯白像素 < 5000」
# 那样从「能红」退化成「永远绿」）。
# 三条换算规则，缺一条就比不上：
#   ① alpha：离屏图是 RGBA，Fluent 刷大半是「纯色 + 低 alpha」（概览页的表体、占位页整页
#      都是 alpha<255），而 PrintWindow 抓到的是已经合成完的 RGB —— 所以参照侧要先按
#      `外壳底色` 把 alpha 合成掉，占位页那种全透明像素才会落到窗口真正显示的那块底色上。
#   ② 取样框：两边现在是**同一块页面几何** —— 离屏画布就是真窗口给页面的 724×600，
#      参照侧再照 PAGE_BOX_DIP 往里缩 4 像素裁成 714×588（见 SHOT_PAGE_DIP 为什么钉死）。
#      但 ①③ 两条差异还在，所以比的仍是「每档颜色占多少比例」这个分布，不逐像素比。
#   ③ 通道漂移：同一个纯色在两条通路上实测会差 1~2 个 level（外观页卡片
#      离屏 #FEFEFE、真窗口 #FDFDFD；文字侧 ClearType 与离屏灰阶抗锯齿更是各出各的中间色），
#      所以每通道右移 2 位（4 级一档）再统计 —— #FDFDFD/#FEFEFE/#FFFFFF 落到相邻档但
#      白色与 #FAFAFA 仍分得开（255→63、250→62），概览与外观不会因此混为一谈。

TITLEBAR_DIP = 48
"""面板标题栏（ui:TitleBar）占的高度。UIA 实测：页面区原点从 (212, 20) 变成 (212, 68)。
外壳几何只有这一个自变量 —— 下面三处全部由它推导，改标题栏就改这一行。"""

PAGE_ORIGIN_DIP = (212, 20 + TITLEBAR_DIP)
"""页面在窗口里的原点：nav 188 + Host 左边距 24 = 212；上边 = 标题栏 + Host 上边距 20。"""

PAGE_BOX_DIP = (PAGE_ORIGIN_DIP[0] + 4, PAGE_ORIGIN_DIP[1] + 6, 930, 614)
"""真窗口里**页面**的取样框（逻辑像素）：外壳是 nav 188 + Host 左边距 24 → 页面左沿 212，
右沿 960-24=936，上沿 Host 上边距 20、下沿 640-20=620（FluentWindow 的内容区从客户区
(0,0) 起算，标题栏是覆盖式的，本机实测表格顶沿 y=20.8）。往里各缩 4 像素避开 1px 描边。
窗口尺寸变了这条就不成立 —— 所以那个尺寸本身是一条前提断言，不是默认值。"""

PAGE_W_DIP = PAGE_BOX_DIP[2] - PAGE_BOX_DIP[0]
PAGE_H_DIP = PAGE_BOX_DIP[3] - PAGE_BOX_DIP[1]
"""真窗口里页面取样框的尺寸（DIP）：714 × 588。离屏参照必须按同一块几何裁过再比，见 shot_profile。"""

SHOT_PAGE_DIP = (724, 600 - TITLEBAR_DIP)
"""`--panel-shot` 的画布（App.cs RenderShot 里那两个数）＝ 真窗口里页面实际拿到的那块：
960 - nav 188 - Host 左右 24×2 = 724，640 - Host 上下 20×2 = 600。
两边必须是**同一个排版**才能比配色分布：宽度差着 176 DIP 时长描述换行数不同、
卡片高度就不同，白底占比整体漂移（Task 7 实测重合度只有 0.77，而画的确实是余额页）。
改 App.cs 那两个数必须同时改这里，否则 shot_profile 直接给 None、比样段当场红。"""

BACKDROP_STRIP_DIP = (196, 100, 206, 600)
"""侧栏右描边（x=188）与页面左沿（x=212）之间那条窗口底色带：
半透明主题刷合成回底的基准。它在取样框**外面**，不会被页面内容污染。"""


def quant_color(c: bytes) -> bytes:
    """每通道右移 2 位：4 级一档，吸收离屏/上屏之间 1~2 个 level 的合成漂移。"""
    return bytes((c[0] >> 2, c[1] >> 2, c[2] >> 2))


def color_profile(colors: list[bytes], quantize: bool = True) -> dict[bytes, float]:
    """颜色 → 占比。quantize=False 留给「同一张图内部」那种能逐字节对齐的场合。"""
    cnt: collections.Counter = collections.Counter(
        quant_color(c) if quantize else c for c in colors)
    tot = max(1, len(colors))
    return {c: n / tot for c, n in cnt.items()}


def over_backdrop(px: bytes, bg: bytes) -> bytes:
    """把离屏 RGBA 的一个像素按 alpha 合成到外壳底色上，得到它在真窗口里应当呈现的 RGB。"""
    a = px[3]
    if a == 255:
        return bytes(px[:3])
    return bytes(round((px[i] * a + bg[i] * (255 - a)) / 255) for i in range(3))


def shot_profile(path: str, bg: bytes, stride: int = 2) -> dict[bytes, float] | None:
    """离屏参照页的配色分布（已按外壳底色合成 alpha）；读不出 PNG 或几何对不上给 None。

    裁到和真窗口取样框**同一块几何**再统计：`--panel-shot` 出的是整页 724×600，
    而真窗口那一侧的框是从页面左上往里缩 4（左）/ 6（上）像素的 714×588 —— 就是
    PAGE_BOX_DIP 减掉页面原点 (212, 20)。不裁的话参照多带着一圈描边和页边空白，
    两边量的就不是同一块地方。
    """
    got = read_png_rgba(path)
    if got is None:
        return None
    w, h, buf = got
    if (w, h) != SHOT_PAGE_DIP:
        # 参照画布一改（App.cs RenderShot 里那两个数），下面这块裁切就全错，
        # 与其悄悄比错，不如让调用处当场红。
        return None
    # 页面原点：nav 188 + Host 左边距 24 = 212；Host 上边距 20。它和 PAGE_BOX_DIP 是
    # 同一个外壳的两处读数，所以这里当场校验裁切块落在画布里 —— 外壳改了而这里没跟上时，
    # 宁可红在「参照读不出来」，也不要切片悄悄短一截、拿错区域比样。
    x0, y0 = PAGE_BOX_DIP[0] - PAGE_ORIGIN_DIP[0], PAGE_BOX_DIP[1] - PAGE_ORIGIN_DIP[1]
    x1, y1 = x0 + PAGE_W_DIP, y0 + PAGE_H_DIP
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
        return None
    return color_profile([over_backdrop(buf[(y * w + x) * 4:(y * w + x) * 4 + 4], bg)
                          for y in range(y0, y1, stride) for x in range(x0, x1, stride)])


def box_profile(rgb: bytes, w: int, h: int, scale: float, stride: int = 2) -> dict[bytes, float]:
    """真窗口抓帧里页面取样框的配色分布。"""
    x0, y0, x1, y1 = box_px(w, h, scale)
    return color_profile([rgb[y * w * 3 + x * 3:y * w * 3 + x * 3 + 3]
                          for y in range(y0, y1, stride) for x in range(x0, x1, stride)])


def box_hash(rgb: bytes, w: int, h: int, scale: float) -> str:
    """取样框的逐行指纹（整块 md5）：证明「换了个请求就换了一屏内容」，零容差。"""
    x0, y0, x1, y1 = box_px(w, h, scale)
    md5 = hashlib.md5()
    for y in range(y0, y1):
        md5.update(rgb[y * w * 3 + x0 * 3:y * w * 3 + x1 * 3])
    return md5.hexdigest()


def box_px(w: int, h: int, scale: float) -> tuple[int, int, int, int]:
    """把 DIP 常量按 DPI 缩放到抓帧像素，并夹进画面里（缩放手柄/描边各留 2 像素）。"""
    x0, y0, x1, y1 = (int(v * scale) for v in PAGE_BOX_DIP)
    return max(2, x0), max(2, y0), min(x1, w - 2), min(y1, h - 2)


def strip_backdrop(rgb: bytes, w: int, h: int, scale: float) -> tuple[bytes, int]:
    """侧栏与页面之间那条底色带的众数色与它的像素数（量不到就交给调用处记 ✗）。"""
    x0, y0, x1, y1 = (int(v * scale) for v in BACKDROP_STRIP_DIP)
    x0, x1 = max(2, x0), min(x1, w - 2)
    y0, y1 = max(2, y0), min(y1, h - 2)
    cnt: collections.Counter = collections.Counter(
        rgb[y * w * 3 + x * 3:y * w * 3 + x * 3 + 3]
        for y in range(y0, y1, 2) for x in range(x0, x1))
    return cnt.most_common(1)[0] if cnt else (b"", 0)


def profile_overlap(a: dict[bytes, float], b: dict[bytes, float]) -> float:
    """两个配色分布的重合度 = Σ min(占比)。1.0 = 两屏内容同一种排布，0.0 = 完全不相干。"""
    return sum(min(a.get(c, 0.0), b.get(c, 0.0)) for c in set(a) | set(b))


def fmt_profile(pal: dict[bytes, float], top: int = 3) -> str:
    """量化档写回可读的十六进制：后面的 `+0..3` 说的是这一档覆盖 4 个 level
    （#FCFCFC+ = 252~255，白色与 #FDFDFD/#FEFEFE 都在这档里，见上面规则 ③）。"""
    return " ".join(f"#{c[0] << 2:02X}{c[1] << 2:02X}{c[2] << 2:02X}+0..3×{v:.3f}"
                    for c, v in sorted(pal.items(), key=lambda kv: -kv[1])[:top])


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

    注入实测（同一台机器、同一次运行、固定 96 DPI 离屏，Task 8 换主题刷后的**新纸色口径**）：
    空表基线 带内横线 0 / 非纸色 5883，1 行 → +1 / +3536，3 行 → +3 / +6267，
    29 行 → +8 / +14832。深浅主题两组数字相同（2026-10-06 各量过一遍）。
    横线只数**表格带以内**（`band_hlines`）：概览页补上整页底色之后，卡片之间那几段
    整行同色的底衬会被 `hlines` 数成 130 条线，空表也过不了「< 6」那条阈值。
    判据仍是最松的 1 行情形：横线至少多 1 条、非纸色至少多 600 像素（600 对 3536 留 5.9 倍）。
    旧版钉的是 hlines >= 6 / ink >= 5000：前者在本机实测等价于「至少 4 个会话」
    （hlines ≈ 2 + min(可见行数, 15)），只在零会话时才跳过，1~3 个会话的机器必假红；
    后者真正的邻居是**负样本**基线，不是正样本。做差之后会话数、DPI、列宽三个变量
    一次性抵消 —— 这条口径在换刷前后都成立，因为两边量的是同一个「与纸色不同的像素」。
    """
    return st["band_hlines"] - base["band_hlines"] >= 1 and st["ink"] - base["ink"] >= 600


def check_panel_shell(empty_base: dict | None) -> dict[str, int]:
    print("\n== 控制台面板 ==")
    shots: dict[str, int] = {}
    if not os.path.isfile(BAR_EXE):
        print("  （跳过：没有编译产物）")
        return shots
    if bar_processes():
        check("启动前无残留实例", False, "已有 Vigil 在跑")
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
              p.returncode == 0 and colors >= 0
              and (w_px, h_px) == SHOT_PAGE_DIP,
              f"退出码 {p.returncode} 输出 {head!r} err={(p.stderr or '')[-160:]!r}"
              f"（画布必须是 {SHOT_PAGE_DIP[0]}×{SHOT_PAGE_DIP[1]}：真窗口页面区就是这一块，"
              "离屏参照换了尺寸就不是同一个排版，直达页那段比样会整体漂移）")
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
        pct = st["cover"] / st["total"]
        span = st["x1"] - st["x0"] + 1
        colors = shots.get("overview", 0)
        print(f"  概览页离屏实测：颜色 {colors} 种，落笔 {st['cover']}/{st['total']}（{pct:.1%}），"
              f"纸色 #{'%02X%02X%02X%02X' % tuple(st['paper'])} 占 {st['paper_n']}，"
              f"非纸色 {st['ink']} 像素，逐字节 #FFFFFFFF {st['pure_white']} 个，"
              f"绘制区 x {st['x0']}..{st['x1']} y {st['y0']}..{st['y1']}，"
              f"≥600px 横线 {st['hlines']} 条、右侧最长竖段 {st['vscroll']}px")
        # 占位页在这三条上的实测值（新口径）：7 色 / 0.0% 落笔（整页透明）/ 横向跨度不成立。
        # 空表（Sessions 为 null 时）实测 78.5% 落笔、横向铺满页宽，所以这条不依赖数据量，
        # 见 check_panel_empty_sessions()。
        check("概览页已画出表格（不再是一行占位文字）",
              pct >= 0.35 and span >= st["w"] - 2 and colors > 20,
              f"colors={colors} cover={pct:.1%} x 跨度={span}/{st['w']}"
              f"（占位页实测 7 色、0.0% 落笔）")
        # 【Task 5 白底豁免的收口门禁】表底从写死 `Brushes.White` 换成 `Ui.CardKey` 之后，
        # 离屏图里逐字节 #FFFFFFFF 的不透明像素从实测 **399904**（占整张图 92%）掉到实测 **0**。
        # 判据方向是安全的：主题卡片键在浅底合成 #FEFEFE、深底更暗，两种主题下都不会给出
        # 成片逐字节纯白；只有「有人把 Brush 写死回白」才可能把它顶上去，所以阈值给 2000
        # （实测 0 的一侧 / 写死白 399904 的一侧），钉的是「豁免没有复发」，不是「必须是某个色值」。
        check("概览页表底已交回主题键（Task 5 白底豁免收口：不许有整片逐字节纯白）",
              st["white_run"] <= 60,
              f"逐字节 #FFFFFFFF 的最长横向连续段 {st['white_run']} 像素（阈值 60）；"
              f"豁免期内同一张图实测 ~700（白底铺满页宽，共 399904 个纯白像素、占画布 92%），"
              f"而现在纯白只剩文字墨迹（深色主题下正文本身就是 #FFFFFFFF，"
              f"本机实测总共 6861 个、最长一段 24）—— 数**总像素**会被文字骗过，"
              f"数**最长横段**骗不过：一块白底必然横着连成一片。"
              f"当前实测纸色是 #{'%02X%02X%02X%02X' % tuple(st['paper'])}（主题卡片键）")
        # 行分隔线成排 = 真的有数据行渲染出来，而不只是一块纸底 + 表头。
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
            check("概览页表格有数据行：行分隔线与纸色外像素都高出空表基线",
                  row_data_gate(st, empty_base),
                  f"本机 {len(session_files)} 个会话：横线 {empty_base['hlines']}→{st['hlines']}、"
                  f"非纸色 {empty_base['ink']}→{st['ink']}"
                  f"（判据：至少多 1 条横线、至少多 600 像素）")
        # 简报 Step 4 那条：原来是「概览页色数 > 关于页色数」，靠关于页是 7 色的透明占位页
        # 撑出区分度。2026-10-07 关于页按 spec V5 补齐四块（Logo + 9 条许可清单 + MIT 全文）
        # 之后那个参照就作废了 —— 它成了全页面最丰富的一张，这条会**永远红**。
        # 参照换成绝对下限（spec §9 给的就是"颜色种类 > 200"）：占位页实测 7 色，
        # 填了数据的概览页 400+ 色，退回占位照样红。
        check("概览页色数高出占位页一个量级（会话表已填上真实数据）",
              shots.get("overview", 0) > 200,
              f"overview={shots.get('overview')}，判据 >200（占位页实测 7）")

    # 非法页名必须非零退出。旧实现把未知 key 静默当 overview：`--panel-shot nonsense`
    # 照样回 `SHOT nonsense 724 600 <概览的色数>` 且退 0，上面 parts[1] == page 只比回声，
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
        check("启动前无残留实例", False, "已有 Vigil 在跑")
        return
    rc, head, st = empty
    # 断言的不是色数，是「没崩 + 表壳还在 + 没有数据行」：
    # hlines 在 29 行注入下实测 10（Task 8 换主题刷后的新纸色口径；换刷前是 17），
    # 空表实测 1 —— 这里必须掉下来，否则说明假引擎根本没生效。
    colors = shot_dims(head)[2]
    check("空表注入：ok:false 异常帧（无 sessions 键） 仍出图且退 0",
          rc == 0 and colors > 20 and st is not None,
          f"退出码 {rc} 输出 {head!r}")
    if st:
        check("空表注入：ok:false 异常帧（无 sessions 键） 表壳仍在但没有数据行",
              st["cover"] / st["total"] >= 0.35 and st["band_hlines"] < 6,
              f"落笔 {st['cover']}/{st['total']}（纸色 #{'%02X%02X%02X%02X' % tuple(st['paper'])}）"
              f"表格带内横线 {st['band_hlines']} 条（阈值 6：空表实测 0、29 行注入实测 8；"
              f"整画布数是 {st['hlines']} 条 —— 卡片之间那几段整行同色的底衬会被数成线，"
              f"所以这条只数表格带以内）")
    # 假引擎 ②：什么都不输出 → LoadOneShotSnapshot 直接 return → _last 仍是 null
    rc2, head2, st2 = shot_with_fake_engine('import sys\nsys.stdout.write("")\n',
                                            os.path.join(ds.state_dir(), "panel-overview-null.png"),
                                            "空表注入：空 stdout")
    colors2 = shot_dims(head2)[2]
    check("空表注入：空 stdout（快照整个是 null） 仍出图且退 0",
          rc2 == 0 and colors2 > 20 and st2 is not None, f"退出码 {rc2} 输出 {head2!r}")
    if st2:
        check("空表注入：空 stdout（快照整个是 null） 表壳仍在但没有数据行",
              st2["cover"] / st2["total"] >= 0.35 and st2["band_hlines"] < 6,
              f"落笔 {st2['cover']}/{st2['total']} 表格带内横线 {st2['band_hlines']} 条"
              f"（阈值 6：空表实测 0、29 行注入实测 8，Refresh(null) 与无 sessions 键同一条路）")


def check_panel_row_data(empty_base: dict | None) -> None:
    """概览页表格的两条硬约束，都用**注入的固定行数**验，本机 ~/.dsh 里有几个会话都无关。

    ① 「有数据行」必须是相对空表那张的量：1 / 3 / 29 行都得比基线高出一截。
       旧版钉 hlines>=6 / ink>=5000 的绝对值，等价于要求本机至少 4 个会话；
    ② 滚动视口必须**高度受限**：--panel-shot 给页面的是 724×600 的有限约束。
       Content 外面套一层竖向 StackPanel 会把这层约束丢掉（StackPanel 给子元素无限高度），
       DataGrid 于是按内容长到 ~1150px 再被 600 裁掉：画面看起来是满的、门禁完全隐形，
       但表格自己的 ScrollViewer 拿不到有限高度 → 永不滚动，第 16~60 行
       （SESSIONS_IN_SNAPSHOT 上限 60）在真实面板里只能靠整页滚动、表头也一起滚出视野。
       Task 8 填实后这一条的落点跟着换：概览页成了「状态卡 + 表格 + 待处理卡」三段，
       绘制区不再顶到画布下沿，于是改量**表格带**（最长那条纸色带）——
       等高、不短于 _grid.MinHeight、且不溢出到下面那条空隙带里（溢出的形状实测把
       「等你处理」卡盖掉：带下落笔行数从 15 涨到 78）。反证见 task-8-report.md 的变异表。
    """
    print("\n== 概览页表格：数据行与滚动视口 ==")
    if not os.path.isfile(BAR_EXE):
        print("  （跳过：没有编译产物）")
        return
    if bar_processes():
        check("启动前无残留实例", False, "已有 Vigil 在跑")
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
            print(f"  {n:2d} 行：落笔 {st['cover']}/{st['total']}"
                  f"（{st['cover'] / st['total']:.1%}）非纸色 {st['ink']}，"
                  f"表格带 y {st['table_band'][0]}..{st['table_band'][1]}（高 {st['table_h']}px）、"
                  f"带下仍落笔 {st['below_table']} 行，"
                  f"横线 {st['hlines']} 条、右侧最长竖段 {st['vscroll']}px"
                  f" [空表基线 {empty_base['hlines']} 条 / {empty_base['ink']} 非纸色]")
    # ① 数据行门禁在三种数据量下都不许假红（1 个会话的机器 / 3 个 / 满屏）
    for n in (1, 3, 29):
        st = got[n][2]
        if st is None:
            check(f"概览页 {n} 行的数据行证据高出空表基线", False, "那张图没读出来")
            continue
        check(f"概览页 {n} 行的数据行证据高出空表基线", row_data_gate(st, empty_base),
              f"横线 {empty_base['hlines']}→{st['hlines']}（+{st['hlines'] - empty_base['hlines']}）、"
              f"非纸色 {empty_base['ink']}→{st['ink']}（+{st['ink'] - empty_base['ink']}），"
              f"判据：至少多 1 条横线、至少多 600 像素")
    # 先证明确实多画了行，否则下面「两张一样高」会因为两张都是空表而假绿
    st3, st29 = got[3][2], got[29][2]
    check("注入确实画出了更多行（29 行的横线严格多于 3 行）",
          bool(st3 and st29) and st29["band_hlines"] > st3["band_hlines"],
          f"3 行 {st3 and st3['band_hlines']} 条 / 29 行 {st29 and st29['band_hlines']} 条"
          f"（都只数表格带以内）")
    # ② 视口高度受限。Task 8 填实后这一条不能再写成「绘制区钉在画布下沿」——
    #    概览页现在是「状态卡 + 表格 + 待处理卡」三段，表格带本来就不挨着底边
    #    （实测表格带 y 139..521，画布 600）。改成量**表格带本身**的两件事：
    #      · 高度不随行数变（3 行与 29 行同高，多出的行走表格自己的 ScrollViewer）；
    #      · 表格带不许溢出它那一行：溢出时它会一路画到画布下沿，把下面 18 DIP 的
    #        空隙带和「等你处理」卡盖掉 —— 实测正确形状带下只剩 15 行落笔（那张卡），
    #        溢出形状是 78 行。这条就是简报 Step 0 #1 要的「可用的滚动视口 + 高度受限」。
    if st3 and st29:
        h3, h29 = st3["table_h"], st29["table_h"]
        check("概览页表格的滚动视口高度受限：行数从 3 到 29 不把表格带撑长，也不溢出它那一行",
              h3 >= 280 and abs(h29 - h3) <= 2 and st29["table_band"][1] <= st29["h"] - 18
              and st29["below_table"] >= 1 and st3["below_table"] >= 1,
              f"表格带 3 行高 {h3}px、29 行 {h29}px（判据：等高 ±2，且都不许短于 280 = "
              f"OverviewPage 里 _grid.MinHeight）；29 行那张的带下沿 y={st29['table_band'][1]}"
              f"（画布 {st29['h']}，判据 ≤ {st29['h'] - 18}：顶到底边就是表格越出它那一行、"
              f"把「等你处理」卡盖住了）；带下仍落笔 3 行 {st3['below_table']} 行 / "
              f"29 行 {st29['below_table']} 行（判据 ≥1：实测正确形状 28 = 「等你处理」那一格"
              f"和它的标题，0 就是那张卡被表格盖掉了）")
        # 装不下就得有滚动条：这是「多出的行落在滚动里」最直白的像素证据。
        # 阈值 40 的两侧都是量出来的（Task 8 填实后重量，表高被状态卡/待处理卡挤矮了，
        # 滑块从旧口径的 302px 变成 90px）：29 行实测 90px，3 行（内容装得下）与
        # 未修版（视口无限高、压根不需要滚动条）都只有 1px 的描边。
        check("概览页 29 行时表格右侧画出滚动条（装不下的行落在滚动里）",
              st29["vscroll"] >= 40,
              f"右侧 24px 条带里最长的同色竖段 {st29['vscroll']}px（阈值 40；"
              f"填实后 29 行实测 90px）；3 行那张 {st3['vscroll']}px 作对照 —— "
              f"竖段掉到个位数就是视口没被限住、行是被裁掉而不是被滚动")
    else:
        check("概览页表格的滚动视口高度受限：行数从 3 到 29 不把表格带撑长，也不溢出它那一行",
              False, f"样本不全：3 行={st3 is not None} 29 行={st29 is not None}")


def strip_cs_comments(text: str) -> str:
    """剥掉 `//` 与 `///` 注释后的 C# 正文（只按行首/行中出现的 `//` 切一刀）。

    概览页那几条源码门禁看的是**代码**：类注释里写着「不再写 Brushes.White」是给人读的，
    不能因此把「不许再有写死白」这条判成违例。仓库现有源码门禁（check_panel_request_guard /
    check_balance_key_handling）都是直接在原文上找串，本函数只在需要提纯的那几条上用。
    本仓库的 C# 没有 `/* */` 块注释，也没有把 `//` 写进字符串字面量，够用。
    """
    out = []
    for line in text.splitlines():
        cut = line.find("//")
        out.append(line if cut < 0 else line[:cut])
    return "\n".join(out)


CS_STATE_ROW = re.compile(
    r'\{\s*"([a-z_]+)"\s*,\s*\("([^"]*)"\s*,\s*"(#[0-9A-Fa-f]{6})"\s*\)\s*,?\s*\}')


def cs_state_map(text: str) -> dict[str, tuple[str, str]]:
    """把 Ui.cs 里 StateMap 那批 `{ "code", ("标签", "#RRGGBB") }` 收成 dict。"""
    return {c: (lab, hexv) for c, lab, hexv in CS_STATE_ROW.findall(text)}


def fake_overview_engine_source(state: str = "needs_action", waiting: int = 2,
                                watch: bool = False, age: float = 3.0) -> str:
    """概览页的注入引擎：一帧**定死**的快照，状态码与待处理条数由参数给。

    为什么不复用 fake_static_engine_source：那一份是 notify/runtime/balance 三段共用的基准帧，
    改它会连带改掉它们两张图的指纹比对（那些「两张不同」的门禁全都以它为参照）。
    这一份只给概览页用，字段形状照 dsh_state.py 的真实输出：
      · `label/glyph/color` 一律从 `ds.STATES[state]` 取 —— 不自己编配色；
      · `waiting` 的每条 text 带一个**独有标记**（冒烟待处理甲/乙），
        待处理那一格有没有「每帧整串重算」就数这个标记出现几次（append 会一路叠）；
      · watch=True 时每帧把 `session.age_sec` +1 —— 那是「下一帧真的进来了」唯一的外部凭据，
        有它就不用钉死 sleep 秒数（Task 7 就是被两条钉死 sleep 的判据打回去的）。
    """
    label, glyph, hexv, _prio = ds.STATES[state]
    marks = ["冒烟待处理甲", "冒烟待处理乙", "冒烟待处理丙"][:max(0, min(waiting, 3))]
    waits = ", ".join('{"project": "甲项目", "state": "%s", "text": "%s"}' % (state, m)
                      for m in marks)
    body = (
        'rows = [{"key": f"ov-{i}", "project": f"proj-{i}", "title": f"会话 {i}",\n'
        '         "state": "idle", "turn": i, "step": i, "age_sec": 60.0 + i,\n'
        '         "last_event": "result", "last_tool": f"tool{i}", "end_reason": "success",\n'
        '         "records": i, "todo": {"total": 4, "done": i, "in_progress": 0, "pending": 0},\n'
        '         "usage_total": {"input": 100, "output": 50, "total": 150 + i}, "pending": None}\n'
        '        for i in range(3)]\n'
        'def frame(i):\n'
        f'    return json.dumps({{"ok": True, "app_running": True, "state": "{state}",\n'
        f'        "label": "{label}", "glyph": "{glyph}", "color": "{hexv}",\n'
        f'        "strip_left": "{label}", "strip_right": "¥12.34",\n'
        '        "tooltip": "概览注入", "tip_lines": ["概览注入"],\n'
        f'        "session": {{"project": "甲项目", "title": "t", "state": "{state}",\n'
        f'            "turn": 1, "step": 1, "age_sec": {age} + i,\n'
        '            "last_tool": "edit", "pending": None, "todo": None, "error": None},\n'
        '        "balance": None, "waiting": [%s], "recent": [],\n'
        '        "sessions_scanned": 3, "sessions": rows}, ensure_ascii=False)\n' % waits)
    if not watch:
        return ('import json, sys\n' + body +
                'sys.stdout.write(frame(0) + "\\n")\n')
    return ('import json, sys, time\n' + body +
            'if "--watch" not in sys.argv:\n'
            '    sys.stdout.write(frame(0) + "\\n")\n'
            '    sys.exit(0)\n'
            'try:\n'
            '    for i in range(900):\n'
            '        sys.stdout.write(frame(i) + "\\n")\n'
            '        sys.stdout.flush()\n'
            '        time.sleep(0.5)\n'
            'except (BrokenPipeError, ValueError, OSError):\n'
            '    pass\n')


def check_overview_page() -> None:
    """Task 8 概览页填实的门禁，分三面：源码 / 离屏两张定帧 / 真窗口多帧。

    这一页的**产品**面（表格骨架、数据行、滚动视口、空表容忍）在 check_panel_shell、
    check_panel_empty_sessions、check_panel_row_data 三段里已经钉住了；本段钉的是
    Task 8 新加的那三块内容与 Step 0 #3 的白底豁免收口：

      ① 状态码 → 中文标签/配色的前端映射必须逐项等于引擎的 `STATES`（源码面）——
         两侧各写一份表，抄错一个字就是界面上「tool_running」或色带和状态条不一样；
      ② 表底/描边不再有写死色，且真的挂在 `Ui.CardKey` / `Ui.LineKey` 上（源码面），
         配套的像素面是 check_panel_shell 那条「逐字节 #FFFFFFFF 归零」；
      ③ 待处理那一格每帧**整串重算**，不许 `Text +=`（源码面 + 真窗口多帧面）——
         Refresh 每几秒一帧、永远在跑，append 会把历史帧的条目叠成复读机；
      ④ 竖向约束链上不许出现无限高容器（源码面）—— 见 check_panel_row_data ② 的像素面；
      ⑤ 状态卡与待处理卡跟着快照走（离屏两面不同）—— 不是构造时写死的一行字。
    """
    print("\n== 概览页填实（状态映射 / 白底豁免收口 / 待处理不累加） ==")
    ui_path = os.path.join(HERE, "bar", "Panel", "Ui.cs")
    ov_path = os.path.join(HERE, "bar", "Panel", "Pages", "OverviewPage.cs")
    if not (os.path.isfile(ui_path) and os.path.isfile(ov_path)):
        check("概览页源码可读（Ui.cs 与 OverviewPage.cs）", False, f"{ui_path} / {ov_path}")
        return
    ui_src = open(ui_path, encoding="utf-8-sig", errors="replace").read()
    ov_src = open(ov_path, encoding="utf-8-sig", errors="replace").read()
    ov_code = strip_cs_comments(ov_src)
    ui_code = strip_cs_comments(ui_src)

    # ---- ① 前端状态映射 == 引擎 STATES ----
    front = cs_state_map(ui_code)
    want = {k: (v[0], v[2]) for k, v in ds.STATES.items()}
    check("前端状态映射的键集与行数覆盖引擎 STATES 全部状态码",
          len(front) == len(want) and set(front) == set(want),
          f"前端解析出 {len(front)} 条 / 引擎 {len(want)} 条；"
          f"多出 {sorted(set(front) - set(want))}，缺失 {sorted(set(want) - set(front))}，"
          f"解析式 CS_STATE_ROW 认的是 {{ \"code\", (\"标签\", \"#RRGGBB\") }}")
    bad = sorted(k for k in want if k in front and front[k] != want[k])
    check("前端状态映射的中文标签与配色逐项等于引擎 STATES",
          not bad and len(front) == len(want),
          "; ".join(f"{k}：前端 {front[k]} ≠ 引擎 {want[k]}" for k in bad[:4])
          if bad else f"逐项比对 {len(want)} 个状态码（标签 + 十六进制色）")
    check("LabelOf 对不认识的状态码兜「未知」而不是把英文码甩到界面上",
          'StateMap.TryGetValue' in ui_code and ': "未知"' in ui_code,
          "LabelOf 里没有 TryGetValue + 「未知」兜底那一支")

    # ---- ② 白底豁免收口：概览页不再有任何写死的纸色/描边色 ----
    hard = [pat for pat in ("Brushes.White", "Brushes.Black", "E2E5EA", "#FFFFFF",
                            "FromRgb(0xFF, 0xFF, 0xFF)") if pat.lower() in ov_code.lower()]
    check("概览页代码里不再有写死的白底/描边字面量（Task 5 Step 0 #1 的豁免已收口）",
          not hard, f"代码正文里还剩 {hard}（注释已剥掉，见 strip_cs_comments）")
    check("概览页表格底色挂 Ui.CardKey、描边挂 Ui.LineKey",
          bool(re.search(r"Ui\.Ref\(\s*_grid\s*,\s*WpfControls\.DataGrid\.BackgroundProperty\s*,"
                         r"\s*Ui\.CardKey\s*\)", ov_code))
          and bool(re.search(r"Ui\.Ref\(\s*_grid\s*,\s*WpfControls\.DataGrid\.BorderBrushProperty\s*,"
                             r"\s*Ui\.LineKey\s*\)", ov_code)),
          "没找到 Ui.Ref(_grid, DataGrid.BackgroundProperty, Ui.CardKey) / BorderBrush + Ui.LineKey")

    # ---- ③ 待处理那一格：整串赋值，不许 append ----
    ri = ov_code.find("public void Refresh(Snapshot snap)")
    refresh = ov_code[ri:ri + 1400] if ri >= 0 else ""
    check("找到概览页的 Refresh 站点", bool(refresh), "OverviewPage.cs 里没有 public void Refresh(Snapshot snap)")
    check("待处理那一格每帧整串赋值，不做 Text +=（Refresh 每几秒就跑一次）",
          bool(refresh) and "_waiting.Text +=" not in refresh
          and "_waiting.Text = WaitingText(" in refresh,
          "Refresh 里出现了 _waiting.Text +=（会把历史帧的待处理条目一路叠下去），"
          "或不是整串赋值")

    # ---- ④ 竖向约束链：表格那一行必须是有限高的 * 行，且不被无限高容器包着 ----
    ti = ov_code.find("WpfControls.Grid TableArea()")
    area = ov_code[ti:ti + 1200] if ti >= 0 else ""
    check("表格拿到的是有限高度：表体行是 * 行定义，且 _grid 直接落进那一行",
          "GridUnitType.Star" in area and "Grid.SetRow(_grid, 1)" in area,
          "TableArea 里没有 Star 行定义或没有把 _grid 放进那一行")
    check("表格外层没有竖向 StackPanel（Ui.Group / Ui.Column 都会把有限高换成无限高）",
          bool(area) and "Ui.Group(" not in area and "Ui.Column(" not in area
          and "new WpfControls.StackPanel" not in area,
          "TableArea 里出现了 Ui.Group/Ui.Column/StackPanel —— 竖向 StackPanel 给子元素"
          "无限高度，正是 Task 4 轮 2 修掉的那个缺陷（像素门禁看不见它）")

    # ---- ⑤⑥ 像素面与真窗口面（都要编译产物） ----
    if not os.path.isfile(BAR_EXE):
        print("  （跳过：没有编译产物，离屏与真窗口两面跑不了）")
        return
    if bar_processes():
        check("启动前无残留实例", False, "已有 Vigil 在跑")
        return
    eng_bin = os.path.join(os.path.dirname(BAR_EXE), "dsh_state.py")
    if not check("概览页注入点就位", os.path.isfile(eng_bin), eng_bin):
        return
    before = python_pids()

    def shot_and_stats(tag: str) -> tuple[str, int, dict | None]:
        """出一张概览页离屏图，返回 (整幅 md5, 色数, 表格结构量)；读完就删（磁盘紧）。"""
        out = os.path.join(ds.state_dir(), f"t8-{tag}.png")
        if os.path.isfile(out):
            os.remove(out)
        p = run_shot("overview", out)
        if p is None:
            check(f"概览页 {tag}：--panel-shot", False, "120 秒没返回（离屏渲染卡死）")
            return "", -1, None
        head = ((p.stdout or "").strip().splitlines() or [""])[0]
        got = read_png_rgba(out)
        st = shot_table_stats(out)
        if os.path.isfile(out):
            os.remove(out)
        if got is None:
            return "", -1, st
        return hashlib.md5(got[2]).hexdigest(), shot_dims(head)[2], st

    hwnd = 0
    try:
        # ⑤ 离屏：同一份 sessions，只换状态码与待处理条数 → 画面必须跟着变
        real = engine_swap(fake_overview_engine_source("needs_action", 2))
        if real is None:
            check("引擎拷贝可注入", False, eng_bin)
            return
        try:
            fp_act, c_act, st_act = shot_and_stats("ov-action")
        finally:
            engine_restore(real)
        check("概览页离屏出图（注入 needs_action + 2 条待处理）",
              bool(fp_act) and c_act > 20, f"色数 {c_act}，指纹 {fp_act[:8]}")
        real = engine_swap(fake_overview_engine_source("idle", 0))
        try:
            fp_idle, c_idle, _idle_st = shot_and_stats("ov-idle")
        finally:
            engine_restore(real)
        check("概览页的状态卡与待处理卡跟着快照走（needs_action×2 / idle×0 两张离屏图不同）",
              bool(fp_act) and bool(fp_idle) and fp_act != fp_idle,
              f"两张指纹 {'相同' if fp_act == fp_idle else '不同'}"
              f"（{fp_act[:8]}/{fp_idle[:8]}）：相同就是那两块卡在构造时写死、没接 Refresh")
        # 两张定帧（数据量相同）都不许塌回占位页那一档：落笔占比与横向跨度还在。
        check("注入帧出的图仍然是「画出表格」的形状（落笔占比与跨度没塌）",
              st_act is not None and st_act["cover"] / st_act["total"] >= 0.35
              and st_act["x1"] - st_act["x0"] + 1 >= st_act["w"] - 2,
              f"needs_action 那张：cover={st_act and st_act['cover']}/"
              f"{st_act and st_act['total']} 跨度={st_act and st_act['x1'] - st_act['x0'] + 1}/"
              f"{st_act and st_act['w']}")

        # ⑥ 真窗口多帧：待处理那一格不许累加
        real = engine_swap(fake_overview_engine_source("needs_action", 2, watch=True))
        try:
            subprocess.Popen([BAR_EXE, "--panel", "overview"], cwd=os.path.dirname(BAR_EXE))
            t0, hwnd = time.time(), 0
            while time.time() - t0 < 25:
                hwnd = top_window("Vigil 控制台")
                if hwnd:
                    break
                time.sleep(0.4)
            if not check("真窗口：概览页面板已打开（--panel overview）", bool(hwnd),
                         "找不到标题为 Vigil 控制台 的顶层窗口"):
                return
            res = uia(hwnd, "text1")
            if not check("真窗口：UIA 读得到概览页文字（text1 把换行折成 <NL>，一条不落）",
                         uia_ok(res), str(res.get("ERR"))[:200]):
                return
            blob = "\n".join(res.get("TEXT", []))
            ages = [a for a in (thresholds_age([t]) for t in res.get("TEXT", [])) if a is not None]
            check("真窗口：概览页的状态卡读出中文标签，界面上没有英文状态码",
                  ds.STATES["needs_action"][0] in blob and "needs_action" not in blob,
                  f"含「{ds.STATES['needs_action'][0]}」={ds.STATES['needs_action'][0] in blob}，"
                  f"「needs_action」={ 'needs_action' in blob}；界面文字开头 {blob[:120]!r}")
            # 先等「下一帧真的进来了」—— 条件是这个帧里唯一会动的数：session.age_sec。
            # 不钉固定 sleep 秒数（Task 7 的两条判据就是钉死 sleep 被打回的）：面板打开后
            # 第一帧什么时候到、StateClient 何时重连都不定，等到条件出现才算数。
            base_age = max(ages) if ages else None
            moved = None
            t1 = time.time()
            while time.time() - t1 < 20:
                r2 = uia(hwnd, "text1")
                if uia_ok(r2):
                    t2texts = r2.get("TEXT", [])
                    a2 = [a for a in (thresholds_age([t]) for t in t2texts) if a is not None]
                    if base_age is not None and any(a > base_age for a in a2):
                        moved, blob = max(a2), "\n".join(t2texts)
                        break
                time.sleep(0.5)
            if not check("真窗口：等到了第二帧（session.age_sec 涨上去了，多帧通路可用）",
                         moved is not None,
                         f"第一帧 age={base_age}，等满 20 秒没出现更大的 age —— "
                         "下面那条「不累加」就只有一帧可看，不构成反证"):
                return
            n_jia, n_yi = blob.count("冒烟待处理甲"), blob.count("冒烟待处理乙")
            check("真窗口：待处理卡跑过两帧后仍然各只有一行（Refresh 不许 += 累加）",
                  n_jia == 1 and n_yi == 1,
                  f"age 从 {base_age} 涨到 {moved}（至少两帧进来）后，"
                  f"标记「冒烟待处理甲」出现 {n_jia} 次、「乙」{n_yi} 次 —— 各自都该是 1，"
                  f">1 就是每帧又 append 了一遍，0 是没画出来")
        finally:
            subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
            engine_restore(real)
            time.sleep(2)
            left = python_pids() - before
            t2 = time.time()
            while left and time.time() - t2 < 20:
                time.sleep(1)
                left = python_pids() - before
            check("概览页真窗口场景：退出后无引擎孤儿子进程", not left, f"仍在跑 {sorted(left)}")
            check("概览页真窗口场景：退出后 Vigil 已消失", not bar_processes(),
                  str(bar_processes()))
    except Exception as ex:
        check("概览页门禁整段不抛", False, f"{type(ex).__name__}: {ex}")


# ---------------------------------------------------------------- Task 6：通知页 / 运行页
#
# 这两页的可观察面分两类，各自挑了能真的红的判据：
#   ① 离屏（--panel-shot）—— 页面画没画出真控件、设置值有没有被**画回控件上**；
#   ② 真窗口 + UIA —— 改设置到底生没生效：不弹通知、重复提醒、重启引擎、写注册表、开目录。
# ②为什么走 UIA 而不是鼠标：本探测环境有前台锁（`SetForegroundWindow` 与
# `AttachThreadInput` 都返回 0，见 task-5-report.md §10.1），真点击要抢前台；
# UIA 的 Toggle/Invoke/Value 模式不经前台、不打全局输入，是这里唯一能把「面板里改一下」
# 这条路真走通的通路（Task 5 §12 记的「ToggleSwitch 点击通路未验」也由它补上）。
# 通路本身是 Windows 自带的 UIAutomationClient + powershell.exe，不引入任何第三方包。

UIA_DRIVER_PS = r'''
param([Int64]$Hwnd, [string]$Action = 'count', [string]$Name = '', [int]$Index = 0, [string]$Value = '')
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
function Pat($e, $t) { $o = $null; if ($e.TryGetCurrentPattern($t, [ref]$o)) { return $o } return $null }
$PTog = [System.Windows.Automation.TogglePattern]::Pattern
$PInv = [System.Windows.Automation.InvokePattern]::Pattern
$PVal = [System.Windows.Automation.ValuePattern]::Pattern
$PScr = [System.Windows.Automation.ScrollPattern]::Pattern
$PSel = [System.Windows.Automation.SelectionItemPattern]::Pattern
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Hwnd)
if ($null -eq $root) { Write-Output 'ERR=NOROOT'; exit 3 }
$rx = $root.Current.BoundingRectangle.X
$all = @($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                       [System.Windows.Automation.Condition]::TrueCondition))
Write-Output ("COUNT=" + $all.Count)
$toggles = @($all | Where-Object { $null -ne (Pat $_ $PTog) })
$edits   = @($all | Where-Object { $_.Current.ClassName -eq 'TextBox' })
$spins   = @($all | Where-Object { $_.Current.ClassName -eq 'RepeatButton' })
# 页面自带的那个 ScrollViewer 在内容列里；侧栏 ListBox 模板里若也藏一个，不能算在页面头上。
$scrolls = @($all | Where-Object { $_.Current.ClassName -eq 'ScrollViewer' -and
                                   ($_.Current.BoundingRectangle.X - $rx) -gt 200 })
# 按钮只算**页面区**里的：标题栏那颗「收到托盘」也是 Button，而「运行页控件齐」这条
# 断言的是页面上的动作按钮集合，混进外壳控件就是假红。
# 按 y 过滤（页面原点在标题栏之下），按 x 挡不住 —— 那颗按钮在标题栏右侧，x 比页面按钮还大。
$ry0 = $root.Current.BoundingRectangle.Y
$buttons = @($all | Where-Object { $_.Current.ClassName -eq 'Button' -and $_.Current.Name -ne '' -and
                                   $null -ne (Pat $_ $PInv) -and
                                   (($_.Current.BoundingRectangle.Y - $ry0) / ($root.Current.BoundingRectangle.Width / 960.0)) -ge 68 })
# 数字框自己的加减档：中心落在那个编辑框矩形里的那两颗。
# 整页滚动条也是 RepeatButton（Name 是 CaretUp24/CaretDown24 加一颗翻页键），
# 窗口收窄时它就贴着数字框右沿，只按「在不在数字框左边」分不开 —— 按中心点算才稳。
function BoxSpins($box) {
  if ($null -eq $box) { return @() }
  $b = $box.Current.BoundingRectangle
  return @($spins | Where-Object {
      $r = $_.Current.BoundingRectangle
      $cx = $r.X + $r.Width / 2; $cy = $r.Y + $r.Height / 2
      $cx -ge $b.X -and $cx -le ($b.X + $b.Width) -and $cy -ge $b.Y -and $cy -le ($b.Y + $b.Height) })
}
Write-Output ("TOGGLES=" + $toggles.Count)
Write-Output ("EDITS=" + $edits.Count)
Write-Output ("SPINS=" + $spins.Count)
Write-Output ("BOXSPINS=" + (BoxSpins $edits[0]).Count)
Write-Output ("SCROLLERS=" + $scrolls.Count)
$names = @($buttons | ForEach-Object { $_.Current.Name })
Write-Output ("BUTTONS=" + ($names -join '|'))
switch ($Action) {
  'count' { }
  'text' {
    foreach ($e in $all) {
      $n = $e.Current.Name
      if ($n -ne '' -and ($Name -eq '' -or $n.Contains($Name))) { Write-Output ("TEXT=" + $n) }
    }
  }
  'text1' {
    # 同 text，但把名字里的换行折成 <NL>。概览页「等你处理」那一格是**一个 TextBlock、
    # 每个待处理项一行**（Text 里带 \\n），走 text 那条会被下面的按行解析拆成好几条，
    # 「同一条标记出现了几次」（+= 累加的反证）就数不出来了。
    foreach ($e in $all) {
      $n = $e.Current.Name
      if ($n -ne '' -and ($Name -eq '' -or $n.Contains($Name))) {
        Write-Output ("TEXT=" + ($n -replace "`r?`n", '<NL>'))
      }
    }
  }
  'togstate' {
    if ($Index -ge $toggles.Count) { Write-Output 'ERR=NOTOGGLE'; exit 4 }
    Write-Output ("STATE=" + (Pat $toggles[$Index] $PTog).Current.ToggleState)
  }
  'toggle' {
    if ($Index -ge $toggles.Count) { Write-Output 'ERR=NOTOGGLE'; exit 4 }
    $t = Pat $toggles[$Index] $PTog
    Write-Output ("BEFORE=" + $t.Current.ToggleState)
    $t.Toggle()
    Start-Sleep -Milliseconds 600
    Write-Output ("AFTER=" + $t.Current.ToggleState)
  }
  'step' {
    # -Index = 第几个数字框（按文档序），-Value = up（默认）/ down
    if ($Index -ge $edits.Count) { Write-Output 'ERR=NOEDIT'; exit 4 }
    $near = BoxSpins $edits[$Index]
    $pick = 0
    if ($Value -eq 'down') { $pick = 1 }
    if ($near.Count -le $pick) { Write-Output 'ERR=NOSPIN'; exit 5 }
    (Pat $near[$pick] $PInv).Invoke()
    Write-Output ("STEPPED=" + $near.Count)
    Start-Sleep -Milliseconds 600
  }
  'invoke' {
    $hit = @($buttons | Where-Object { $_.Current.Name -eq $Name })
    if ($hit.Count -eq 0) { Write-Output 'ERR=NOBUTTON'; exit 4 }
    (Pat $hit[0] $PInv).Invoke()
    Write-Output ("INVOKED=" + $Name)
    Start-Sleep -Milliseconds 800
  }
  'value' {
    if ($Index -ge $edits.Count) { Write-Output 'ERR=NOEDIT'; exit 4 }
    Write-Output ("VALUE=" + (Pat $edits[$Index] $PVal).Current.Value)
  }
  'item' {
    # -Name = 项名子串。只读地看「这个窗口（资源管理器）里那个项有没有被选中」，
    # 供现场用；列表项是虚拟化的，读不到不算产品坏，所以调用处**不拿它当判据**。
    $lis = @($all | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::ListItem })
    $hit = @($lis | Where-Object { $_.Current.Name -like ('*' + $Name + '*') })
    if ($hit.Count -eq 0) { Write-Output ('ITEMMISS=' + $lis.Count); exit 0 }
    $o = Pat $hit[0] $PSel
    if ($null -eq $o) { Write-Output ('ITEMNOPAT=' + $hit[0].Current.Name); exit 0 }
    Write-Output ('ITEM=' + $hit[0].Current.Name)
    Write-Output ('SELECTED=' + $o.Current.IsSelected)
  }
  'rect' {
    # -Name = 元素名子串，取第一个命中。用来判断"这一格到底在不在视口里"：
    # 页面放不下又滚不动（内容被裁）时，最下面那颗按钮的矩形会掉到视口外。
    $hit = @($all | Where-Object { $_.Current.Name -like ('*' + $Name + '*') })
    if ($hit.Count -eq 0) { Write-Output 'RECTMISS=0'; exit 0 }
    $r = $hit[0].Current.BoundingRectangle
    Write-Output ('RECT=' + [int]$r.X + '|' + [int]$r.Y + '|' + [int]$r.Width + '|' + [int]$r.Height)
  }
  'scroll' {
    if ($scrolls.Count -eq 0) { Write-Output 'ERR=NOSCROLL'; exit 4 }
    $s = Pat $scrolls[0] $PScr
    if ($null -eq $s) { Write-Output 'ERR=NOSCROLLPATTERN'; exit 5 }
    Write-Output ("VSCROLLABLE=" + $s.Current.VerticallyScrollable)
    Write-Output ("EXTENT=" + [int]$s.Current.ExtentHeight)
    Write-Output ("VIEWPORT=" + [int]$s.Current.ViewportHeight)
    Write-Output ("PCT=" + [int]$s.Current.VerticalPercent)
    # 被测那个 ScrollViewer 自己的高度：重排没落定时它会一直变，等它稳定下来比睡固定秒数可靠
    # （EXTENT/VIEWPORT 在本机恒为 0，用不了，所以单独报几何）。
    Write-Output ("SVH=" + [int]$scrolls[0].Current.BoundingRectangle.Height)
    if ($Value -eq 'down') {
      $s.SetScrollPercent(-1, 100)
      Start-Sleep -Milliseconds 800
      Write-Output ("PCT2=" + [int]$s.Current.VerticalPercent)
    }
    if ($Value -eq 'up') {
      $s.SetScrollPercent(-1, 0)
      Start-Sleep -Milliseconds 800
      Write-Output ("PCT0=" + [int]$s.Current.VerticalPercent)
    }
  }
  'wide' {
    # 页面上**长说明文字**最窄有多宽（DIP）。Ui.Row 的左侧是 Star 列：右侧字段吃掉多少，
    # 说明文字就只剩多少，而这件事任何像素门禁都看不见 —— 离屏参照与真窗口是同一块
    # 挤压后的排版，比样照样 0.95+（Task 7 复核 I4）。所以直接量控件的包围盒。
    # 只算 Name 长度 >= 24 的 TextBlock：标题和按钮文字本来就短，不参与。
    # 外壳宽度是这条换算的分母，它自己由「面板窗口是默认的 960×640」那条前提断言钉着。
    #
    # **只看内容列**（左边距 > 188 DIP，即侧栏之外）：这条量的是 Ui.Row 的 Star 列被
    # 右侧字段挤没挤，而侧栏那行 VersionLine 的窄是它自己的设计（188 宽的铁轨里
    # 减掉边距只剩 ~164 DIP，还带 TextWrapping）。把它算进来，量到的就不是"被挤坏"
    # 而是"侧栏本来就窄"。改名那次（c5ea33e）给 VersionText 加了种加词代号，
    # 长度从 <24 涨到 34，于是这条一直在替侧栏报警 —— 面板当时又开不出来，没人看见。
    # 换算成 DIP 再比，别学 scrolls 那个 -gt 200 的裸像素（DPI 一缩放就偏）。
    $scW = $root.Current.BoundingRectangle.Width / 960.0
    $min = -1; $who = ''
    foreach ($e in $all) {
      if ($e.Current.ClassName -ne 'TextBlock') { continue }
      $n = $e.Current.Name
      if ($n.Length -lt 24) { continue }
      $x = $e.Current.BoundingRectangle.X
      if ((($x - $rx) / $scW) -lt 190) { continue }
      # 只看**从行首起点铺开**的文本：这条量的是 Ui.Row 左侧 Star 列被右侧字段挤没挤，
      # 而右列（Auto）里那些长 URL / 许可证值本来就窄 —— 关于页的许可清单每条右列是
      # 「MIT + 上游地址」，Auto 宽实测 171 DIP，不排掉就会把"值列本来就窄"当成"说明被挤坏"。
      if ((($x - $rx) / $scW) -gt 320) { continue }
      $wd = [int](($e.Current.BoundingRectangle.Width) / $scW)
      if ($min -lt 0 -or $wd -lt $min) { $min = $wd; $who = $n.Substring(0, 20) }
    }
    Write-Output ("MINWIDE=" + $min)
    Write-Output ("MINWIDEWHO=" + $who)
  }
  default { Write-Output 'ERR=BADACTION'; exit 6 }
}
'''


def uia_script() -> str | None:
    """把 UIA 驱动脚本落到 %TEMP%（不进仓库）。

    必须是 UTF-8 **带 BOM**：Windows PowerShell 5.1 把无 BOM 的 .ps1 按 ANSI 读，
    脚本里的中文匹配串（按钮名）会先被读坏 —— 本机实测过。
    """
    d = os.environ.get("TEMP") or ds.state_dir()
    p = os.path.join(d, "dsh-panel-uia-driver.ps1")
    try:
        with open(p, "w", encoding="utf-8-sig", newline="\r\n") as fh:
            fh.write(UIA_DRIVER_PS.lstrip("\n"))
        return p
    except OSError:
        return None


def uia(hwnd: int, action: str, name: str = "", index: int = 0,
        value: str = "") -> dict[str, list[str]]:
    """跑一次 UIA 驱动，把 `KEY=值` 收成 dict（同名可多条）。

    powershell 起不来 / 超时 / 没输出都归成 {'ERR': [...]}：调用处记 ✗，
    不让一次 traceback 把整轮冒烟带崩（与 run_shot 同一口径）。
    """
    ps = uia_script()
    if ps is None:
        return {"ERR": ["UIA 驱动脚本写不出来（%TEMP% 不可写？）"]}
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps,
             "-Hwnd", str(hwnd), "-Action", action, "-Name", name, "-Index", str(index),
             "-Value", value],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as ex:
        return {"ERR": [f"{type(ex).__name__}: {ex}"]}
    out: dict[str, list[str]] = {}
    for line in (r.stdout or "").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out.setdefault(k.strip(), []).append(v)
    if not out:
        out["ERR"] = [f"powershell 退出码 {r.returncode}：{(r.stderr or '')[-200:]}"]
    return out


def uia_ok(res: dict[str, list[str]]) -> bool:
    return "ERR" not in res


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Vigil"


def reg_run_value() -> str | None:
    """HKCU\\...\\Run 里 Vigil 的当前值；没有这一项（或读不到）都给 None。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            v, _ = winreg.QueryValueEx(k, RUN_VALUE)
        return str(v)
    except OSError:
        return None


def reg_run_write(val: str | None) -> None:
    """把 Run 项还原成给定值（None = 删掉）。测试收尾用它归还用户原状。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if val is None:
                winreg.DeleteValue(k, RUN_VALUE)
            else:
                winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, val)
    except OSError:
        pass


def list_top_windows() -> list[tuple[int, str, str]]:
    """可见的顶层窗口 (hwnd, 类名, 标题)。「打开目录/日志」两个按钮用前后差集归因。"""
    u = _user32()
    hits: list[tuple[int, str, str]] = []
    proto = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _l):
        if not u.IsWindowVisible(h):
            return True
        n = u.GetWindowTextLengthW(h)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, buf, n + 1)
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, cls, 256)
        hits.append((h, cls.value, buf.value))
        return True

    u.EnumWindows(proto(cb), 0)
    return hits


def write_settings(**fields) -> None:
    """按 Settings 的五字段契约写一份完整的 settings.json（备份还原归调用处）。"""
    base = {"interval": 2, "notify": True, "repeatSec": 90, "theme": "light", "showBar": True}
    base.update(fields)
    with open(os.path.join(ds.state_dir(), "settings.json"), "w", encoding="utf-8") as fh:
        json.dump(base, fh)


def shot_fp(page: str, tag: str) -> tuple[str, int, int]:
    """出一张离屏图，返回（画面指纹, 色数, 不透明像素数）；PNG 看完就删，不留档。

    指纹取的是解码后的 RGBA 字节（不是 PNG 文件字节）：两张图只要有一个像素不同就算不同。
    """
    out = os.path.join(ds.state_dir(), f"t6-{tag}.png")
    if os.path.isfile(out):
        os.remove(out)
    p = run_shot(page, out)
    if p is None:
        check(f"{tag}：--panel-shot {page}", False, "120 秒没返回（离屏渲染卡死）")
        return "", -1, -1
    head = ((p.stdout or "").strip().splitlines() or [""])[0]
    got = read_png_rgba(out)
    if os.path.isfile(out):
        os.remove(out)
    if got is None:
        check(f"{tag}：离屏 PNG 可读", False, f"{out} 解不出来，首行 {head!r}")
        return "", -1, -1
    w, h, buf = got
    opaque = sum(1 for i in range(3, len(buf), 4) if buf[i] == 255)
    return hashlib.md5(buf).hexdigest(), shot_dims(head)[2], opaque


def fake_static_engine_source(ok: bool = True, with_key: bool = True) -> str:
    """一帧**定死**的快照（age_sec / label 都不随时间变），给「两张图只差一个设置项」的比对用。

    为什么不用真引擎：运行页的阈值那一行跟着快照走（静默秒数每帧都在涨），
    拿真引擎连出两张图必然不同 —— 那条门禁就退化成「什么都不断言」的摆设。
    ok=False 出的是 `{"ok": false}`：反序列化后 Snapshot.Ok 为 false，
    页面走「还没有可用快照」那一支（与面板在第一帧之前打开是同一条路）。
    with_key 只多一个 `balance.key` 字段（C# 的 Balance 模型里没有这一格）：
    带与不带两张出图必须逐字节相同，那是「界面对 Key 本体是聋的」那条门禁的两张图。
    """
    if not ok:
        return 'import sys\nsys.stdout.write(\'{"ok": false, "error": "smoke-static"}\\n\')\n'
    decoy = ', "key": "SMOKE-TEST-NOT-A-REAL-KEY-0000"' if with_key else ''
    return (
        'import json, sys\n'
        'frame = json.dumps({"ok": True, "app_running": True, "state": "idle",\n'
        '    "label": "定格冒烟", "glyph": "·", "color": "#6B7280", "strip_left": "定格冒烟",\n'
        '    "strip_right": "--", "tooltip": "定格冒烟", "tip_lines": ["定格冒烟"],\n'
        '    "session": {"project": "定格项目", "title": "t", "state": "idle", "turn": 7,\n'
        '        "step": 1, "age_sec": 42.0, "last_tool": "edit", "pending": None,\n'
        '        "todo": None, "error": None},\n'
        # 余额那一格给的是**真形状**的帧（Task 7 的余额页要钉「那一行跟着快照走」）。
        '    "balance": {"available": True, "total": "12.34", "currency": "¥",\n'
        f'        "error": None, "source": "dpapi"{decoy}}},\n'
        '    "waiting": [], "recent": [], "sessions_scanned": 3,\n'
        '    "sessions": [\n'
        '        {"key": "s%d" % i, "project": "proj-%d" % i, "title": "会话 %d" % i,\n'
        '         "state": "idle", "turn": i, "step": i, "age_sec": 60.0 + i,\n'
        '         "last_event": "result", "last_tool": "tool%d" % i, "end_reason": "success",\n'
        '         "records": i, "todo": None, "usage_total": None, "pending": None}\n'
        '        for i in range(3)]}, ensure_ascii=False)\n'
        'sys.stdout.write(frame + "\\n")\n')


def fake_action_engine_source(state: str = "needs_action", every: float = 0.5) -> str:
    """持续吐**同一个状态**的帧，但 age_sec 每帧 +1 —— 那个涨法就是「帧还在进来」的证据。

    通知/重复提醒的门禁全都需要一个「不然就是引擎压根没出帧」的反证，
    而 age_sec 会原样出现在运行页阈值那一行的文字里（`静默 N 秒`），
    所以读一次 UIA 文本就知道帧有没有在流，不用去猜、也不用抢前台。
    --json 出一帧即退（`App.LoadOneShotSnapshot` 会 WaitForExit），--watch 才循环。
    """
    return (
        'import json, sys, time\n'
        'def frame(i):\n'
        '    return json.dumps({\n'
        '        "ok": True, "app_running": True, "state": "%s",\n'
        '        "label": "等你操作", "glyph": "?", "color": "#D97706",\n'
        '        "strip_left": "等你操作", "strip_right": "--",\n'
        '        "tooltip": "冒烟：等你操作", "tip_lines": ["冒烟：等你操作"],\n'
        '        "session": {"project": "冒烟项目", "title": "t", "state": "%s",\n'
        '            "turn": 1, "step": 1, "age_sec": 3.0 + i, "last_tool": "ask_user_question",\n'
        '            "pending": {"kind": "question", "tool": "ask_user_question",\n'
        '                "text": "要不要先只改固件那套？", "options": ["只改固件"]},\n'
        '            "todo": None, "error": None},\n'
        '        "balance": None, "waiting": [], "recent": [], "sessions_scanned": 1,\n'
        '        "sessions": []}, ensure_ascii=False)\n'
        'if "--watch" not in sys.argv:\n'
        '    sys.stdout.write(frame(0) + "\\n")\n'
        '    sys.exit(0)\n'
        'try:\n'
        '    for i in range(1200):\n'
        '        sys.stdout.write(frame(i) + "\\n")\n'
        '        sys.stdout.flush()\n'
        '        time.sleep(%s)\n'
        'except (BrokenPipeError, ValueError, OSError):\n'
        '    pass\n') % (state, state, every)


def engine_swap(source: str) -> bytes | None:
    """把编译产物里的引擎拷贝换成 source，返回原字节（没换成功返回 None）。

    与 shot_with_fake_engine 同一注入点（bin 下那份 dsh_state.py），
    但那段是一次性出图、这一段要**跨多次出图**保持注入状态，所以分开写，
    生产代码里因此仍然没有任何测试钩子。还原必须放在 finally。
    """
    eng = os.path.join(os.path.dirname(BAR_EXE), "dsh_state.py")
    if not (os.path.isfile(BAR_EXE) and os.path.isfile(eng)):
        return None
    with open(os.path.join(HERE, "dsh_state.py"), "rb") as fh:
        real = fh.read()
    with open(eng, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(source)
    return real


def engine_restore(real: bytes) -> bool:
    """按仓库根字节还原产物里的引擎拷贝。"""
    eng = os.path.join(os.path.dirname(BAR_EXE), "dsh_state.py")
    with open(eng, "wb") as fh:
        fh.write(real)
    return open(eng, "rb").read() == real


def check_pages_filled() -> None:
    """Task 6 离屏面：两页画了真控件，且**设置值被画回控件上**（setting → UI 那一半）。

    Task 7 把余额页也并到这里：它同样是「占位 → 真页」，判据口径与 notify/runtime 一致
    （色数与不透明像素两条地板值 + 一行跟着快照走），不再另起一段。

    占位页的形状是「一行标题 + 整页透明」，实测 7 色 / 0 不透明像素 —— 那两条地板值
    就是「填实没有」的分界（与 Task 5 给 appearance 定的口径同源）。
    另一半（UI → setting，改了到底生不生效）在 check_panel_settings_live() 的真窗口段。
    """
    print("\n== 通知页 / 运行页 / 余额页（离屏） ==")
    if not os.path.isfile(BAR_EXE):
        print("  （跳过：没有编译产物）")
        return
    if bar_processes():
        check("启动前无残留实例", False, "已有 Vigil 在跑")
        return
    f = os.path.join(ds.state_dir(), "settings.json")
    backup = open(f, encoding="utf-8").read() if os.path.isfile(f) else None
    reg_had = reg_run_value()
    real = engine_swap(fake_static_engine_source(True))
    if not check("离屏门禁的引擎注入点就位（定死一帧，两张图只差被测的那个设置项）",
                 real is not None, "bin 下没有 dsh_state.py 拷贝，注入无从下手"):
        return
    try:
        # ① 画没画出真控件
        write_settings(interval=2, notify=True, repeatSec=90, theme="light", showBar=True)
        fp_n_on, c_n, o_n = shot_fp("notify", "notify-on")
        check("notify 页已画出真实控件（离屏色数 > 20，占位页实测 7）", c_n > 20,
              f"色数 {c_n}、不透明 {o_n}")
        check("notify 页不再是透明占位（不透明像素 > 0）", o_n > 0, f"不透明像素 {o_n}")
        fp_r_a, c_r, o_r = shot_fp("runtime", "runtime-a")
        check("runtime 页已画出真实控件（离屏色数 > 20，占位页实测 7）", c_r > 20,
              f"色数 {c_r}、不透明 {o_r}")
        check("runtime 页不再是透明占位（不透明像素 > 0）", o_r > 0, f"不透明像素 {o_r}")
        fp_b_a, c_b, o_b = shot_fp("balance", "balance-a")
        check("balance 页已画出真实控件（离屏色数 > 20，占位页实测 7）", c_b > 20,
              f"色数 {c_b}、不透明 {o_b}")
        check("balance 页不再是透明占位（不透明像素 > 0）", o_b > 0, f"不透明像素 {o_b}")

        # ② 设置值有没有被画回控件：换掉那一项，画面必须跟着换
        write_settings(notify=False)
        fp_n_off, _, _ = shot_fp("notify", "notify-off")
        check("notify 开关把设置画出来了（notify=true / false 两张离屏图不同）",
            bool(fp_n_on) and fp_n_on != fp_n_off,
              f"两张指纹 {'相同' if fp_n_on == fp_n_off else '不同'}"
              f"（{fp_n_on[:8]}/{fp_n_off[:8]}）：相同就是开关压根没读 cfg.Notify")
        write_settings(repeatSec=33)
        fp_n_33, _, _ = shot_fp("notify", "notify-33")
        check("repeatSec 数字框显示的是设置值（33 / 90 两张离屏图不同）",
              bool(fp_n_33) and fp_n_33 != fp_n_on,
              f"90→{fp_n_on[:8]}、33→{fp_n_33[:8]}")
        write_settings(interval=7)
        fp_r_7, _, _ = shot_fp("runtime", "runtime-int7")
        check("interval 数字框显示的是设置值（2 / 7 两张离屏图不同）",
              bool(fp_r_7) and fp_r_7 != fp_r_a,
              f"2→{fp_r_a[:8]}、7→{fp_r_7[:8]}")

        # ③ 阈值那一行是跟着快照走的（不是构造时写死的一行字）
        with open(os.path.join(os.path.dirname(BAR_EXE), "dsh_state.py"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(fake_static_engine_source(False))
        write_settings(interval=2, notify=True, repeatSec=90)
        fp_r_no, _, _ = shot_fp("runtime", "runtime-nosnap")
        fp_n_no, _, _ = shot_fp("notify", "notify-nosnap")
        check("runtime 页的阈值行跟着快照变（有帧 / 无帧两张离屏图不同）",
              bool(fp_r_no) and fp_r_no != fp_r_a,
              f"有帧 {fp_r_a[:8]} vs 无帧 {fp_r_no[:8]}：相同就是那一行没接 Refresh")
        check("对照：notify 页不含快照相关内容（换掉快照两张仍一致，差别不来自随机性）",
              bool(fp_n_no) and fp_n_no == fp_n_on,
              f"notify 有帧 {fp_n_on[:8]} vs 无帧 {fp_n_no[:8]}：不同说明离屏出图本身不稳定，"
              "上面那几条「两张不同」就都不作数了")
        # 余额页那一行「当前余额 …（来源 …）」是 Refresh 里写的，不是构造时写死的：
        # 有帧 / 无帧两张必须不同。占位页（Task 7 之前的 BalancePage 是 PageBase 一行字）
        # 两张一样，所以这条对「只删了占位、页还没接快照流」的半成品也会红。
        fp_b_no, _, _ = shot_fp("balance", "balance-nosnap")
        check("balance 页的余额行跟着快照走（有帧 / 无帧两张离屏图不同）",
              bool(fp_b_no) and bool(fp_b_a) and fp_b_no != fp_b_a,
              f"有帧 {fp_b_a[:8]} vs 无帧 {fp_b_no[:8]}：相同就是 Refresh 没接上 balance")

        # 反向配对：引擎帧里**多带一个 key 字段**，界面必须画不出任何东西 ——
        # 带与不带两张出图逐字节相同。上面那几条「两张不同」钉的是该画的会画，
        # 这一条钉的是不该画的画不出来；占位页两张也一样，所以它对半成品是假绿的，
        # 但只有「余额行真的跟着快照走」那条绿了它才算数（两条成对，判据在下面）。
        with open(os.path.join(os.path.dirname(BAR_EXE), "dsh_state.py"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(fake_static_engine_source(True, with_key=False))
        fp_b_nk, _, _ = shot_fp("balance", "balance-nokey")
        check("界面对 Key 本体是聋的：帧里带不带 key 字段，两张离屏图逐字节相同",
              bool(fp_b_a) and bool(fp_b_nk) and fp_b_a == fp_b_nk,
              f"带 key {fp_b_a[:8]} vs 不带 {fp_b_nk[:8]}：不同就是界面把 Key 画出来了")
        # 换回去：下面 ④ 那张开机自启的对照图必须和 fp_r_a 只差注册表这一件事。
        # 留着 with_key=False 跑 ④，两张就差两个变量，那条门禁从此名不副实。
        with open(os.path.join(os.path.dirname(BAR_EXE), "dsh_state.py"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(fake_static_engine_source(True))

        # ④ 开机自启那一格读的是注册表现状，不是 settings.json
        reg_run_write(f'"{BAR_EXE}"')
        fp_r_reg, _, _ = shot_fp("runtime", "runtime-reg-on")
        check("开机自启开关反映注册表现状（Run 项在 / 不在 两张离屏图不同）",
              bool(fp_r_reg) and fp_r_reg != fp_r_a,
              f"Run 不在 {fp_r_a[:8]} vs 在 {fp_r_reg[:8]}：相同就是没读 AutostartOn()")
    finally:
        ok = engine_restore(real)
        check("离屏注入的引擎拷贝已按仓库根还原", ok, os.path.dirname(BAR_EXE))
        reg_run_write(reg_had)
        check("离屏场景的注册表 Run 项已还原到用户原状", reg_run_value() == reg_had,
              f"现在是 {reg_run_value()!r}，用户原状 {reg_had!r}")
        if backup is None:
            if os.path.isfile(f):
                os.remove(f)
        else:
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(backup)
        for tag in ("notify-on", "notify-off", "notify-33", "runtime-a", "runtime-int7",
                    "runtime-nosnap", "notify-nosnap", "runtime-reg-on",
                    "balance-a", "balance-nosnap", "balance-nokey"):
            p = os.path.join(ds.state_dir(), f"t6-{tag}.png")
            if os.path.isfile(p):
                os.remove(p)


def engine_pid_after(tail: str) -> int | None:
    """日志里最后一条「引擎已启动 PID=…」的 PID；没有给 None。

    它是「换过引擎进程」的唯一外部凭据：interval 改值与手动重启都该让它变，
    而 repeatSec / notify 这两项热生效项**不该**让它变 —— 同一条判据两边用。
    """
    pids = re.findall(r"引擎已启动 PID=(\d+)", tail)
    return int(pids[-1]) if pids else None


def thresholds_age(texts: list[str]) -> float | None:
    """从运行页阈值那一行的文字里取出「静默 N 秒」。

    那一行是 `当前判定：<label>｜会话 P｜静默 42 秒｜本次扫描 3 个会话`，
    静默秒数直接来自引擎那一帧的 age_sec，所以它是「帧还在进来」最省事的凭据。
    """
    for t in texts:
        m = re.search(r"静默\s*(\d+(?:\.\d+)?)\s*秒", t)
        if m:
            return float(m.group(1))
    return None


def close_windows(hwnds: list[int]) -> None:
    """只关测试自己开出来的那几扇窗口（定向 WM_CLOSE，不动别家的窗口）。"""
    u = _user32()
    for h in hwnds:
        u.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE


def wait_new_windows(base: set[tuple[int, str]], key: str, limit: float = 12.0
                     ) -> list[tuple[int, str]]:
    """等「标题里含 key 的新窗口」出现；返回 (hwnd, 标题) 列表，只含 base 里没有的那些。"""
    t0 = time.time()
    while time.time() - t0 < limit:
        fresh = [(h, t) for h, _c, t in list_top_windows() if (h, t) not in base and key in t]
        if fresh:
            return fresh
        time.sleep(0.5)
    return []


def check_panel_settings_live() -> None:
    """Task 6 真窗口面：面板里改的东西**到底生效没有**，全走 UIA（不经前台）。

    为什么必须真点：spec §6 的生效矩阵（notify/repeatSec 热生效、interval 重启生效）
    说的就是「面板里改完」那一刻之后的行为，只看 settings.json 变了不算数 ——
    文件是 App.SaveSettings() 写的，而消费方读的是内存里那个 Settings 单例，
    两者可以完全脱节（写盘了但没改内存，或改了内存没落盘）。
    本环境有前台锁（task-5-report.md §10.1），真点击要抢前台，所以驱动通路选
    Windows 自带的 UIAutomation：Toggle / Invoke / Value 三种模式都不需要窗口在前台，
    也不往系统输入队列里打任何东西。

    引擎用注入的假引擎（needs_action 持续吐帧、age_sec 每帧 +1），
    于是「有没有弹通知」「帧还在不在流」「引擎进程换没换」三件事全都有外部凭据：
      · 通知 → bar.log 里的「通知 […]」行（App.Balloon 真ShowBalloonTip 之后才记）；
      · 帧在流 → 运行页阈值行里的静默秒数在涨；
      · 进程换没换 → 日志里「引擎已启动 PID=…」。
    """
    print("\n== 通知页与运行页（真窗口 + UIA） ==")
    if not os.path.isfile(BAR_EXE):
        print("  （跳过：没有编译产物）")
        return
    if bar_processes():
        check("启动前无残留实例", False, "已有 Vigil 在跑")
        return
    req = os.path.join(ds.state_dir(), "panel.request")
    if os.path.isfile(req):
        os.remove(req)
    log_path = os.path.join(ds.state_dir(), "bar.log")
    f = os.path.join(ds.state_dir(), "settings.json")
    backup = open(f, encoding="utf-8").read() if os.path.isfile(f) else None
    reg_had = reg_run_value()
    before_pids = python_pids()
    real = engine_swap(fake_action_engine_source("needs_action"))
    if not check("真窗口段的引擎注入点就位（needs_action 持续吐帧）", real is not None,
                 "bin 下没有 dsh_state.py 拷贝"):
        return
    opened: list[int] = []
    try:
        write_settings(interval=2, notify=True, repeatSec=0, theme="light", showBar=True)
        log_off = os.path.getsize(log_path) if os.path.isfile(log_path) else 0
        subprocess.Popen([BAR_EXE, "--panel", "runtime"], cwd=os.path.dirname(BAR_EXE))
        hwnd, t0 = 0, time.time()
        while time.time() - t0 < 25:
            hwnd = top_window("Vigil 控制台")
            if hwnd:
                break
            time.sleep(0.4)
        if not check("真窗口：面板开在运行页", bool(hwnd), "找不到标题为 Vigil 控制台 的顶层窗口"):
            return
        res = uia(hwnd, "count")
        # 这条只钉「通路本身能用」（占位页也能枚举出 15 个元素，所以不拿元素数当门槛）：
        # 通路红 = 环境问题，后面每一条都是它的下游；页面填没填实由下面那些判据各自红。
        if not check("UIA 驱动通路可用（面板控件树枚举得出来）",
                     uia_ok(res) and len(res.get("COUNT", [])) == 1
                     and int(res["COUNT"][0]) >= 1,
                     f"{res.get('ERR') or res.get('COUNT')}"):
            return

        def cnt(k: str) -> int:
            return int((res.get(k) or ["0"])[0])

        check("运行页控件齐：数字框 1 / 数字框自带加减档 2 / 动作按钮 3 / 开关 1",
              cnt("EDITS") == 1 and cnt("BOXSPINS") == 2 and cnt("TOGGLES") == 1
              and sorted((res.get("BUTTONS") or [""])[0].split("|")) ==
                  sorted(["重启检测进程", "打开日志位置", "打开数据目录"]),
              f"EDITS={cnt('EDITS')} BOXSPINS={cnt('BOXSPINS')} TOGGLES={cnt('TOGGLES')} "
              f"BUTTONS={(res.get('BUTTONS') or [''])[0]!r}")
        check("运行页自带整页滚动容器（页内有 ScrollViewer，外壳不再代劳）",
              cnt("SCROLLERS") >= 1, f"内容列里的 ScrollViewer 数 = {cnt('SCROLLERS')}")

        # —— 阈值只读行：跟着快照走，不是构造时写死的一行字 ——
        t1 = uia(hwnd, "text", name="当前判定")
        a1 = thresholds_age(t1.get("TEXT", []))
        time.sleep(3.0)
        t2 = uia(hwnd, "text", name="当前判定")
        a2 = thresholds_age(t2.get("TEXT", []))
        check("阈值只读行跟着快照走（静默秒数在涨，且带的是引擎那一帧的状态标签）",
              a1 is not None and a2 is not None and a2 > a1
              and any("等你操作" in x for x in t2.get("TEXT", [])),
              f"静默 {a1}→{a2} 秒，文本 {t2.get('TEXT') or t1.get('TEXT')}")
        print(f"  阈值行现场：{(t2.get('TEXT') or t1.get('TEXT') or ['—'])[0]!r}"
              f"（静默 {a1}→{a2} 秒）")

        # —— interval：改值 → 引擎进程重启（spec §6 的「重启生效」那一支）——
        tail = log_tail(log_off)
        pid0 = engine_pid_after(tail)
        uia(hwnd, "step", index=0)
        time.sleep(1.5)
        tail = log_tail(log_off)
        pid1 = engine_pid_after(tail)
        got_interval = (json.loads(open(f, encoding="utf-8").read()).get("interval")
                        if os.path.isfile(f) else None)
        check("interval 步进一档真的写进了设置（settings.json 的 interval 变了）",
              got_interval == 3, f"现在是 {got_interval}（步进前 2）")
        check("interval 改值后日志出现新的「引擎已启动 PID=…」（重启生效，不是只改了文件）",
              pid0 is not None and pid1 is not None and pid1 != pid0,
              f"PID {pid0} → {pid1}；日志尾部 {tail[-200:]!r}")

        # —— 手动重启按钮 ——
        uia(hwnd, "invoke", name="重启检测进程")
        time.sleep(1.5)
        tail = log_tail(log_off)
        pid2 = engine_pid_after(tail)
        check("「重启检测进程」按钮真的又重启了一次（PID 再次变化）",
              pid2 is not None and pid2 != pid1, f"PID {pid1} → {pid2}")
        print(f"  引擎 PID：{pid0} →（interval 2→{got_interval}）{pid1} →（手动重启）{pid2}")

        # —— 开机自启：注册表，测完还原用户原状 ——
        uia(hwnd, "toggle", index=0)
        time.sleep(0.8)
        reg_on = reg_run_value()
        check("开机自启开关真的写了 HKCU\\...\\Run（值就是当前这个 exe 的路径）",
              reg_on == f'"{BAR_EXE}"', f"注册表里是 {reg_on!r}")
        uia(hwnd, "toggle", index=0)
        time.sleep(0.8)
        # 要求「刚才确实写进去过」再要求现在没了：只钉「关」这一半的话，
        # 占位页（一次都没写）也会因为「本来就没有」而假绿。
        check("再关一次真的把该项删掉（不是「本来就没有」）",
              reg_on == f'"{BAR_EXE}"' and reg_run_value() is None,
              f"开的时候 {reg_on!r}，关之后 {reg_run_value()!r}")
        print(f"  注册表 Run\\Vigil：开={reg_on!r} → 关={reg_run_value()!r}"
              f"（用户原状 {reg_had!r}）")

        # —— 两个目录入口：真的开了窗口，测试只关自己开出来的那几扇 ——
        # 判据口径统一成「新窗口标题里出现数据目录名」，两条都是**资源管理器的文件夹窗口**，
        # 不再依赖 `.log` 的文件关联（旧的那条把「系统的选取应用对话框」也算落点，
        # 而关联归 shell 决定，同一份代码会时红时绿 —— 见 task-6-report 的 flaky 段）。
        dirname = os.path.basename(ds.state_dir())
        base = {(h, t) for h, _c, t in list_top_windows()}
        uia(hwnd, "invoke", name="打开数据目录")
        fresh = wait_new_windows(base, dirname)
        opened += [h for h, _t in fresh]
        check("「打开数据目录」真的开了那个目录（新窗口标题里有数据目录名）",
              bool(fresh), f"数据目录 {dirname}，新窗口 {fresh}")
        print(f"  打开数据目录 → 新窗口 {fresh}")
        close_windows([h for h, _t in fresh])
        time.sleep(1.0)
        base = {(h, t) for h, _c, t in list_top_windows()}
        uia(hwnd, "invoke", name="打开日志位置")
        fresh = wait_new_windows(base, dirname, limit=8.0)
        opened += [h for h, _t in fresh]
        # bar.log 就在数据目录里，所以「定位到它」开的也是这一族的文件夹窗口：
        # 判据只钉「新窗口的标题里有数据目录名」这件必然发生的事。
        check("「打开日志位置」真的用资源管理器定位到 bar.log（新窗口标题里有数据目录名，"
              "判据不碰 .log 的打开方式）",
              bool(fresh), f"数据目录 {dirname}，新窗口 {fresh}")
        # 选中态只作现场、不进判据：资源管理器的列表项是虚拟化的，
        # 「这一刻枚举不枚举得到 bar.log」由它的渲染时机决定，不由产品决定。
        cls = {h: c for h, c, _t in list_top_windows()}
        it = uia(fresh[0][0], "item", name="bar.log") if fresh else {"ERR": ["没有新窗口"]}
        way = (f"{(it.get('ITEM') or ['?'])[0]} 选中={(it.get('SELECTED') or ['?'])[0]}"
               if "ITEM" in it and "SELECTED" in it
               else f"读不到（{it.get('ITEMMISS') or it.get('ITEMNOPAT') or it.get('ERR')}）")
        print(f"  打开日志位置 → 新窗口 {fresh}"
              f"（类名 {[cls.get(h) for h, _t in fresh]}，选中态现场：{way}）")
        close_windows([h for h, _t in fresh])
        time.sleep(1.0)

        # —— 整页滚动：外壳已不代劳，页面收到最小尺寸时必须自己滚 ——
        u = _user32()
        l, t, r, b = rect_of(hwnd)
        u.SetWindowPos(hwnd, 0, l, t, 760, 500, 0x0004 | 0x0010)  # NOZORDER|NOACTIVATE
        # 等**几何稳定**而不是睡固定秒数：重排没落定就取基线的话，"画面变了"会被算成"滚动了"。
        # 这里是这一整段最后一条固定 sleep（其余三处 Task 7 已改成等条件），而它恰好是
        # 2026-10-05 那次「可滚=False / 滚回顶部差 800 行」最说得通的落点：机器忙时
        # 2.5 秒不够，量到的是重排中间态。条件用 SVH（被测 ScrollViewer 的高度）连读两次不变。
        sc: dict = {}
        prev_h = -1
        settle_deadline = time.time() + 20
        while time.time() < settle_deadline:
            sc = uia(hwnd, "scroll")
            cur_h = int((sc.get("SVH") or ["-1"])[0])
            if uia_ok(sc) and cur_h > 0 and cur_h == prev_h:
                break
            prev_h = cur_h
            time.sleep(0.8)
        can = (sc.get("VSCROLLABLE") or ["False"])[0] == "True"
        # 两分支判据：可滚 **或** 最下面那颗按钮还在视口里。
        # 原来只认「可滚」，而运行页在 2026-10-07 把阈值行从 Ui.Row 右列换成整行堆叠
        # （Ui.StackedRow）之后变矮了，最小尺寸下整页放得下 —— 放得下不是缺陷，
        # 「放不下又滚不动、内容被裁在视口外」才是这条门禁要挡的那件事。
        rr = uia(hwnd, "rect", name="打开数据目录")
        rect_s = (rr.get("RECT") or [""])[0]
        inside = False
        if rect_s:
            bx, by, bw, bh = (int(v) for v in rect_s.split("|"))
            inside = by + bh / 2 <= b
        check("窗口收到最小尺寸时运行页的内容可达（可滚，或整页本来就放得下）",
              uia_ok(sc) and (can or inside),
              f"VerticallyScrollable={can} 末行按钮={rect_s or rr} 窗口底={b} "
              f"（extent/viewport 本机都报 0，只作现场不进判据；{sc.get('ERR')}）")
        # 原来这里还跟着一条像素判据（「滚到底变化 ≥30 行、滚回顶部与基线差 ≤N 行」），
        # 2026-10-06 拆掉，两条理由：
        #   ① 这一段没有能滚的通路 —— 面板的合成输入在本环境送不达窗口（定向 PostMessage
        #      WM_MOUSEWHEEL 3/3、抬到最上层后 SendInput 真滚轮 3/3、连侧栏左键点击都不换页，
        #      抓帧逐行零变化），只剩 UIA ScrollPattern 可滚，而 WPF 的
        #      ScrollViewerAutomationPeer 本机把 extent/viewport/percent 全报 0，
        #      滚没滚就只能看像素；
        #   ② 像素在这里也不是「滚没滚」的证据：带背衬的窗口一激活就整帧换色，实测
        #      1200×800 的 800 行里 800 行对不上，而表格顶沿/表头带/分隔线一个没变。
        #      这条族红了半年的「表头被整页滚动推走了」就是这么来的，产品没这个毛病。
        # 「内容在滚、不是被裁在视口外」这件事实改由 check_panel_scroll_selftest 钉：
        # 应用自己 ScrollToEnd，前后两张定帧逐行比。这一段只留「最小尺寸下这页可滚」这条 UIA 判据。
        print(f"  整页滚动现场：VerticallyScrollable={can}，SVH={sc.get('SVH')}，"
              f"extent/viewport 本机报 {sc.get('EXTENT')}/{sc.get('VIEWPORT')}（不进判据）")
        # 实验做完把窗口还给外壳默认尺寸：这一段后面还有通知页的四条判据，
        # 让它们跑在「被上一节顺手收窄到 760×500」的几何上，等于给以后留一个
        # 「改上一节的尺寸就红」的隐式依赖。
        u.SetWindowPos(hwnd, 0, l, t, r - l, b - t, 0x0004 | 0x0010)
        time.sleep(1.2)

        # —— 通知页：切过去（二次实例写请求 → 常驻实例导航）——
        # 一次调用就够：驱动脚本在 switch 之前就把 TOGGLES/EDITS/BOXSPINS/BUTTONS 打全了，
        # 「页面标题的文本」与「这页的控件数」因此来自**同一帧**控件树 —— 分两次调计时
        # 会拿到切换前后的两帧（本轮实测踩过：count 还是运行页、text 已经是通知页）。
        subprocess.run([BAR_EXE, "--panel", "notify"], cwd=os.path.dirname(BAR_EXE), timeout=30)
        res2: dict[str, list[str]] = {}
        t_n = ""
        t0 = time.time()
        while time.time() - t0 < 20:
            res2 = uia(hwnd, "text", name="托盘气泡提醒")
            if res2.get("TEXT"):
                t_n = res2["TEXT"][0]
                break
            time.sleep(1.0)
        if not check("面板已导航到通知页（托盘气泡提醒那一行在控件树里）", bool(t_n),
                     f"{res2.get('ERR') or '等满 20 秒没出现'}"):
            return

        def cnt2(k: str) -> int:
            return int((res2.get(k) or ["0"])[0])

        check("通知页控件齐：开关 1 / 数字框 1 / 数字框自带加减档 2",
              cnt2("TOGGLES") == 1 and cnt2("EDITS") == 1 and cnt2("BOXSPINS") == 2,
              f"TOGGLES={cnt2('TOGGLES')} EDITS={cnt2('EDITS')} BOXSPINS={cnt2('BOXSPINS')} "
              f"（SPINS 总数 {cnt2('SPINS')}：窗口收窄后整页滚动条的三颗翻页/箭头键也算 RepeatButton）")
        check("通知页自带整页滚动容器（页内有 ScrollViewer，外壳不再代劳）",
              cnt2("SCROLLERS") >= 1, f"内容列里的 ScrollViewer 数 = {cnt2('SCROLLERS')}")

        # —— notify / repeatSec：热生效（引擎进程不变）——
        tail = log_tail(log_off)
        rep0 = tail.count("还在等你操作")
        check("notify=true 时「需要操作」真的弹了通知（日志有那条提醒）",
              "DeepSeek 在等你操作" in tail, tail[-300:] or "无新日志")
        check("repeatSec=0 期间不重复提醒（帧一直在来，整段只弹了第一次那一条）",
              rep0 == 0 and "DeepSeek 在等你操作" in tail,
              f"重复提醒 {rep0} 条（这一段已经跑了 ~1 分钟 needs_action 帧）")
        uia(hwnd, "step", index=0)  # repeatSec 0 → 1
        time.sleep(0.8)
        got_repeat = (json.loads(open(f, encoding="utf-8").read()).get("repeatSec")
                      if os.path.isfile(f) else None)
        val = uia(hwnd, "value", index=0)
        check("repeatSec 步进一档生效（数字框读到 1，settings.json 也是 1）",
              got_repeat == 1 and (val.get("VALUE") or [""])[0] in ("1", "1.0"),
              f"文件里 {got_repeat}，控件显示 {(val.get('VALUE') or ['?'])[0]}")
        time.sleep(4.0)
        tail = log_tail(log_off)
        rep1 = tail.count("还在等你操作")
        pid3 = engine_pid_after(tail)
        check("面板里把 repeatSec 从 0 改成 1 → 同一个引擎进程开始重复提醒（热生效，没重启）",
              rep1 >= 2 and pid3 == pid2,
              f"重复提醒 {rep1} 条，引擎 PID {pid2}→{pid3}"
              "（相等才对：repeatSec 不该重启引擎）")
        # 关闸。时间窗必须从**开关真的被拨过去之后**才开始算：一次 uia() 调用里含
        # powershell 冷启动（~1.5s），拿调用前的日志长度当基线，会把关闸前那几条重复提醒算进来
        # —— 本轮实测就是这么假红了 2 条。
        tg = uia(hwnd, "toggle", index=0)
        st_now = uia(hwnd, "togstate", index=0)
        mark = os.path.getsize(log_path) if os.path.isfile(log_path) else 0
        time.sleep(4.5)
        after = log_tail(mark)
        pid4 = engine_pid_after(log_tail(log_off))
        check("面板里关掉通知总开关 → 提醒立刻停，引擎进程没换（热生效）",
              (st_now.get("STATE") or [""]) [0] == "Off"
              and "通知 [" not in after and pid4 == pid3,
              f"开关现状={(st_now.get('STATE') or ['?'])[0]}（TogglePattern 读回），"
              f"关闸后新增日志里通知 {after.count('通知 [')} 条（阈值 0），"
              f"引擎 PID {pid3}→{pid4}；日志尾部 {after[-200:]!r}")
        subprocess.run([BAR_EXE, "--panel", "runtime"], cwd=os.path.dirname(BAR_EXE), timeout=30)
        a3 = None
        t0 = time.time()
        while time.time() - t0 < 20:
            t3 = uia(hwnd, "text", name="当前判定")
            a3 = thresholds_age(t3.get("TEXT", []))
            if a3 is not None:
                break
            time.sleep(1.0)
        check("关掉通知之后引擎帧仍在进来（上面那条「没通知」不是引擎停了造成的）",
              a3 is not None and a2 is not None and a3 > a2,
              f"静默秒数 {a2} → {a3}（读不到就是运行页没接上快照流）")
        print(f"  通知现场：首次 {'有' if 'DeepSeek 在等你操作' in tail else '无'} 条；"
              f"repeatSec=0 期间重复 {rep0} 条 → 改成 1 之后 {rep1} 条 → 关闸后 4.5 秒内 {after.count('通知 [')} 条；"
              f"引擎 PID {pid2}→{pid3}→{pid4}；静默秒数 {a1}→{a2}→{a3}")
    finally:
        close_windows(opened)
        subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
        ok = engine_restore(real)
        check("真窗口段注入的引擎拷贝已按仓库根还原", ok, os.path.dirname(BAR_EXE))
        reg_run_write(reg_had)
        check("真窗口段的注册表 Run 项已还原到用户原状", reg_run_value() == reg_had,
              f"现在是 {reg_run_value()!r}，用户原状 {reg_had!r}")
        if backup is None:
            if os.path.isfile(f):
                os.remove(f)
        else:
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(backup)
        check("真窗口段的 settings.json 已还原",
              (open(f, encoding="utf-8").read() if os.path.isfile(f) else None) == backup, f)
        if os.path.exists(req):
            os.remove(req)
        left = python_pids() - before_pids
        t1 = time.time()
        while left and time.time() - t1 < 20:
            time.sleep(1)
            left = python_pids() - before_pids
        check("真窗口段之后无残留（Vigil / 引擎子进程 / panel.request 都清干净）",
              not bar_processes() and not left and not os.path.exists(req),
              f"Vigil={sorted(bar_processes())} python={sorted(left)} req={os.path.exists(req)}")


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
        check("启动前无残留实例", False, "已有 Vigil 在跑")
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
        subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
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
        check("records:null 场景：退出后 Vigil 已消失", not bar_processes(), str(bar_processes()))


def row_hashes(rgb: bytes, w: int, h: int) -> list[str]:
    """逐行指纹：判断「滚动之后画面哪些行变了」，两帧一比就知道从哪一行开始动。"""
    return [hashlib.md5(rgb[y * w * 3:(y + 1) * w * 3]).hexdigest() for y in range(h)]


GAP_ROWS = 20
"""`real_window_stats` 切纸色块时允许跨过的**非纸色段**最大行数（物理行）。
线/表头文字那类短段实测 2~9 行，两张卡之间的空隙实测 32 行 —— 20 落在中间，
100%~150% 缩放下这个次序都不变（Task 8 换主题刷后表格被行分隔线切成一片小段，
逐行硬切会让 lines 恒 0、等帧那条 while 白跑满 20 秒）。"""


def real_window_stats(rgb: bytes, w: int, h: int) -> dict:
    """真实窗口一帧的结构量：表格顶沿、表头文字那条墨迹带、成排的横向分隔线、
    最右条带里的整页滚动条滑块像素。

    横向探测带取 30%~90%：左边避开 188 逻辑像素的侧栏，右边避开窗口最右那 1px 描边。

    【Task 8 的口径变更：顶沿不再用「逐字节纯白占多数」认定，改成按当前主题现量纸色】
    旧口径钉的是 Pages.cs 里写死的 `Background = Brushes.White`（Task 5 Step 0 #1 的豁免）。
    本任务把表底换成 `Ui.CardKey` 后实测：真窗口那一片合成出来是 **#FEFEFE**（卡片键在浅底
    是半透明 #FEFEFE 贴在窗口底 #FAFAFA 上），旧的 `#FFFFFF` 判据从此**一条都认不出来** ——
    `top=-1`、`lines=0`，等帧那条 while 会一直跑到 20 秒时限才记 ✗（简报点名的「不是变红、
    是变慢死」就是这里）。现在纸色 = 探测带里**所有像素的众数**，深浅主题都跟着走。

    纸色块（block）：连续成片纸色的行段。概览页现在有三块 —— 状态卡、会话表、等你处理卡，
    中间被 18 DIP 的下边距（窗口底色 #FAFAFA，不是纸色）隔开。表格是**含分隔线最多的那一块**
    （实测 29 行时表格块 8 条线、两张卡各 0 条），`top` 取的就是这一块的顶行，
    和旧口径里「表格顶沿」是同一个东西 —— 旧版整页只有表格，第一块就是表格。

    「分隔线」= 这一行被同一种非纸色占掉一半以上，且上下 4 物理像素内**都**有纸色行，
    连续的线行归并成一条。两侧都要贴纸色是必需的：卡片外面那片主题底色（本机 #FAFAFA）
    也是整行同色，不加这道限定空表会被数成 26 条「线」，等待条件就成了摆设（本机实测）。
    125% 缩放下 1 逻辑像素的线会糊成两行（实测 y=111 是 #F7F7F7、y=112 是 #E2E2E2），
    所以按连续段归并、允许几像素缓冲。

    表头下沿**不能**拿「顶沿往下第一条线」当基准 —— 本机实测表头与第一行数据之间
    根本不画线（GridLinesVisibility=Horizontal 只画行与行之间），第一条线在第一行
    数据下面（y=111），拿它当基准会把第一行也圈进「不许动」，修好了照样红。
    改成量表头**文字的墨迹带**：表格块顶沿往下第一段连续墨迹（容 3 行空隙、撞线即停），
    它的下沿才是判据②的基准线（本机实测 y=38..52）。
    """
    x0, x1 = int(w * 0.30), int(w * 0.90)
    band = max(1, x1 - x0)
    counters: list[collections.Counter] = []
    allc: collections.Counter = collections.Counter()
    for y in range(h):
        base = y * w * 3
        cnt: collections.Counter = collections.Counter()
        for x in range(x0, x1):
            cnt[rgb[base + x * 3:base + x * 3 + 3]] += 1
        counters.append(cnt)
        allc.update(cnt)
    # 纸色 = 探测带众数（实测浅主题 #FEFEFE=卡片键、旧写死白底时是 #FFFFFF，
    # 两种形态下都是画面里最大的一片同色），不写死任何字面量。
    paper = allc.most_common(1)[0][0] if allc else b"\x00\x00\x00"
    paper_n = [c.get(paper, 0) for c in counters]
    dom: list[tuple[bytes, int]] = []
    for c in counters:
        rest = c.copy()
        rest.pop(paper, None)
        dom.append(rest.most_common(1)[0] if rest else (b"", 0))

    def is_paper(y: int) -> bool:
        return 0 <= y < h and paper_n[y] * 5 >= band * 2

    def is_line(y: int) -> bool:
        return 0 <= y < h and dom[y][1] * 2 >= band

    def near_paper(y: int) -> bool:
        return (any(is_paper(y - k) for k in (2, 3, 4))
                and any(is_paper(y + k) for k in (2, 3, 4)))

    def has_ink(y: int) -> bool:
        """有文字墨迹：非纸色像素成把，但不是整行的线。"""
        return 0 <= y < h and not is_line(y) and band - paper_n[y] >= 40

    # 纸色块切出来，表格 = 含分隔线最多的那一块（并列时取更高的那块）。
    # 段必须**跨过短线**才算一块：125% 缩放下一根 1 DIP 的行分隔线实测糊成两行
    # （y=284 是 #F6F6F6、y=285 是 #E1E1E1，两侧都是纸色），逐行硬切会把表格切成
    # 一-row-一块，块里永远不含线 → lines 恒 0、等帧那条 while 跑满 20 秒才红
    # （就是简报点名的「不是变红，是变慢死」的第二处）。
    # GAP_ROWS=20 物理行的容差是两侧都量过的：线/表头文字那种非纸色段实测 2~9 行，
    # 两张卡之间的空隙实测 32 行（168~197，状态卡底描边 + 18 DIP 下边距 + 「会话」标题），
    # 100%~150% 缩放下这个次序不变（20 介于两者之间）。
    blocks, yy = [], 0
    while yy < h:
        if is_paper(yy):
            s0 = e0 = yy
            yy += 1
            while yy < h:
                if is_paper(yy):
                    e0 = yy
                    yy += 1
                    continue
                k = yy
                while k < h and not is_paper(k):
                    k += 1
                if k - yy > GAP_ROWS or k >= h:
                    break
                yy = k
            blocks.append((s0, e0))
        else:
            yy += 1

    def line_count(a: int, b: int) -> int:
        n, y2 = 0, a
        while y2 <= b:
            if is_line(y2) and near_paper(y2):
                n += 1
                while y2 <= b and is_line(y2):
                    y2 += 1
                continue
            y2 += 1
        return n

    top, bottom = -1, h - 1
    if blocks:
        best = max(blocks, key=lambda bl: (line_count(bl[0], bl[1]), bl[1] - bl[0]))
        top, bottom = best
    groups, y = [], top + 1 if top >= 0 else h
    while y <= bottom:
        if is_line(y) and near_paper(y):
            groups.append(y)
            while y <= bottom and is_line(y):
                y += 1
            continue
        y += 1
    head0 = next((y for y in range(max(0, top), bottom + 1) if has_ink(y)), -1)
    head1, gap = head0, 0
    y = head0
    while head0 >= 0 and y + 1 <= bottom:
        y += 1
        if has_ink(y):
            head1, gap = y, 0
        elif is_line(y) or gap >= 3:
            break
        gap += 1
    # 最右 20 物理像素条带里「整页滚动条的滑块」有多少像素。
    # 【口径 2026-10-06 改：绝对灰阶 → 相对本带众数色】旧口径钉的是
    # `0x55 <= r <= 0xD0` 且三通道相近，可这条带子里画的是**窗口自己的背衬**：
    # light 主题本机实测整带 9738 个像素全是 #D3D3D3 —— 离 0xD0 只差 3 档。
    # Mica 采的是桌面，激活/失焦或壁纸一变就整体漂几档，于是同一份构建同一个布局，
    # 这一项能从 0 跳到 9738（那条「外壳有整页滚动条」的假红就是这么来的，
    # 而它下游的结构量分毫未动）。现在拿本带自己的众数色当参照：
    # 背衬再怎么漂都跟着漂、差值为 0，滑块（实测 #8A8A8A 对 #D3D3D3）照样看得见。
    strip: collections.Counter = collections.Counter()
    for y in range(max(0, top), h):
        base = y * w * 3
        for x in range(max(x0, w - 20), w - 2):
            strip[rgb[base + x * 3:base + x * 3 + 3]] += 1
    gray_ref = strip.most_common(1)[0][0] if strip else b"\x00\x00\x00"
    gray, gray_x0 = 0, -1
    for c, n in strip.items():
        if max(abs(c[k] - gray_ref[k]) for k in range(3)) > 24:
            gray += n
    # 光数像素不够：窗口自己那圈描边/圆角抗锯齿就贴着右沿，背衬没合成出来时
    # （PrintWindow 给一条 #000000 的黑带）描边整列都「离众数色差得远」，本机实测
    # 一次跑出 3868 个 —— 又是一条把测量的锅记成产品的红。滑块和描边分得开：
    # **滑块是一根几像素宽、几百像素高的同色竖条**，描边是逐行变化的梯度。
    # 所以再加一个「同色最长竖段」，和 shot_table_stats 的 vscroll 同一把尺。
    gray_run = 0
    for x in range(max(x0, w - 20), w - 2):
        run, prev = 0, None
        for y in range(max(0, top), h):
            base = y * w * 3
            c = rgb[base + x * 3:base + x * 3 + 3]
            off = max(abs(c[k] - gray_ref[k]) for k in range(3)) > 24
            run = run + 1 if (off and c == prev) else (1 if off else 0)
            prev = c if off else None
            gray_run = max(gray_run, run)
    if gray:
        for y in range(max(0, top), h):
            base = y * w * 3
            hit = [x for x in range(max(x0, w - 20), w - 2)
                   if max(abs(rgb[base + x * 3 + k] - gray_ref[k]) for k in range(3)) > 24]
            if hit:
                gray_x0 = hit[0]
                break
    return {"top": top, "bottom": bottom, "lines": len(groups),
            "first_line": groups[0] if groups else -1,
            "paper": paper, "blocks": len(blocks),
            "head0": head0, "head1": head1, "gray": gray, "gray_x0": gray_x0,
            "gray_run": gray_run, "gray_ref": gray_ref}


def flatten_png(path: str, bg: tuple[int, int, int] = (255, 255, 255)) -> tuple[int, int, bytes] | None:
    """离屏 PNG（RGBA，页面底是透明的）合成成 RGB，好把真窗口那一套结构量
    （`real_window_stats`：表格顶沿 / 表头墨迹带 / 行分隔线 / 块底）原样用在定帧上。

    合成用什么底色没有意义 —— 两张定帧走同一个 bg，比的是它们彼此差在哪几行。
    """
    got = read_png_rgba(path)
    if got is None:
        return None
    w, h, buf = got
    rgb = bytearray(w * h * 3)
    for i in range(w * h):
        a = buf[i * 4 + 3]
        o = i * 3
        if a >= 255:
            rgb[o], rgb[o + 1], rgb[o + 2] = buf[i * 4], buf[i * 4 + 1], buf[i * 4 + 2]
            continue
        for k in range(3):
            rgb[o + k] = (buf[i * 4 + k] * a + bg[k] * (255 - a) + 127) // 255
    return w, h, bytes(rgb)


SCROLL_ROWS = 29
"""滚动那两族判据喂的假引擎行数。29 行 > 概览页视口装得下的行数（实测 8~9 行），
内容溢出是「表格自己有一个会滚的视口」这件事的前提。"""


def check_panel_scroll_selftest() -> None:
    """滚动证据由应用自己交出来：`--panel-scroll` 把概览页按真窗口给它的**有限高度**排一遍，
    量内部那个 ScrollViewer 的 extent/viewport/offset，滚到底前后各出一张定帧，冒烟比这两张。

    为什么不在真窗口上发滚轮（这一族判据原先正是那么写的，而且红了半年）：本机实测
    三条合成输入通路**全都送不达**面板窗口 —— 定向 PostMessage WM_MOUSEWHEEL 3/3、
    抬到最上层（SWP_NOACTIVATE）之后 SendInput 真滚轮 3/3、连侧栏导航项的左键点击
    都不换页，抓帧逐行零变化；而窗口激活/失焦会让 Mica 背衬整帧换色，实测 1200×800 的
    800 行里 800 行对不上，可表格顶沿 y=259、表头墨迹带 271..288、分隔线 8 条一个没变。
    两件事叠在一起，「画面变了多少行」就不再是「滚没滚」的证据 ——
    旧判据报出来的那条「表头被整页滚动推走了 259 行」就是这么来的，产品没有这个毛病。
    """
    print("\n== 离屏滚动自证：表头固定、只有表体滚动 ==")
    eng = os.path.join(os.path.dirname(BAR_EXE), "dsh_state.py")
    if not check("滚动自证注入点就位", os.path.isfile(BAR_EXE) and os.path.isfile(eng), eng):
        return
    with open(os.path.join(HERE, "dsh_state.py"), "rb") as fh:
        real = fh.read()
    a = os.path.join(ds.state_dir(), "scroll-a.png")
    b = os.path.join(ds.state_dir(), "scroll-b.png")
    try:
        with open(eng, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(fake_engine_source(SCROLL_ROWS))
        for f in (a, b):
            if os.path.isfile(f):
                os.remove(f)
        try:
            p = subprocess.run([BAR_EXE, "--panel-scroll", "overview", a, b],
                               capture_output=True, text=True, errors="replace",
                               timeout=180, cwd=os.path.dirname(BAR_EXE))
        except subprocess.TimeoutExpired:
            p = None
        head = (((p.stdout or "").strip().splitlines() or [""])[0] if p else "")
        parts = head.split()
        parsed: list[int] = []
        if len(parts) == 9 and parts[0] == "SCROLL" and parts[1] == "overview":
            try:
                parsed = [int(x) for x in parts[2:]]
            except ValueError:
                parsed = []
        if not check("应用自证：--panel-scroll 出了两张定帧并报了数字",
                     bool(p) and p.returncode == 0 and len(parsed) == 7
                     and os.path.isfile(a) and os.path.isfile(b),
                     f"退出码 {p.returncode if p else '180 秒没返回'}，首行 {head!r}，"
                     f"stderr {(((p.stderr or '').strip()[:200]) if p else 'timeout')!r}"):
            return
        cw, ch, extent, viewport, off0, off1, swidth = parsed
        # ① 前提：内容真的溢出视口 —— 不溢出就没有「滚」这回事，后面全是空转
        check("应用自证：表格内容溢出自己的视口（有限高度真的交到了表格里）",
              extent > viewport >= 2 and viewport < SCROLL_ROWS,
              f"extent={extent} 行 / viewport={viewport} 行 / 引擎喂了 {SCROLL_ROWS} 行"
              f"（DataGrid 默认 CanContentScroll=true，这三个数的单位是**行**不是像素）："
              f"视口装得下全部行时滚动条没有存在的理由，这条判据也就无从谈起")
        # ② 滚到底：偏移必须走到 extent-viewport，一步不多一步不少
        check("应用自证：ScrollToEnd 真的滚到了底",
              off1 > off0 and off1 == extent - viewport,
              f"竖向偏移 {off0} → {off1} 行，理论底 {extent - viewport} 行")
        # 横向溢出：八列写死合计 950 DIP 时，概览页只给表格 ~722 宽 —— 真窗口截图实测
        # 表格里横着一根滚动条、「标题」整列看不见。末列改星宽 + 固定列收窄之后必须是 0。
        check("应用自证：默认宽度下表格不横向溢出（标题列没被推出视口）",
              swidth == 0,
              f"ScrollableWidth={swidth} DIP（>0 就是那根横向滚动条活着，末列在视口外）；"
              f"画布 {cw}×{ch}，页面给表格的那一块约 {cw - 2} 宽")
        fa, fb = flatten_png(a), flatten_png(b)
        if not check("应用自证：两张定帧同尺寸可解码",
                     fa is not None and fb is not None and fa[:2] == fb[:2] == (cw, ch),
                     f"A {fa and fa[:2]} / B {fb and fb[:2]}，应用报的是 {cw}x{ch}"):
            return
        w, h = fa[0], fa[1]
        sa, sb = real_window_stats(fa[2], w, h), real_window_stats(fb[2], w, h)
        if not check("应用自证：定帧里认得出表头与成排的分隔线（比样的基准线）",
                     sa["top"] >= 0 and sa["head0"] > sa["top"] and sa["lines"] >= 4,
                     f"表格顶沿 y={sa['top']}、表头墨迹带 y={sa['head0']}..{sa['head1']}、"
                     f"分隔线 {sa['lines']} 条（画布 {w}x{h}）：认不出来就没有「表头不许动」的基准"):
            return
        ra, rb = row_hashes(fa[2], w, h), row_hashes(fb[2], w, h)
        above = [y for y in range(0, sa["head1"] + 1) if ra[y] != rb[y]]
        body = [y for y in range(sa["head1"] + 1, sa["bottom"] + 1) if ra[y] != rb[y]]
        tail = [y for y in range(sa["bottom"] + 1, h) if ra[y] != rb[y]]
        # ③ 这条才是「表头固定」的正面证据：表头带连同它上面的一切（概览标题、状态卡、
        #    「会话」标题）逐行一模一样，表格块下面的「等你处理」卡也一动不动 ——
        #    整页滚动会把这两段一起推走，DataGrid 那个冻住的表头不会。
        check("应用自证：表头连同它上面的一切、表格下面的那张卡，逐行没动",
              not above and not tail,
              f"表头带 y0..{sa['head1']} 里变了 {len(above)} 行、表格块（y{sa['top']}..{sa['bottom']}）"
              f"下面变了 {len(tail)} 行；变了的那几行起点 "
              f"{(above + tail)[0] if (above or tail) else -1}")
        # ④ 防空转：③只说「上面没动」，这一条说「下面真的换了一整屏」
        check("应用自证：表体换了一整屏内容（不是什么都没滚）",
              len(body) >= (sa["bottom"] - sa["head1"]) * 0.5,
              f"表体 y{sa['head1'] + 1}..{sa['bottom']} 里变了 {len(body)} 行"
              f"（那一段 {sa['bottom'] - sa['head1']} 行，阈值取一半）")
        check("应用自证：滚前滚后的表格结构同形（视口没被滚大滚小）",
              (sa["top"], sa["bottom"], sa["lines"]) == (sb["top"], sb["bottom"], sb["lines"]),
              f"顶沿 {sa['top']}->{sb['top']}、块底 {sa['bottom']}->{sb['bottom']}、"
              f"分隔线 {sa['lines']}->{sb['lines']} 条、表头带 {sa['head0']}..{sa['head1']}"
              f"->{sb['head0']}..{sb['head1']}")
    finally:
        with open(eng, "wb") as fh:
            fh.write(real)
        check("滚动自证：注入后引擎拷贝已按仓库根还原",
              open(eng, "rb").read() == real, eng)
        for f in (a, b):
            if os.path.isfile(f):
                os.remove(f)


def check_panel_real_scroll() -> None:
    """真实窗口这一侧只留**静态**判据：外壳有没有把有限高度交给页面。

    为什么离屏那两条（check_panel_row_data）守不住这件事：`--panel-shot` 是直接把页面
    Measure/Arrange 到 724×552 上出图的，**压根不经过 PanelWindow 的外壳**。外壳一旦把
    Host 包回 ScrollViewer，页面拿到的竖向约束就是无限高，表格按行数长到 ~1150px
    再由整页滚动兜着 —— 表头跟着一起滚出视野，而离屏门禁全绿。所以这一族必须在真窗口上量。

    「滚起来是什么样子」不在这里量（合成输入送不达面板窗口，见
    `check_panel_scroll_selftest` 的说明），这里量的是两件静态事实：
      ① 概览页在真窗口里画出了数据行（不画出来，②都是空的）；
      ② 表格被**自己**的视口裁住了 —— 引擎喂 29 行，画面上只数得到几行分隔线那一截，
         且表格块底停在 y=677 而不是贴住画布下沿。外壳一旦把页面包回 ScrollViewer，
         块底就变成 y=799（画布 800）—— 反证走过一遍，当场红。
    外加一条测量自检：静置两帧之间结构量必须一致。带背衬的窗口会因激活/失焦整帧换色，
    逐行像素比不得（本机实测 800 行全变而结构量分毫不动），所以这一族判据比的是结构量。
    """
    print("\n== 真实窗口：外壳给页面的是有限高度 ==")
    if not os.path.isfile(BAR_EXE):
        print("  （跳过：没有编译产物）")
        return
    if bar_processes():
        check("启动前无残留实例", False, "已有 Vigil 在跑")
        return
    eng = os.path.join(os.path.dirname(BAR_EXE), "dsh_state.py")
    if not check("真实窗口注入点就位", os.path.isfile(eng), eng):
        return
    with open(os.path.join(HERE, "dsh_state.py"), "rb") as fh:
        real = fh.read()
    before = python_pids()
    shot = os.path.join(ds.state_dir(), "smoke-panel-real.png")
    u = _user32()
    try:
        with open(eng, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(fake_engine_source(SCROLL_ROWS, watch=True))
        subprocess.Popen([BAR_EXE, "--panel"], cwd=os.path.dirname(BAR_EXE))
        hwnd, t0 = 0, time.time()
        while time.time() - t0 < 25:
            hwnd = top_window("Vigil 控制台")
            if hwnd:
                break
            time.sleep(0.4)
        if not check("真实窗口：面板已打开（--panel 起真窗口）", bool(hwnd),
                     "找不到标题为 Vigil 控制台 的顶层窗口"):
            return
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
        # ① 先证明「表格真的画上了数据行」，否则②说不清是谁的锅。
        if not check("真实窗口：概览页画出了数据行",
                     bool(st0) and st0["top"] >= 0 and st0["head0"] > st0["top"]
                     and st0["lines"] >= 4,
                     f"抓取 {w}x{h}（缩放 {scale}），表格顶沿 y={st0 and st0['top']}、"
                     f"表头墨迹带 y={st0 and st0['head0']}..{st0 and st0['head1']}、"
                     f"横向分隔线 {st0 and st0['lines']} 条：等满 20 秒也没成排，"
                     f"说明假引擎那 {SCROLL_ROWS} 行没喂进面板"):
            return
        print(f"  基线：表格顶沿 y={st0['top']}、表头墨迹带 y={st0['head0']}..{st0['head1']}、"
              f"分隔线 {st0['lines']} 条、块底 y={st0['bottom']}、"
              f"最右条带滑块像素 {st0['gray']}（最长同色竖段 {st0['gray_run']}，"
              f"x0={st0['gray_x0']}，带底色 #{st0['gray_ref'].hex()}）、"
              f"PrintWindow 通路 {capture_path(hwnd)}")
        # 测量自检：静置两帧之间结构量一致。这一条红 = 画面根本不稳定（背衬在换、面板在重建），
        # 后面②③的红就先别按产品回归查。
        time.sleep(2.5)
        w2, h2, rgb2 = capture_bar(hwnd, shot, scale=scale)
        st1 = real_window_stats(rgb2, w2, h2) if w2 else {}
        check("真实窗口：静置两帧之间结构量一致（逐行像素比不得，比的是结构量）",
              (w2, h2) == (w, h) and bool(st1)
              and (st1["top"], st1["bottom"], st1["lines"])
              == (st0["top"], st0["bottom"], st0["lines"])
              and not capture_flips[hwnd],
              f"顶沿 {st0['top']}->{st1.get('top')}、块底 {st0['bottom']}->{st1.get('bottom')}、"
              f"分隔线 {st0['lines']}->{st1.get('lines')} 条，抓帧 {w2}x{h2}（基线 {w}x{h}），"
              f"通路 {capture_path(hwnd)}（切换 {capture_flips[hwnd]} 次，"
              f"切换一次就代表两帧逐行不可比，见 capture_bar 上方那段）")
        # ② 溢出发生在表格内部：可见行数远小于引擎行数。
        check("真实窗口：表格被自己的视口裁住（溢出在表格里，不是整页长高）",
              st0["lines"] + 1 < SCROLL_ROWS and st0["bottom"] < h - 8,
              f"{SCROLL_ROWS} 行数据，画面上的表格里只数到 {st0['lines'] + 1} 行上下"
              f"（分隔线 {st0['lines']} 条），表格块底在 y={st0['bottom']}、"
              f"窗口高 {h}：整页长高的形状是块底贴到画布下沿、行数一路数到顶")
        # ③ 外壳右沿不许有整页滚动条的滑块。
        # 原来这里还有第三条「外壳右沿没有整页滚动条的滑块」（按颜色数滑块像素）。
        # 2026-10-06 撤成只出现场、不进判据，因为它量的那条带子画的是**窗口自己的背衬**，
        # 而背衬在不在图里由 PrintWindow 走哪条通路决定：flag=2 时 light 主题整带实测
        # #D3D3D3（离旧口径 0x55..0xD0 的上限只差 3 档，同一次运行能从 0 跳到 9738 ——
        # 那条假红的来源），flag=0 时整带根本没合成、是 #000000，于是窗口自己那圈描边
        # 成了「离众数色差得远的一根 539 像素同色竖段」（同一份构建、同一个布局）。
        # 颜色在这里量不到产品。缺陷形状已经被②抓住：包进 ScrollViewer 时表格块底
        # y=799 贴住画布下沿（反证实测），有限高度下块底停在 y=677 —— 差 123 像素。
        print(f"  右沿现场（不进判据）：滑块状像素 {st0['gray']} 个、"
              f"最长同色竖段 {st0['gray_run']}、带底色 #{st0['gray_ref'].hex()}")
    finally:
        subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
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
        check("真实窗口场景：退出后 Vigil 已消失", not bar_processes(), str(bar_processes()))
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
            found = top_window("Vigil 控制台")
            if found:
                break
            time.sleep(0.4)
        check("面板窗口已出现", bool(found), "找不到标题为 Vigil 控制台 的顶层窗口")
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
        check("关窗后面板不再可见", not top_window("Vigil 控制台"), "窗口还看得见")
        subprocess.run([BAR_EXE, "--panel", "about"], cwd=os.path.dirname(BAR_EXE),
                       timeout=30)
        t1 = time.time()
        while time.time() - t1 < 20 and not top_window("Vigil 控制台"):
            time.sleep(0.4)
        check("再次请求能重新打开面板", bool(top_window("Vigil 控制台")), "面板没能再打开")
    finally:
        subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
        time.sleep(3)
        check("退出后无残留", not bar_processes(), str(bar_processes()))


DIRECT_AGREE_MIN = 0.80
"""真窗口页面取样框的配色分布，与**同一页**离屏参照的最低重合度。

本机实测（假引擎 29 行、theme 走 settings.json 的默认浅）：
概览 0.905、外观 0.950、四个占位页 0.9996 —— 正确侧最低 0.90，
离阈值还有 0.10 缓冲（用户鼠标正好停在某一行上会多带走 3~4 个百分点的悬停底色）。
把 Navigate 还原成 Task 3 的坏版本后实测：请求外观页却停在概览，
与外观参照的重合度 0.175、与四个占位参照的 0.0002 —— 走错页那一侧离阈值也差四倍多。
两侧都不靠「概览页是白底」这个写死事实，Task 8 换主题刷后照样成立（同一轮实测过）。

**这条阈值单独用会被陈帧蹭过去**：2026-10-06 实测「请求外观、画面还是通知那一屏」的
陈帧对自己的参照给到 0.8110 —— 两页都是深底白字，配色分布本来就近。所以 settle()
在比样之前还要求「这一帧和请求之前那一帧逐字节不同」（见下面 settle 的②），
新鲜度归新鲜度、像不像归像不像，不靠把阈值顶到 0.9 去赌。"""


PAGE_HEADINGS = {"overview": "概览", "notify": "通知", "appearance": "外观",
                 "runtime": "运行", "balance": "余额", "about": "Vigil"}
"""每页 `Ui.Heading(...)` 那一个标题，六页各不相同。

用来拿**活树**的证据判断「Host 里换没换页」：窗口被别家盖住时 DWM 不重新合成，
PrintWindow 会连着几次交出上一页那张位图。像素只配回答「画得像不像这页」，
「换没换页」得问控件树。"""


TEXT_FLOOR_DIP = {"notify": 180, "appearance": 180, "runtime": 180, "balance": 180,
                  # 关于页在 2026-10-07 补齐 V5 后有了长说明列（许可清单每条都是
                  # 「名称 + 用途 + 右侧许可证/上游」），不再是"只有三行短取值"，进射程。
                  "about": 180}
"""四页里「长说明文字」最窄可以接受到多少 DIP（Ui.Row 的 Star 列）。

这条是给 Task 7 复核 I4 补的：余额页那行右侧摆了输入框 + 两颗按钮，把说明文字挤到
一行三四个字，而**任何像素门禁都看不见** —— 离屏参照与真窗口是同一块挤压后的排版，
比样照样 0.95+。所以量控件包围盒，不量颜色。
180 这个地板值是量出来的，不是猜的：把余额页改回 I4 那一版（输入框 300 + 长按钮文案）
实测 **87 DIP** 并当场红，而修好之后四页的实测区间是 **275 ~ 528 DIP**
（notify 528 / appearance 275 / runtime 309 / balance 299，连续三轮跑批都是 299）——
两侧离阈值都还有一倍以上，不会时红时绿。概览页（表格标题列）与关于页（Task 9 才填实）不在这里。
**只反证过 balance 那一页**：其余三条钉的是同一个动作、同一份实现，但「把它们改坏会不会红」没被演示过 —— 记在这里，别让它们看起来比实际更可信。
"""


def check_panel_direct_page() -> None:
    """「请求了哪一页」必须等于「面板画了哪一页」，而且要用**正证据**判。

    为什么重写（Task 5 轮 2 复核事 1）：旧那条挂在 check_showbar_hidden 里的
    「面板真的切到了 appearance 页」判的是 `内容区纯白像素 < 5000` ——
    ① 它的区分度**全部来自概览页那张写死的白底表格**（Step 0 #1 明文豁免的那个）。
      Task 8 按计划把表底换成主题刷之后概览页也没有成片 #FFFFFF，这条就从「能红」
      退化成「永远绿」——坏门禁不报错、只沉默，比红更糟；
    ② 它躲在 showBar 场景里，是那个场景的副产品，重构 showBar 段就跟着一起消失。
    现在它站在自己的段里，判据换成**同一 theme、同一份假引擎下，真窗口那一页的配色分布
    必须等于该页离屏参照的配色分布**（离屏与上屏由同一个 `PanelPages.TryCreate(key)` 造页，
    换刷时两边一起变，见上面 PAGE_BOX_DIP 那一段的三条换算规则），
    外加一条零容差的「六个请求产生六种内容」。
    概览页白底换成主题刷之后这两条仍然有效 —— 本轮的 (b) 组红→绿证据就是拿这个假设量出来的。

    走的是 `--panel` 的两条直达通路：冷启动（`Vigil.exe --panel appearance`，
    Task 5 复核里撞出的那条 Task 3 缺陷路径）与请求通路（二次实例写 panel.request →
    常驻实例 ShowOn → Navigate）。两条都终点在同一个 Navigate 上。
    """
    print("\n== 直达页 = 请求页 ==")
    if not os.path.isfile(BAR_EXE) or bar_processes():
        print("  （跳过：无产物或已有实例）")
        return
    eng = os.path.join(os.path.dirname(BAR_EXE), "dsh_state.py")
    if not check("直达段注入点就位", os.path.isfile(eng), eng):
        return
    with open(os.path.join(HERE, "dsh_state.py"), "rb") as fh:
        real = fh.read()
    before = python_pids()
    req = os.path.join(ds.state_dir(), "panel.request")
    shot = os.path.join(ds.state_dir(), "smoke-panel-direct.png")
    refs = {p: os.path.join(ds.state_dir(), f"panel-direct-{p}.png") for p in PANEL_PAGES}
    u = _user32()
    try:
        # 一份假引擎同时喂两条通路：--panel-shot 走 --json（出一帧就退），面板走 --watch（持续吐帧）。
        # 数据固定成 29 行，离屏那张与真窗口那张才是同一份内容的两种画法，
        # 拿本机真实会话当基准的话，两次取样的行数都能对不上。
        with open(eng, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(fake_engine_source(29, watch=True))
        for page in PANEL_PAGES:
            if os.path.isfile(refs[page]):
                os.remove(refs[page])
            run_shot(page, refs[page])

        # 冷启动直达非默认页：Task 3 的 Navigate 就是把这一步的渲染压掉的
        # （导航条亮「外观」、Host 里还是概览页），默认页恰好是 0 号，冷开与旧冒烟都没暴露它。
        subprocess.Popen([BAR_EXE, "--panel", "appearance"], cwd=os.path.dirname(BAR_EXE))
        hwnd, t0 = 0, time.time()
        while time.time() - t0 < 25:
            hwnd = top_window("Vigil 控制台")
            if hwnd:
                break
            time.sleep(0.4)
        if not check("冷启动直达：面板窗口已出现（--panel appearance）", bool(hwnd),
                     "找不到标题为 Vigil 控制台 的顶层窗口"):
            return
        scale = (u.GetDpiForWindow(hwnd) or 96) / 96.0
        w0, h0, rgb0 = capture_bar(hwnd, shot, scale=scale)
        bg, bg_n = strip_backdrop(rgb0, w0, h0, scale)
        # 半透明主题刷要先合成回底才比得了（离屏图存的是 RGBA，PrintWindow 给的是合成完的 RGB），
        # 所以这条底色带是整段比样地基；量不到就当场停，别拿猜出来的底色彩一条绿的过去。
        if not check("外壳底色取样条量到了（半透明主题刷的合成基准）", bg_n >= 50,
                     f"侧栏与页面之间那条带只有 {bg_n} 个像素可数，无从确定合成回底："
                     f"{fmt_rgba(bg + bytes([255])) if bg else '空'}"):
            return
        check("取样框前提：面板窗口是外壳默认的 960×640",
              (w0, h0) == (int(960 * scale), int(640 * scale)),
              f"抓帧 {w0}x{h0}（缩放 {scale}），页面取样框 {PAGE_BOX_DIP} 是按这套外壳布局"
              f"（nav 188 + Host 边距 24/20）算出来的，窗口尺寸一变就得重derive")
        expect = {p: shot_profile(refs[p], bg) for p in PANEL_PAGES}
        if not check("六页离屏参照都可解码，且画布就是比样前提的那 724×600",
                     all(v is not None for v in expect.values()),
                     "读不出来 / 画布对不上的页：" + " ".join(
                         p for p in PANEL_PAGES if expect[p] is None)
                     + f"（参照必须正好是 {SHOT_PAGE_DIP}，裁切块 {PAGE_W_DIP}×{PAGE_H_DIP} "
                     "才和真窗口取样框同几何；App.cs RenderShot 改尺寸就要同步这两个常量）"):
            return

        def settle(page: str, before: str = "", limit: float = 12.0):
            """等这一页真的装上、再画上，然后交出现场。

            三道关，顺序不能反：
              ① 先等**活树**（UIA）里出现这一页的标题 —— 像素回答不了「换没换页」；
              ② 再要求这一帧**新**：与请求之前抓的那帧逐字节不同。窗口被别家盖住时
                 DWM 不重新合成，PrintWindow 能连着几次交出上一页那张位图
                 （本机实测：陈帧对自己那页的参照还能给到 0.811，蹭得过 0.80 的比样阈值）；
              ③ 最后才比配色分布。快照是一帧一帧推进来的，抢在空屏上比必然假红。
            不够就等到超时，把最后一次实测交给 check —— 走错页那种红等多久都不会变绿，
            等出来的红才是红；而「帧压根没换过」这种红，红因在取样不在产品（现场里写明）。
            """
            heading, tree = PAGE_HEADINGS[page], False
            t_h = time.time()
            while time.time() - t_h < limit:
                names = uia(hwnd, "text", name=heading).get("TEXT") or []
                if heading in names:
                    tree = True
                    break
                time.sleep(0.4)
            t_start, last = time.time(), (0.0, "", "", 0.0)
            while True:
                cw, ch, buf = capture_bar(hwnd, shot, scale=scale)
                if cw:
                    prof = box_profile(buf, cw, ch, scale)
                    ov = profile_overlap(prof, expect[page])
                    hh = box_hash(buf, cw, ch, scale)
                    last = (ov, fmt_profile(prof), hh, time.time() - t_start)
                    if ov >= DIRECT_AGREE_MIN and (not before or hh != before):
                        break
                if time.time() - t_start >= limit:
                    break
                time.sleep(1.0)
            return last + (tree, heading, not (before and last[2] == before))

        def report(page: str, via: str, ov: float, prof: str, t_wait: float,
                   tree: bool, heading: str, fresh: bool) -> bool:
            print(f"  {via} → {page}：重合度 {ov:.4f}（阈值 {DIRECT_AGREE_MIN}，"
                  f"等了 {t_wait:.1f}s）｜活树标题「{heading}」{'在' if tree else '不在'}"
                  f"｜{'帧已换新' if fresh else '帧与请求前逐字节相同（陈帧）'}"
                  f"｜真窗口 {prof}｜参照 {fmt_profile(expect[page] or {})}")
            check(f"{via}：这一页真的装进了 Host（UIA 活树里有标题「{heading}」）",
                  tree, f"等满 12 秒，控件树里没有出现「{heading}」—— Host 还躺在上一页上。"
                        f"（像素那条判据单独看它：陈帧的配色分布对错误参照也能给到 "
                        f"{DIRECT_AGREE_MIN} 上下，所以「换没换页」只能问活树）")
            return check(f"{via}：面板显示的就是 {page} 页（配色分布 = 该页离屏参照）",
                         ov >= DIRECT_AGREE_MIN and fresh,
                         f"重合度 {ov:.4f}（阈值 {DIRECT_AGREE_MIN}，等了 {t_wait:.1f}s）；"
                         + ("" if fresh else
                            "等满 12 秒 PrintWindow 交回来的还是请求之前那一帧（逐字节相同）—— "
                            "窗口被别家盖住、DWM 没重新合成，红因在取样不在产品；"
                            "上面那条活树判据绿着，说明页确实换了。")
                         + f"真窗口取样框现场 {prof} vs {page} 页离屏参照 "
                           f"{fmt_profile(expect[page] or {})}；"
                           f"参照图 {refs[page]}（同一 theme、同一份假引擎、同一个页面工厂）")

        ov, prof, _hh, waited, tree, heading, fresh = settle("appearance")
        report("appearance", "冷启动直达：--panel appearance", ov, prof, waited,
               tree, heading, fresh)

        hashes: dict[str, str] = {}
        for page in PANEL_PAGES:
            if os.path.isfile(req):
                os.remove(req)
            # 「请求之前那一帧」是新鲜度的参照：拿不到它就退化成只比配色。
            wb, hb, bufb = capture_bar(hwnd, shot, scale=scale)
            # 别叫 before：本函数 finally 里那个 `before = python_pids()` 是 PID 集合。
            pre_hash = box_hash(bufb, wb, hb, scale) if wb else ""
            # 用 Popen 而不是 run(timeout=30)：这句的本意是"二次实例把请求递进去就退"，
            # 可一旦常驻实例不在了（前面的门禁把它收了、或前台被抢导致它没起来），
            # 这一句就变成**冷启动一个不退出 Vigil 主进程**，run() 必然等满 30 秒抛
            # TimeoutExpired，把整轮冒烟带崩（2026-10-05 实测：崩在 --panel about）。
            # 请求到底递没递进去，下面那个"请求文件是否被消费"的轮询才是判据。
            subprocess.Popen([BAR_EXE, "--panel", page], cwd=os.path.dirname(BAR_EXE))
            t1 = time.time()
            while time.time() - t1 < 12 and os.path.isfile(req):
                time.sleep(0.3)
            consumed = not os.path.isfile(req)
            ov, prof, hh, waited, tree, heading, fresh = settle(page, pre_hash)
            hashes[page] = hh
            report(page, f"二次实例 --panel {page}"
                   f"{'（请求文件已消费）' if consumed else '（请求文件 12 秒没被消费！）'}",
                   ov, prof, waited, tree, heading, fresh)
            # 说明文字有没有被右侧字段挤坏：像素门禁看不见这件事（参照与现场是同一块
            # 挤压后的排版），所以直接量长 TextBlock 的包围盒宽度。
            # 概览页不在射程里：那张表里的会话标题也是长 TextBlock，窄列是它的设计。
            if page in TEXT_FLOOR_DIP:
                wd = uia(hwnd, "wide")
                got = int((wd.get("MINWIDE") or ["-1"])[0])
                print(f"  {page} 页最窄的长说明文字实测 {got} DIP："
                      f"{(wd.get('MINWIDEWHO') or ['?'])[0]!r}")
                check(f"{page} 页的说明文字列没有被右侧字段挤坏（最长那段宽 ≥ "
                      f"{TEXT_FLOOR_DIP[page]} DIP）",
                      uia_ok(wd) and got >= TEXT_FLOOR_DIP[page],
                      f"最窄的长文本 {(wd.get('MINWIDEWHO') or ['?'])[0]!r} 实测 {got} DIP"
                      f"（Task 7 复核那次挤到 ~48 DIP，一行只放得下三四个字）；"
                      f"{wd.get('ERR')}")
        dup = [k for k, v in hashes.items() if list(hashes.values()).count(v) > 1]
        check("六个直达请求产生六种内容（没有一个请求停在上一页上）",
              len(set(hashes.values())) == len(hashes),
              f"取样框指纹去重后 {len(set(hashes.values()))}/{len(hashes)}，"
              f"画同一片内容的：{' '.join(dup)}（Navigate 不渲染时六次请求全是概览那一张表）")
    finally:
        subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
        time.sleep(3)
        with open(eng, "wb") as fh:
            fh.write(real)
        check("直达段：注入后引擎拷贝已按仓库根还原", open(eng, "rb").read() == real, eng)
        for p in list(refs.values()) + [shot]:
            if os.path.isfile(p):
                os.remove(p)
        check("直达段：退出后 Vigil 已消失", not bar_processes(), str(bar_processes()))
        left_pids = python_pids() - before
        t3 = time.time()
        while left_pids and time.time() - t3 < 20:
            time.sleep(1)
            left_pids = python_pids() - before
        check("直达段：退出后无引擎孤儿子进程", not left_pids, f"仍在跑 {sorted(left_pids)}")


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

        # Task 6 Step 5：notify / repeatSec 这两个新页面读得走的字段，写进设置后面板仍正常。
        # （它们「画回控件上」的正面证据在 check_pages_filled()，这里只钉「带着这两项出图不崩」。）
        with open(f, "w", encoding="utf-8") as fh:
            json.dump({"interval": 7, "notify": False, "repeatSec": 33,
                       "theme": "light", "showBar": True}, fh)
        p = run_shot("notify", "-")
        check("notify/repeatSec 写进设置后面板仍正常",
              p is not None and p.returncode == 0 and "SHOT" in p.stdout,
              "" if p is None else (p.stdout + p.stderr)[-160:])

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
            hwnd = top_window("Vigil 控制台")
            if hwnd:
                break
            time.sleep(0.4)
        check("showBar=false：面板仍能打开（--panel 直达）", bool(hwnd),
              "找不到标题为 Vigil 控制台 的顶层窗口")
        # 「直达页 == 请求页」的证据不在这里。Task 5 轮 1 曾在这儿量过一片纯白像素，
        # 它的区分度全来自概览页那张写死的白底表格（Step 0 #1 的明文豁免），
        # Task 8 换主题刷后就会静默变成「永远绿」；而且它躲在 showBar 场景里，
        # 重构这一段就会跟着一起消失。现在由 check_panel_direct_page() 独立成段，
        # 拿同一 theme、同一份假引擎下的**离屏参照配色分布**做正证据。
        bars = find_bar_windows()
        check("showBar=false：任务栏里没有停靠的状态条（真的消失了）", not bars,
              f"Shell_TrayWnd 下仍挂着 {len(bars)} 个 Vigil 子窗口")
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
        check("showBar=false：面板窗口仍在（能从唤起回到面板）", bool(top_window("Vigil 控制台")),
              "请求消费了但窗口不在了")
        subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
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
        subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
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

    check_panel_window() 用 `Vigil.exe --panel` 起的正是常驻实例本身，
    PanelWindow.Instance 在它的构造函数里就被置上了，所以那条链路从没覆盖过
    「Instance 还是 null、只能惰性创建」的路径 —— 而日常最常见的情形恰恰是它：
    开机自启的普通 Vigil.exe + 用户后来敲一次 `Vigil.exe --panel`。
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
        check("冷打开前面板不存在", not top_window("Vigil 控制台"), "状态栏一起来面板就在")
        subprocess.run([BAR_EXE, "--panel", "balance"], cwd=os.path.dirname(BAR_EXE),
                       timeout=30)
        found, t1 = 0, time.time()
        while time.time() - t1 < 25 and not (found := top_window("Vigil 控制台")):
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
        subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
        deadline = time.time() + 20
        while time.time() < deadline and (bar_processes() or python_pids() - before):
            time.sleep(1)
        check("冷打开后无残留", not bar_processes() and not (python_pids() - before),
              f"Vigil={sorted(bar_processes())} python={sorted(python_pids() - before)}")


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


def _hue(r: int, g: int, b: int) -> float:
    """RGB → 色相角度（0-360）。用来判「这是不是紫色」，不是用来做色彩管理。"""
    mx, mn = max(r, g, b), min(r, g, b)
    if mx == mn:
        return -1.0
    if mx == r:
        h = (g - b) / (mx - mn)
    elif mx == g:
        h = 2.0 + (b - r) / (mx - mn)
    else:
        h = 4.0 + (r - g) / (mx - mn)
    return ((h * 60.0) + 360.0) % 360.0


# 紫色相区间。245° 以下算蓝（原 answering 的 #2563EB 是 221°），
# 300° 以上算品红/粉（配色积累里的 #F58FA6 是 345°）—— 两端都不误伤。
PURPLE_LO, PURPLE_HI = 245.0, 300.0


def check_shot_pipeline() -> None:
    """最便宜的一条端到端：离屏出图必须真的能出。

    为什么单独放在不碰前台的那一轮里：`--panel-shot` 是整条 WPF 管线
    （STA 线程、资源字典、程序集里的图片资源、RenderTargetBitmap）最短的一次全程走通，
    而它过去只在 `--gui` 段里被覆盖 —— 于是改名那次 `[STAThread]` 被插到别的方法头上、
    `Main` 变成 MTA、`--panel-shot` 直接抛 InvalidOperationException，
    不碰前台的 96 条照样全绿。那条红要等到有人去跑十几分钟的 GUI 才看得见。
    这一条两三秒，所以默认那一轮就该带上。
    """
    print("\n== 离屏出图管线（不弹窗、不抢前台） ==")
    if not os.path.isfile(BAR_EXE):
        check("编译产物存在", False, BAR_EXE)
        return
    p = run_shot("overview", "-")
    if p is None:
        check("--panel-shot overview 能出图", False, "120 秒没返回")
        return
    head = (p.stdout or "").strip().splitlines()
    ok = p.returncode == 0 and head and head[0].startswith("SHOT overview ")
    check("--panel-shot overview 退出码 0 且回了一行 SHOT（WPF/STA/资源管线全程走通）",
          ok, f"退出码 {p.returncode} 输出 {(head[0] if head else '')[:80]!r} "
              f"err={(p.stderr or '')[-160:]!r}")

    app = os.path.join(HERE, "bar", "App.cs")
    if os.path.isfile(app):
        lines = open(app, encoding="utf-8-sig", errors="replace").read().splitlines()
        mi = next((i for i, l in enumerate(lines) if "static int Main(" in l), -1)
        check("[STAThread] 紧贴 Main（被别的方法截走的话 Main 就变成 MTA，WPF 当场起不来）",
              mi > 0 and "[STAThread]" in lines[mi - 1],
              f"Main 在第 {mi + 1} 行，它上一行是 {lines[mi - 1].strip()[:40]!r}"
              if mi > 0 else "没找到 Main")


def check_brand_palette() -> None:
    """品牌视觉的两条底线：不许有紫色、材质必须真是三档可切。

    为什么钉「结果」而不是「过程」：紫色是用户明确否决的东西，而它有一百种回来的路
    —— 新加一个状态、换个主题刷、有人嫌橙不好看偷偷调回去。逐条钉「某个常数是几号」
    挡不住这些，只有把**源码里出现的每一个颜色字面量**都过一遍色相才挡得住。
    同理材质：三档不是注释里说说，是 Settings 的合法值、外观页的选择器、
    App 里的落点三处都得在，少一处就退化成「只有一档」。
    """
    print("\n== 品牌配色（无紫 / 三档材质） ==")
    files = [os.path.join(HERE, "dsh_state.py")]
    for root, dirs, names in os.walk(os.path.join(HERE, "bar")):
        dirs[:] = [d for d in dirs if d not in ("bin", "obj")]
        files += [os.path.join(root, n) for n in names if n.endswith(".cs")]

    viol: list[str] = []
    seen = 0
    for path in files:
        try:
            raw = open(path, encoding="utf-8-sig", errors="replace").read()
        except OSError:
            continue
        ext = path.rsplit(".", 1)[-1].lower()
        if ext == "cs":
            text = strip_cs_comments(raw)
        else:
            text = "\n".join(l.split("#")[0] for l in raw.splitlines())
        hits = []
        for m in re.finditer(r"#([0-9A-Fa-f]{6})\b", text):
            hits.append(tuple(int(x, 16) for x in
                              (m.group(1)[0:2], m.group(1)[2:4], m.group(1)[4:6])))
        for m in re.finditer(r"0x[0-9A-Fa-f]{2}([0-9A-Fa-f]{6})u?", text):
            hits.append(tuple(int(x, 16) for x in
                              (m.group(1)[0:2], m.group(1)[2:4], m.group(1)[4:6])))
        for m in re.finditer(r"From(?:Rgb|Argb)\(([^)]*)\)", text):
            # 直接抓括号里的参数再解析：上一版把第一个分量写成了非捕获组，
            # FromRgb(124, 58, 237) 只剩两个数可判 —— 反证当场把它抓出来了。
            vals = []
            for x in [t.strip() for t in m.group(1).split(",") if t.strip()]:
                try:
                    vals.append(int(x, 16) if x.lower().startswith("0x") else int(x))
                except ValueError:
                    vals = []
                    break
            if len(vals) == 4:
                vals = vals[1:]        # FromArgb(a, r, g, b)
            if len(vals) == 3:
                hits.append(tuple(vals))
        for r0, g0, b0 in hits:
            seen += 1
            h = _hue(r0, g0, b0)
            if PURPLE_LO <= h <= PURPLE_HI:
                viol.append(f"{os.path.relpath(path, HERE)}: #{r0:02X}{g0:02X}{b0:02X} 色相 {h:.0f}°")
    check(f"全部源码里的颜色字面量都不落在紫色相 {PURPLE_LO:.0f}°~{PURPLE_HI:.0f}°"
          f"（共扫 {seen} 个）",
          not viol, "出现紫色相：" + " | ".join(viol[:6]))

    app_src = open(os.path.join(HERE, "bar", "App.cs"),
                      encoding="utf-8-sig", errors="replace").read()
    check("强调色被显式钉成品牌色（原生控件的选中点会跟系统主题色，本机那一个是紫）",
          "AccentFillColorPrimary" in app_src and "ApplyBrandAccent" in app_src,
          "App.cs 里没有 ApplyBrandAccent / 没覆盖 AccentFillColorPrimary")
    check("RadioButton 的模板被接管（WPF-UI 4.2 没有 RadioButton，原生那个不读强调色资源）",
          "ApplyFluentControls()" in app_src
          and "typeof(System.Windows.Controls.RadioButton)" in app_src,
          "没找到 ApplyFluentControls 或没把隐式样式挂到 RadioButton 类型上 —— "
          "只覆盖资源的话选中圈仍是系统紫")

    st = open(os.path.join(HERE, "bar", "Settings.cs"),
                 encoding="utf-8-sig", errors="replace").read()
    check("材质合法值恰是云母 / 毛玻璃 / 液态玻璃三档",
          all(k in st for k in ('"mica"', '"acrylic"', '"glass"'))
          and "BackdropKinds" in st and "SafeBackdrop" in st,
          "Settings.cs 里三档取值或 SafeBackdrop 不在了")
    ap = open(os.path.join(HERE, "bar", "Panel", "Pages", "AppearancePage.cs"),
                 encoding="utf-8-sig", errors="replace").read()
    check("外观页真的摆了三档材质选择器，且点下去会当场生效",
          ap.count("cfg.Backdrop") >= 3 and "App.Config.Backdrop" in ap
          and "界面材质" in ap,
          f"外观页里 cfg.Backdrop 出现 {ap.count('cfg.Backdrop')} 次（应为 3 档）")
    check("自绘玻璃档的卡片是半透明的（不半透明就等于没有液态玻璃这一档）",
          "GlassTintLight" in open(os.path.join(HERE, "bar", "Panel", "Ui.cs"),
                                      encoding="utf-8-sig", errors="replace").read(),
          "Ui.cs 里找不到玻璃档的半透明卡片画刷")


def check_balance_key_handling() -> None:
    """余额 Key 的形状门禁：弹窗已删干净、Key 只走 stdin、日志先脱敏、界面只出来源。

    为什么钉源码形状而不是行为：这一条要防的是**以后有人顺手改坏**，不是现在的缺陷。
    行为侧要验「Key 会不会泄漏到界面上」就得真存一把 Key、真开一次窗口、再把整棵控件树
    读一遍 —— 而本机 `balance.protected` 按口径必须不存在，测完还得还原；那条路
    check_key_storage() 已经在引擎侧走过了（假 Key + 落盘非明文 + 当场还原）。
    剩下这半边是 C# 侧的结构不变量，用行为表达反而更弱（一次绿证明不了一次不红）。
    会假红的改动：把 stdin 换成参数、把 RedactKey 从日志那行摘掉、给 PasswordBox 加一句
    「回填上次输入」—— 这三件正是本段要拦的。守不住的：在别的文件里新起一处明文落盘
    （第 ⑤ 条只扫 bar/ 目录，仓库根新加一个 KeyDialog2.cs 不在它射程内）。
    """
    print("\n== 余额 Key 处理口径 ==")
    app = os.path.join(HERE, "bar", "App.cs")
    page = os.path.join(HERE, "bar", "Panel", "Pages", "BalancePage.cs")
    if not (os.path.isfile(app) and os.path.isfile(page)):
        check("App.cs / BalancePage.cs 可读", False, f"{app} {page}")
        return
    app_src = open(app, encoding="utf-8-sig", errors="replace").read()
    page_src = open(page, encoding="utf-8-sig", errors="replace").read()

    # ① 独立弹窗通路已断干净：注释里提 KeyDialog 是历史说明，代码里再出现就是还有调用点
    #    （所以同样只看 `//` 之前那一段，否则以后一句「这里顶掉了 KeyDialog」就自己钉红）。
    callers = []
    for root, dirs, files in os.walk(os.path.join(HERE, "bar")):
        dirs[:] = [d for d in dirs if d not in ("bin", "obj")]
        for fn in files:
            if not fn.endswith(".cs"):
                continue
            p = os.path.join(root, fn)
            code = "\n".join(l.split("//")[0] for l in open(
                p, encoding="utf-8-sig", errors="replace"))
            if "KeyDialog." in code:
                callers.append(os.path.relpath(p, HERE))
    check("bar/KeyDialog.cs 已删除且全目录没有 `KeyDialog.` 调用点（同一功能不留两处入口）",
          not os.path.isfile(os.path.join(HERE, "bar", "KeyDialog.cs")) and not callers,
          f"仍在引用的文件：{' '.join(callers) or '无'}")

    # ② Key 只能从 stdin 进引擎。argv 不是秘密存放处：本机任何进程都能读到子进程命令行。
    gi = app_src.find("static void RunEngineVerb(")
    ge = app_src.find("static void FinishEngineVerb", gi) if gi >= 0 else -1
    verb_body = app_src[gi:ge] if gi >= 0 and ge > gi else ""
    arg_lines = [l.strip() for l in verb_body.splitlines() if "Arguments" in l]
    check("找到了 RunEngineVerb 的引擎调用站点", bool(verb_body) and bool(arg_lines),
          "App.cs 里没有 static void RunEngineVerb(（到 FinishEngineVerb 之间），"
          "或它没有 Arguments 赋值")
    check("引擎命令行里不含 Key（Arguments 只拼 python、脚本路径和 verb）",
          bool(arg_lines) and all("key" not in l.lower() for l in arg_lines),
          ("这些行里出现了 key：" + " / ".join(arg_lines)
           + "（把 Key 插进 argv = 本机任何进程读得到）"))
    check("Key 走 StandardInput.Write，且只在有 Key 时才重定向 stdin",
          "RedirectStandardInput = key != null" in verb_body
          and "proc.StandardInput.Write(key)" in verb_body,
          "没找到 RedirectStandardInput = key != null / StandardInput.Write(key)")
    check("stdin 与 stdout 都显式 UTF-8（否则非 ASCII 的 Key 会被本机码页改形后存进 DPAPI）",
          "StandardInputEncoding = new UTF8Encoding(false)" in verb_body
          and "StandardOutputEncoding = new UTF8Encoding(false)" in verb_body,
          "少了 StandardInputEncoding：写入侧默认按本机 ANSI 码页走")

    # ②b 这一路必须跑在后台线程上。KeyDialog 时代同步等只冻一扇模态窗，
    #     面板是常驻的：同步等会把状态栏、托盘和两个 DispatcherTimer 一起冻住。
    check("引擎子命令跑在后台线程上，回程 marshal 回**发起时抓住的那个** Dispatcher",
          "new System.Threading.Thread(" in verb_body
          and "back.BeginInvoke(new Action(" in verb_body
          and "_window.Dispatcher" in verb_body,
          "RunEngineVerb 里没找到后台线程，或回程不是回到发起时抓住的那个 Dispatcher"
          "（退出过程中 _window 会变 null，后台线程再读它就是读一个不确定的东西）")
    check("起线程失败时把进行中标记还回去（否则以后每次点击都回「还在跑」）",
          "Interlocked.CompareExchange(ref _verbBusy, 1, 0)" in verb_body
          and verb_body.count("Interlocked.Exchange(ref _verbBusy, 0)") == 2,
          "标记不是走 Interlocked，或只有 worker 内部那一处清位 —— worker.Start() 抛在"
          "外面的话这一位就永远挂着")
    # ②c 「先读完再等退出」是死的：ReadToEnd 要等子进程关掉 stdout（也就是退出）才返回，
    #     放在 WaitForExit(20000) 前面，那条超时和 Kill 永远轮不到 —— 挂死的子进程挂死宿主。
    ra, we = verb_body.find("ReadToEndAsync"), verb_body.find("WaitForExit(")
    check("输出用 ReadToEndAsync 发起，20 秒超时与 Kill 才真的成立（⑤）",
          0 <= ra < we and "proc.Kill(true)" in verb_body,
          f"ReadToEndAsync@{ra} WaitForExit@{we}：顺序反了就是同步阻塞读，超时那一支是死代码")

    # ③ 引擎那一路的输出**不保证**不含敏感串：HTTP 错误分支会把服务端返回体原样截 200
    #    字符贴进 error（dsh_state.py fetch_balance），所以落日志前必须过 RedactKey。
    #    判据是「每一处提到 output 的 Log 都得过 RedactKey」，不是「有一处过了」——
    #    后者在旁边再加一句 Log(output) 也照样绿。
    raw = [l.strip() for l in verb_body.splitlines()
           if re.search(r"^\s*(Log|Balloon)\(.*\boutput\b", l) and "RedactKey" not in l]
    check("引擎输出进 bar.log 的每一处都先过 RedactKey（Log 与 Balloon 同一个水槽）",
          not raw, "这些行把未脱敏的输出写进了 bar.log：" + " / ".join(raw))
    def method_body(src: str, start: int, limit: int = 6000) -> str:
        """从签名处往后按大括号配平截出方法体。

        原来这里是 `src[start:start + 1400]` 这样的定长窗口 —— RedactKey 加上超时那一支
        之后长出了窗口，压平那一道锚点被切到外面，门禁当场假红（是反证跑出来的，
        不是想出来的）。方法体长度不该由门禁去猜，配平才是对的。
        """
        k = src.find("{", start)
        if k < 0:
            return ""
        depth, i = 0, k
        while i < len(src) and i - start < limit:
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    return src[start:i + 1]
            i += 1
        return src[start:start + limit]

    rk_i = app_src.find("static string RedactKey(")
    rk = method_body(app_src, rk_i) if rk_i >= 0 else ""
    VERBATIM = 'text.Replace(key, "***")'
    TOLERANT = "Regex.Escape(key[i].ToString())"
    FLAT = r'@"\s+"'          # 锚在字面量上，不锚在整句调用上：见下面那条注释
    v_at = rk.index(VERBATIM) if VERBATIM in rk else -1
    t_at = rk.index(TOLERANT) if TOLERANT in rk else -1
    f_at = rk.index(FLAT) if FLAT in rk else -1
    # 「先压平再替换」是接不上的：\s+ 换成的是**一个空格**、不是删掉，
    # `sk-ab\ncd` 压平成 `sk-ab cd` 之后照样不含 `sk-abcd`。所以真正的兜底是
    # 字符间插 \s* 的那一遍容错匹配，压平只能放在最后当排版。
    # 锚点用字面量而不是整句调用：上一轮的反证暴露了后者能被「在前面再复制一道形状
    # 略不同的压平」绕过去；配 count == 1 一起钉 —— RedactKey 里只许出现一处 \s+。
    check("RedactKey 三道齐全、压平只有一道且排在最后",
          v_at >= 0 and t_at > v_at and f_at > t_at and rk.count(FLAT) == 1,
          f"原样@{v_at} 容错@{t_at} 压平@{f_at}、压平共 {rk.count(FLAT)} 道：" 
          "缺任一道或被提前，被折行的 Key 就能原样进 bar.log")

    # ④ 界面上只出「来源」，不出 Key 本体：PasswordBox 的值只被读去保存，从不写进任何文本。
    leak = [l.strip() for l in page_src.splitlines()
            if "_key.Password" in l and re.search(r"\.(Text|Content)\s*=", l)]
    check("PasswordBox 的值从不写进界面文本（Text/Content 里没有 _key.Password）",
          not leak, "这些行把 Key 画到界面上：" + " / ".join(leak))
    # 上一条只管得住「直接赋值」；先转进局部变量再拼进 Text 就绕过去了。
    # 所以钉得更死一点：跟着快照重画的那条路径（Refresh）里根本不许出现输入框 ——
    # 引擎帧里多出一个 key 字段也好、以后有人给 Balance 加个 Key 属性也好，
    # 这一页都没有把它画出来的通路。
    ri = page_src.find("public void Refresh(")
    check("Refresh 整段不引用输入框（快照重画路径上没有读 Password 的口子）",
          ri >= 0 and "_key" not in page_src[ri:],
          "Refresh 里出现了 _key：" + " / ".join(
              l.strip() for l in page_src[ri:].splitlines() if "_key" in l)[:160])
    check("输入框只在成功那两支里清空（全页 _key.Clear() 恰好 2 处）",
          page_src.count("_key.Password") >= 1 and page_src.count("_key.Clear()") == 2,
          f"_key.Password {page_src.count('_key.Password')} 次、"
          f"_key.Clear() {page_src.count('_key.Clear()')} 次（多于 2 就是有人加了一处"
          "无条件清空 —— 失败时那一下会把用户粘进来的 Key 吃掉）")
    # 「失败也当场清空」是最伤用户的那一支：Key 是从平台控制台复制来的，一失败就清空
    # 等于让人回去重新找一遍，而且界面看起来像保存成功了。所以钉成回调形状：
    # 只有 ok 为真那一条分支里才许出现 Clear。
    check("只有成功才清空输入框（两支都是 if (ok) { _key.Clear(); …}），且等引擎期间按钮禁用",
          "App.SaveBalanceKey(k, (ok, why) =>" in page_src
          and page_src.count("if (ok) { _key.Clear();") == 2
          and "Busy(false);" in page_src and "Busy(true);" in page_src,
          "没找到回调形式的 App.SaveBalanceKey(k, (ok, why) => …) / 两处 if (ok) { _key.Clear(); / "
          "Busy(false)…Busy(true) —— 同步调用或无条件 Clear 都是把一次失败变成一次丢 Key")
    # 失败必须留在**不会被自动清掉**的地方：余额那一行跟着快照走（默认 5 秒一帧），
    # 把失败写进它是会自己消失的；而托盘气泡在用户关掉通知时根本不弹。
    check("失败原因写在页面上、不会被下一帧冲掉（_op 只由回调写，Refresh 不碰）",
          ri >= 0 and "_op" not in page_src[ri:] and "ShowOp(" in page_src[:ri]
          and "Visibility.Collapsed" in page_src,
          "没有独立的 _op 那一行，或 Refresh 会覆盖它 —— 失败信息就只剩一条会自己消失的文本")
    check("余额那一行显示的是来源（dpapi / env:… / file:… / none），不是 Key",
          "b.Source" in page_src and "来源" in page_src,
          "BalancePage.Refresh 里没有 b.Source —— 用户就看不出环境变量有没有盖住已存的 Key")

    # ⑤ 加解密只在引擎那一侧：C# 里出现 balance.protected 就意味着有人在自己写 Key 文件。
    #    只看 `//` 之前的部分：这一路的注释本来就要解释「为什么 C# 不写那个文件」，
    #    把说明也算成引用就成了自己钉自己的假红。
    hits = []
    for root, dirs, files in os.walk(os.path.join(HERE, "bar")):
        dirs[:] = [d for d in dirs if d not in ("bin", "obj")]
        for fn in files:
            if not fn.endswith(".cs"):
                continue
            code = "\n".join(l.split("//")[0] for l in open(
                os.path.join(root, fn), encoding="utf-8-sig", errors="replace"))
            if "balance.protected" in code:
                hits.append(fn)
    check("C# 侧不碰 balance.protected（DPAPI 读写全在 dsh_state.py）", not hits,
          f"引用了该文件名的：{' '.join(hits)}")

    # ⑥ 托盘菜单那一项必须改指面板，而不是自己弹窗 —— 与 ① 配对：① 钉「没有调用点」，
    #    这条钉「入口还在、而且指向了余额页」（删掉菜单项也能过 ①，但那等于把功能删了）。
    check("托盘菜单「设置余额 Key…」指向面板余额页 OpenPanel(\"balance\")",
          re.search(r'"设置余额 Key…",\s*\(s,\s*e\)\s*=>\s*OpenPanel\("balance"\)',
                    app_src) is not None,
          "菜单项没找到，或它不再走 OpenPanel(\"balance\")")


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
        while time.time() - t1 < 25 and not (found := top_window("Vigil 控制台")):
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
              "Shell_TrayWnd 里已经没有 Vigil 子窗口")
        check("消费失败后面板仍可见", bool(top_window("Vigil 控制台")), "面板跟着没了")
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
        subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
        deadline = time.time() + 20
        while time.time() < deadline and (bar_processes() or python_pids() - before):
            time.sleep(1)
        if os.path.exists(req):
            os.remove(req)  # 放弃路径只在能删时才删；测试自己不留垃圾给下一轮
        check("消费失败测试后无残留",
              not bar_processes() and not (python_pids() - before) and not os.path.exists(req),
              f"Vigil={sorted(bar_processes())} python={sorted(python_pids() - before)} req={os.path.exists(req)}")


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
        if not check("竞态测试前面板不存在（开窗必然是冷构造）", not top_window("Vigil 控制台"),
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
            if not t_panel and top_window("Vigil 控制台"):
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
              "Shell_TrayWnd 里已经没有 Vigil 子窗口")
        check("竞态后面板可见", bool(top_window("Vigil 控制台")), "面板跟着没了")
    finally:
        subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
        deadline = time.time() + 20
        while time.time() < deadline and (bar_processes() or python_pids() - before):
            time.sleep(1)
        if os.path.exists(req):
            os.remove(req)
        check("竞态测试后无残留",
              not bar_processes() and not (python_pids() - before) and not os.path.exists(req),
              f"Vigil={sorted(bar_processes())} python={sorted(python_pids() - before)} req={os.path.exists(req)}")


def check_gui() -> None:
    print("\n== 状态栏 GUI ==")
    if not os.path.isfile(BAR_EXE):
        print("  （跳过：没有编译产物，先跑 --build）")
        return
    if bar_processes():
        check("启动前无残留实例", False, "已有 Vigil 在跑，先退出它再冒烟")
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
        check("任务栏里挂上了状态栏窗口", bool(hwnds), "Shell_TrayWnd 无 Vigil 子窗口")
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
        subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
        # 引擎靠 stdout 管道断裂退出，父进程死后还要一两秒才收尾，轮询而不是定死等待。
        left_pids = python_pids() - before
        deadline = time.time() + 20
        while left_pids and time.time() < deadline:
            time.sleep(1)
            left_pids = python_pids() - before
        check("退出后无引擎孤儿子进程", not left_pids, f"仍在跑 {sorted(left_pids)}")
        check("退出后 Vigil 已消失", not bar_processes(), str(bar_processes()))


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
        check_python_resolution()
    check_setup_contract()
    check_engine_copy()
    check_licenses()
    if full or "--setup" in args:
        check_setup()
        check_installer_e2e()
    if full or "--package" in args:
        check_package()
    # 纯读源码、无副作用，所以不放 --gui：默认那轮也要钉住「开窗委托体自带 try」
    # 和「请求文件在开窗之后才删」这两条（外部没法让 PanelWindow 构造必然抛，
    # 见 check_panel_request_guard 的注释）。
    check_panel_request_guard()
    check_balance_key_handling()
    check_shot_pipeline()
    check_brand_palette()
    check_brand_pixels()
    if full or "--engine" in args:
        check_engine_parity()
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
        # Task 8：概览页填实的三面（源码 / 离屏两张定帧 / 真窗口多帧）。
        # 它同样要借产物目录那份引擎拷贝注入定死的帧，所以排在 check_gui 之前。
        check_overview_page()
        # 通知页/运行页的离屏面：同样要借这份引擎拷贝注入「定死的一帧」，所以也排在 check_gui 前。
        check_pages_filled()
        # records:null 的帧要在**状态栏**上验（静默冻结是这条缺陷的表现，不是红），
        # 同样只碰那份引擎拷贝，还原完才轮到 check_gui。
        check_bar_null_records()
        # 真实窗口那一侧的「有限高度」也借这份拷贝注入 29 行假引擎，
        # 所以同样排在 check_gui 之前（check_gui 要拿真引擎起状态栏）。
        # 滚动那两条（应用自己滚 → 比两张定帧）排在真窗口段前面：同一个注入形状，
        # 先量「会不会滚、表头动没动」，再量「外壳给的是不是有限高度」。
        check_panel_scroll_selftest()
        check_panel_real_scroll()
        check_gui()
        check_panel_window()
        # 「请求了哪一页 == 画了哪一页」的正证据段（真窗口比离屏参照）。
        # 它和 check_panel_real_scroll 一样借引擎拷贝注入固定 29 行，所以排在 check_settings_roundtrip
        # 之前：那两段都会改写 settings.json，比样必须在 settings 未被动的状态下量。
        check_panel_direct_page()
        # theme/showBar 的落盘往返 + 「主题真的改像素」的取样门禁（Task 5 Step 0 #1，补 N3 缺口）。
        # 这两段都会改写 settings.json 并在 finally 里按备份还原，串行跑互不污染。
        check_settings_roundtrip()
        # 面板里改的东西到底生效没有（UIA 驱动真窗口：不弹通知 / 重复提醒 / 重启引擎 /
        # 写注册表 / 开目录 / 整页滚动）。它也会改写 settings.json，排在 showBar 那段之前一起走
        # 「各自备份、各自还原」的串行口径。
        check_panel_settings_live()
        check_showbar_hidden()
        check_panel_cold_open()
        check_panel_request_containment()
        check_panel_request_race()
        # 这段会 taskkill /f /im Vigil.exe 全量收进程，放在最后，别打乱前面那些
        # 对"当前注入的是哪一帧"敏感的比样门禁。
        check_panel_backdrop()
        check_about_page()
        check_about_v5()
        check_balance_detail()
        check_runtime_row()
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
