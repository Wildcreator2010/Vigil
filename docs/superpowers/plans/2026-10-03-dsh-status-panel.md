# dsh-status 控制台面板 + 开源合规 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 dsh-status 增加一个 AF-Media-Bar 风格的桌面控制台窗口（实时状态 + 全部会话 + 设置），并把项目自身与全部第三方（含传递依赖）的许可证写清楚。

**Architecture:** 面板是 DshBar.exe 同进程内的第二扇窗口，复用已有的 `StateClient`（常驻 `python dsh_state.py --watch` 子进程）与 `Settings` 内存单例，零 IPC。唯一后端改动是让引擎快照多吐一个 `sessions` 数组。窗口外壳用 XAML + WPF-UI 的 `FluentWindow`，6 个页面全部用 C# 构建（与仓库现有 `BarWindow.cs` 的全代码风格一致）。

**Tech Stack:** C# / .NET 10 WPF + WinForms、WPF-UI 4.2.0（MIT）、Python 3.14 标准库、无 pytest（用仓库自有的断言脚本）。

**Spec:** `docs/superpowers/specs/2026-10-03-dsh-status-panel-design.md`

## Global Constraints

- 目标框架 `net10.0-windows`，`UseWPF` + `UseWindowsForms`，`Nullable=disable`，`ImplicitUsings=disable` —— 所有 C# 文件必须显式 `using`。
- 引擎侧**只用 Python 3.14 标准库**，不得引入第三方包。
- 编译门禁：`dotnet build -c Release` 必须 **0 error 0 warning**。跑构建时不要把输出接管道（`| tail` 会吞掉退出码），先重定向到文件再判 `$?`。
- 面板文案全中文；面板**默认浅色主题（白底黑字）**，任务栏状态条继续跟随系统深浅色，两者互不影响。
- C# 里 `Wpf.Ui.Controls` 与 `System.Windows.Controls` 有同名 `TextBlock`/`Button`，**code-behind 一律 `using WpfControls = System.Windows.Controls;` 起别名**，只从 `Wpf.Ui.Controls` 显式 import 用到的类型，否则 CS0104 二义性。
- `Wpf.Ui.Controls.ToggleSwitch` 继承 `ToggleButton`，状态属性是 **`IsChecked`（`bool?`）**，不存在 `IsOn`。
- 主题 API：`ApplicationThemeManager.Apply(ApplicationTheme, WindowBackdropType, bool updateAccent)`；枚举实际取值 `ApplicationTheme { Unknown, Dark, Light, HighContrast }`、`WindowBackdropType { None, Auto, Mica, Acrylic, Tabbed }`。
- XAML 里 WPF-UI 的命名空间是 `xmlns:ui="http://schemas.lepo.co/wpfui/2022/xaml"`（已实测可编译）。
- **不要用 `PrintWindow` 验证面板**：实测对 `FluentWindow` 返回内容全白的废图。面板渲染一律走进程内 `RenderTargetBitmap`（`--panel-shot`）。`PrintWindow` 只用于分层的状态栏窗口。
- 测试跑法：`python test_dsh_state.py`（引擎单元/端到端）、`python smoke_test.py --all`（全量冒烟）。两者退出码 0 才算过。
- 本仓库刚 `git init`，**提交者身份尚未配置**；第一个任务之前必须先拿到用户提供的邮箱并只写入仓库级 `.git/config`（不动全局配置）。
- 每个任务结束时必须：跑该任务的验证命令并确认真的通过，再 `git commit`。不允许"应该可以了"。

---

### Task 1: 开源合规基线

**Files:**
- Create: `LICENSE`
- Create: `THIRD-PARTY-NOTICES.md`
- Modify: `bar/DshBar.csproj`
- Modify: `README.md`（删「编译不需要联网」；新增「开源协议」「致谢」；文件清单补两行）
- Test: `smoke_test.py`（新增 `check_licenses()`）

**Interfaces:**
- Produces: `smoke_test.py` 里的 `check_licenses()`，被后续任务的 `--all` 全量回归复用。

- [ ] **Step 1: 先加失败的合规断言**

在 `smoke_test.py` 的 `check_engine_copy()` 之后插入：

```python
LICENSE_MUST_CONTAIN = (
    "MIT", "Wildcreator",                      # 本项目自身
    "lepoco", "Leszek Pomianowski",            # WPF-UI
    "AmorFate", "AF-Media-Bar",                # 设计参照
    "Bäumlisberger",                           # VirtualizingWrapPanel（WPF-UI 传递依赖）
    "fluentui-system-icons", "microsoft-ui-xaml", "dotnet/wpf",
    "Segoe Fluent Icons",
)


def check_licenses() -> None:
    print("\n== 开源合规 ==")
    root = HERE
    lic = os.path.join(root, "LICENSE")
    tpn = os.path.join(root, "THIRD-PARTY-NOTICES.md")
    check("LICENSE 存在", os.path.isfile(lic), lic)
    check("THIRD-PARTY-NOTICES.md 存在", os.path.isfile(tpn), tpn)
    blob = ""
    for p in (lic, tpn):
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as fh:
                blob += fh.read()
    missing = [k for k in LICENSE_MUST_CONTAIN if k not in blob]
    check("必要归属条目齐全", not missing, f"缺 {missing}")
    with open(os.path.join(root, "README.md"), encoding="utf-8") as fh:
        readme = fh.read()
    check("README 不再声称编译不需要联网", "不需要联网" not in readme, "仍有「不需要联网」")
    check("README 有开源协议章节", "## 开源协议" in readme, "缺章节")
    csproj = open(os.path.join(root, "bar", "DshBar.csproj"), encoding="utf-8").read()
    check("csproj 声明了许可证元数据", "PackageLicenseExpression" in csproj and "Copyright" in csproj,
          "缺 PackageLicenseExpression / Copyright")
```

并在 `main()` 里 `check_engine_copy()` 之后、`check_gui()` 之前调用 `check_licenses()`。

- [ ] **Step 2: 跑断言，确认它失败**

Run: `python smoke_test.py`
Expected: 退出码非 0，失败项包含 `LICENSE 存在`、`THIRD-PARTY-NOTICES.md 存在`、`必要归属条目齐全`、`README 不再声称编译不需要联网`、`csproj 声明了许可证元数据`。

- [ ] **Step 3: 写 `LICENSE`**

MIT 全文，版权行：

```
MIT License

Copyright (c) 2026 Wildcreator

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
LIABILITY, WHETHER IN ANY ACTION ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

- [ ] **Step 4: 写 `THIRD-PARTY-NOTICES.md`**

按 spec §8 的顺序，每条给「用途 + 上游地址 + 许可证 + 版权行」，并附许可证原文。条目与依据（均已实测核对，不要凭记忆改写）：

1. **WPF-UI 4.2.0** — MIT，`Copyright (C) 2021-2025 Leszek Pomianowski and WPF UI Contributors`，https://github.com/lepoco/wpfui 。用途：控制台窗口的 Fluent 外壳与表单控件。依据：本机 NuGet 包内 `LICENSE.md` 与 `wpf-ui.4.2.0.nuspec` 的 `authors=lepo.co`、`license=MIT`。
2. WPF-UI 内含并需继续向下传递的 5 项（依据包内 `ThirdPartyNotices.txt`）：
   - `sbaeumlisberger/VirtualizingWrapPanel` 2.0.6 — MIT，Copyright (c) 2019 S. Bäumlisberger
   - `microsoft/fluentui-system-icons` 1.1.242 — MIT，Copyright (c) 2020 Microsoft Corporation
   - `dotnet/wpf` 8.0 — MIT，© Microsoft Corporation
   - `microsoft/microsoft-ui-xaml` 3.0 — MIT，© Microsoft Corporation
   - `microsoft/segoe-fluent-icons-font` 3.0 — **微软专有字体许可，非 MIT**。必须单列一段写明：本项目只引用系统已安装的 Segoe Fluent Icons 字体，**不随安装包分发字体文件**，使用受微软该字体自身许可条款约束。
3. **AF-Media-Bar** — MIT，Copyright (c) 2026 AmorFate，https://github.com/Fervent-Tempo/AF-Media-Bar 。声明为「任务栏停靠方式与控制台分组/行布局的**设计参照**，本项目未复制其任何源代码」。
4. **.NET 10 Windows Desktop Runtime** 与 **Python 3.14 标准库** —— 运行时依赖，不随附其代码；写明版本下限（Python 低于 3.14 时引擎会因 `compression.zstd` 缺失而不可用）。

- [ ] **Step 5: 补 csproj 元数据并改 README**

`bar/DshBar.csproj` 的 `<PropertyGroup>` 内追加（`Version` 保持 `1.0.0`，它是版本号唯一来源）：

```xml
    <AssemblyTitle>dsh 任务栏状态栏</AssemblyTitle>
    <Product>dsh-status</Product>
    <Description>DeepSeek Harness Desktop 任务栏状态检测与控制台</Description>
    <Authors>Wildcreator</Authors>
    <Copyright>Copyright (c) 2026 Wildcreator</Copyright>
    <RepositoryUrl>https://github.com/Wildcreator2010/dsh-status</RepositoryUrl>
    <PackageLicenseExpression>MIT</PackageLicenseExpression>
```

README 三处改动：① 删掉实现栈那行里的「编译不需要联网」，改为「首次编译需要联网还原 NuGet 包」；② 文件清单表加 `LICENSE`、`THIRD-PARTY-NOTICES.md` 两行；③ 末尾新增 `## 开源协议`（指向 `LICENSE`，一句话：本项目 MIT）与 `## 致谢`（列 AF-Media-Bar、WPF-UI，各带链接，并指向 `THIRD-PARTY-NOTICES.md`）。

- [ ] **Step 6: 跑断言确认通过**

Run: `python smoke_test.py`
Expected: 退出码 0，`== 开源合规 ==` 段 6 项全 ✓。

Run: `cd bar && dotnet build -c Release --nologo > ../b.log 2>&1; echo $?; cat ../b.log`
Expected: `0`，且 `0 个警告 0 个错误`。

- [ ] **Step 7: 提交**

```bash
git add LICENSE THIRD-PARTY-NOTICES.md bar/DshBar.csproj README.md smoke_test.py
git commit -m "docs: 补齐 MIT 许可证与第三方声明（含 WPF-UI 传递依赖）"
```

---

### Task 2: 引擎 `sessions` 契约

**Files:**
- Modify: `dsh_state.py`（新增 `SESSIONS_IN_SNAPSHOT`、`_session_rows()`，`snapshot()` 返回值加 `sessions`）
- Modify: `test_dsh_state.py`（新增 `run_sessions_field()`）
- Test: `test_dsh_state.py`、`smoke_test.py`

**Interfaces:**
- Consumes: `scan()` 已返回的 rows（含 `path/key/project/title/state/turn/step/age_sec/last_event/last_tool/end_reason/records/todo/usage_total/pending`）。
- Produces: 快照 JSON 新增顶层 `"sessions": [...]`，每项字段与类型见 Step 3 的实现；上限 60 条，按 mtime 倒序。`recent` / `waiting` 字段**保持不变**。C# 侧 Task 3 之后按此形状反序列化。

- [ ] **Step 1: 写失败的引擎断言**

`test_dsh_state.py` 中，在 `run_e2e()` 之后加：

```python
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
            snap = ds.snapshot(g, ts, want_balance=False)
            rows = snap.get("sessions")
            if not isinstance(rows, list):
                errs.append(f"sessions 缺失或类型错: {type(rows)}")
                return errs
            if len(rows) != ds.SESSIONS_IN_SNAPSHOT:
                errs.append(f"sessions 未截到上限: {len(rows)}/{ds.SESSIONS_IN_SNAPSHOT}")
            need = {"key", "project", "title", "path", "state", "turn", "step", "age_sec",
                    "last_event", "last_tool", "end_reason", "records", "todo",
                    "usage_total", "pending"}
            for r in rows[:3]:
                miss = need - set(r)
                if miss:
                    errs.append(f"sessions 字段缺失: {sorted(miss)}")
            long_titles = [r["title"] for r in rows if r.get("title")]
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
```

并在 `main()` 里 `errs += e2e` 之后加：

```python
    se = run_sessions_field()
    if not se:
        print("✓ sessions 契约（上限/字段/截断/排序/无回归）通过")
    errs += se
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python test_dsh_state.py`
Expected: 退出码 1，报 `sessions 缺失或类型错: <class 'NoneType'>`。

- [ ] **Step 3: 实现**

`dsh_state.py` 顶部常量区（`BALANCE_TTL = 300.0` 之后）加：

```python
SESSIONS_IN_SNAPSHOT = 60
```

在 `pick_primary()` 之后加：

```python
def _session_rows(rows: list[dict], cap: int = SESSIONS_IN_SNAPSHOT) -> list[dict]:
    """把 scan() 的结果压成面板会话表要用的定长行：字段白名单 + 字符串截断，
    保证 --watch 每帧大小可控。"""
    out = []
    for s in rows[:cap]:
        pending = s.get("pending")
        out.append({
            "key": s["key"],
            "project": s["project"],
            "title": (s.get("title") or "")[:80] or None,
            "path": s["path"],
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
            },
        })
    return out
```

`snapshot()` 的返回字典里，`"recent": [...]` 之后加一行：

```python
        "sessions": _session_rows(sessions),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python test_dsh_state.py`
Expected: 退出码 0，含 `✓ sessions 契约（上限/字段/截断/排序/无回归）通过`。

Run: `python test_dsh_state.py --live`
Expected: 退出码 0；`真实会话 N 个` 那行仍正常，且缓存单轮扫描仍 < 120ms。

- [ ] **Step 5: 给 smoke_test 加契约断言**

`smoke_test.py` 的 `check_cli()` 里，`if ok:` 块内追加：

```python
        rows = snap.get("sessions")
        check("sessions 数组存在且不超上限",
              isinstance(rows, list) and 0 < len(rows) <= ds.SESSIONS_IN_SNAPSHOT,
              f"{type(rows).__name__} len={len(rows) if isinstance(rows, list) else '-'}")
        if rows:
            need = {"key", "project", "title", "path", "state", "turn", "step", "age_sec",
                    "last_event", "last_tool", "end_reason", "records", "todo",
                    "usage_total", "pending"}
            miss = need - set(rows[0])
            check("sessions 行字段齐全", not miss, f"缺 {sorted(miss)}")
            check("sessions 状态码全部合法",
                  all(r["state"] in ds.STATES for r in rows),
                  str({r["state"] for r in rows} - set(ds.STATES)))
```

- [ ] **Step 6: 跑全量冒烟**

Run: `python smoke_test.py`
Expected: 退出码 0（原 64 项 + 新增合规 6 项 + sessions 3 项全 ✓）。

- [ ] **Step 7: 提交**

```bash
git add dsh_state.py test_dsh_state.py smoke_test.py
git commit -m "feat(engine): 快照输出 sessions 数组供控制台会话表使用"
```

---

### Task 3: 引入 WPF-UI + 面板外壳可开可关

**Files:**
- Modify: `bar/DshBar.csproj`（加 `PackageReference`）
- Create: `bar/Panel/PanelWindow.xaml`
- Create: `bar/Panel/PanelWindow.xaml.cs`
- Modify: `bar/App.cs`（解析 `--panel`、`--panel-shot`；托盘与右键入口；`internal` 门面）
- Modify: `bar/Settings.cs`（`theme` / `showBar`）
- Test: `smoke_test.py`（`check_panel_shell()`）

**Interfaces:**
- Consumes: `StateClient.Snapshot`（Task 4 扩 `Sessions`）、`Settings`。
- Produces:
  - `internal sealed class PanelWindow : Wpf.Ui.Controls.FluentWindow`，方法 `void Open(string pageKey)`、`void ShowOn(string pageKey)`；属性 `static PanelWindow Instance { get; }`（打开过才非空）。
  - `App` 上的 internal 门面：`internal static Settings Config`、`internal static Snapshot Latest`、`internal static event Action SnapshotChanged`、`internal static void RestartEngine()`、`internal static void RequestBalanceRefresh()`、`internal static bool AutostartOn()`、`internal static void SetAutostart(bool)`、`internal static string LogPath`、`internal static string DataDir`、`internal static void OpenInExplorer(string path)`。后续页面只依赖这些。
  - 命令行：`--panel [page]`、`--panel-shot <page> <out.png>`。

- [ ] **Step 1: 加失败的面板冒烟断言**

`smoke_test.py` 里 `check_gui()` 之前加：

```python
PANEL_PAGES = ("overview", "notify", "appearance", "runtime", "balance", "about")


def check_panel_shell() -> None:
    print("\n== 控制台面板 ==")
    if not os.path.isfile(BAR_EXE):
        print("  （跳过：没有编译产物）")
        return
    if bar_processes():
        check("启动前无残留实例", False, "已有 DshBar 在跑")
        return
    shots = {}
    for page in PANEL_PAGES:
        shot = os.path.join(ds.state_dir(), f"panel-{page}.png")
        if os.path.isfile(shot):
            os.remove(shot)
        p = subprocess.run([BAR_EXE, "--panel-shot", page, shot],
                           capture_output=True, text=True, errors="replace",
                           timeout=120, cwd=os.path.dirname(BAR_EXE))
        line = (p.stdout or "").strip().splitlines()
        head = line[0] if line else ""
        # C# 侧输出：SHOT <page> <w> <h> <colors>
        parts = head.split()
        good = (p.returncode == 0 and len(parts) == 5 and parts[0] == "SHOT"
                and parts[1] == page and int(parts[2]) >= 720 and int(parts[3]) >= 480)
        check(f"--panel-shot {page}", good,
              f"退出码 {p.returncode} 输出 {head!r} err={(p.stderr or '')[-160:]!r}")
        check(f"{page} 落盘 PNG", os.path.isfile(shot) and os.path.getsize(shot) > 2000, shot)
        shots[page] = int(parts[4]) if len(parts) == 5 else -1
```

本任务**不**在这里断言颜色种类 > 200：此时 6 页还都是 `PageBase` 占位（一个 TextBlock，
两三种颜色），那条门禁归各自填实该页的任务（Task 5~9）。这里只验「能离屏出图、尺寸对、落了盘」。


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
        u.PostMessage(found, 0x0010, 0, 0)  # WM_CLOSE
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
```

`smoke_test.py` 里补两个小工具（放在 `find_bar_windows` 附近）：

```python
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
```

`main()` 里在 `check_gui()` 之前调 `check_panel_shell()`，之后调 `check_panel_window()`（都受 `--gui`/`--all` 门控）。

- [ ] **Step 2: 跑断言确认失败**

Run: `python smoke_test.py --gui`
Expected: 失败，`--panel-shot overview` 报退出码非 0 或输出为空（参数还不认识）。

- [ ] **Step 3: csproj 加包引用**

`bar/DshBar.csproj` 末尾 `</Project>` 前加：

```xml
  <ItemGroup>
    <PackageReference Include="WPF-UI" Version="4.2.0" />
  </ItemGroup>
```

Run: `cd bar && dotnet restore --nologo > ../r.log 2>&1; echo $?`
Expected: `0`。

- [ ] **Step 4: `Settings.cs` 加两个字段**

```csharp
        [JsonPropertyName("theme")] public string Theme { get; set; } = "light";
        [JsonPropertyName("showBar")] public bool ShowBar { get; set; } = true;
```

`Load` 里 `s.Interval = Math.Max(1, s.Interval);` 之后加：

```csharp
                        if (s.Theme != "light" && s.Theme != "dark" && s.Theme != "system")
                            s.Theme = "light";
```

- [ ] **Step 5: 写 `bar/Panel/PanelWindow.xaml`**

```xml
<ui:FluentWindow x:Class="DshBar.PanelWindow"
        xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        xmlns:ui="http://schemas.lepo.co/wpfui/2022/xaml"
        Title="dsh 控制台"
        Width="960" Height="640"
        MinWidth="760" MinHeight="500"
        WindowStartupLocation="CenterScreen"
        ShowInTaskbar="True"
        Background="#F3F3F3">
  <Grid>
    <Grid.ColumnDefinitions>
      <ColumnDefinition Width="188" />
      <ColumnDefinition Width="*" />
    </Grid.ColumnDefinitions>
    <Border Grid.Column="0" Background="White" BorderBrush="#E2E5EA" BorderThickness="0,0,1,0">
      <StackPanel Margin="10,14,10,0">
        <TextBlock Text="dsh 控制台" FontSize="17" FontWeight="SemiBold"
                   Foreground="#1A1D23" Margin="4,0,0,12" />
        <ListBox x:Name="Nav" BorderThickness="0" Background="Transparent"
                 SelectionChanged="OnNavChanged" />
      </StackPanel>
    </Border>
    <ScrollViewer Grid.Column="1" VerticalScrollBarVisibility="Auto" Padding="24,20,24,20">
      <ContentControl x:Name="Host" />
    </ScrollViewer>
  </Grid>
</ui:FluentWindow>
```

- [ ] **Step 6: 写 `bar/Panel/PanelWindow.xaml.cs`**

```csharp
using System;
using System.Collections.Generic;
using WpfControls = System.Windows.Controls;

namespace DshBar
{
    internal sealed partial class PanelWindow : Wpf.Ui.Controls.FluentWindow
    {
        static readonly string[] Keys = { "overview", "notify", "appearance", "runtime", "balance", "about" };
        static readonly string[] Labels = { "概览", "通知", "外观", "运行", "余额", "关于" };

        readonly Dictionary<string, WpfControls.FrameworkElement> _pages =
            new Dictionary<string, WpfControls.FrameworkElement>();
        bool _navigating;
        bool _reallyClosing;

        public static PanelWindow Instance { get; private set; }

        public PanelWindow()
        {
            InitializeComponent();
            for (int i = 0; i < Keys.Length; i++)
                Nav.Items.Add(new WpfControls.ListBoxItem { Content = Labels[i], Tag = Keys[i] });
            Nav.SelectedIndex = 0;
            Instance = this;
            App.SnapshotChanged += OnSnapshotChanged;
            // 关窗只是藏起来：视觉树留着，下次打开不重建；真正销毁只在退出时
            Closing += (s, e) =>
            {
                if (!_reallyClosing) { e.Cancel = true; Hide(); }
            };
        }

        /// <summary>只在 App.Cleanup 里调用，绕开上面的拦截。</summary>
        internal void ForceClose()
        {
            _reallyClosing = true;
            App.SnapshotChanged -= OnSnapshotChanged;
            if (Instance == this) Instance = null;
            Close();
        }

        void OnSnapshotChanged() => Dispatcher.Invoke(() =>
        {
            if (Host.Content is IPanelPage p) p.Refresh(App.Latest);
        });

        public void ShowOn(string key)
        {
            Navigate(key ?? "overview");
            if (!IsVisible) Show();
            Activate();
        }

        void Navigate(string key)
        {
            int idx = Array.IndexOf(Keys, key);
            if (idx < 0) idx = 0;
            if (Nav.SelectedIndex == idx) { Render(idx); return; }
            _navigating = true;
            Nav.SelectedIndex = idx;
            _navigating = false;
        }

        void OnNavChanged(object sender, WpfControls.SelectionChangedEventArgs e)
        {
            if (!_navigating && Nav.SelectedIndex >= 0) Render(Nav.SelectedIndex);
        }

        void Render(int index)
        {
            string key = Keys[index];
            if (!_pages.TryGetValue(key, out var page))
            {
                page = Build(key);
                _pages[key] = page;
            }
            Host.Content = page;
            if (page is IPanelPage p) p.Refresh(App.Latest);
        }

        static WpfControls.FrameworkElement Build(string key)
        {
            switch (key)
            {
                case "notify": return new NotifyPage();
                case "appearance": return new AppearancePage();
                case "runtime": return new RuntimePage();
                case "balance": return new BalancePage();
                case "about": return new AboutPage();
                default: return new OverviewPage();
            }
        }
    }

    internal interface IPanelPage
    {
        void Refresh(Snapshot snap);
    }
}
```

Task 5~8 会给出这 6 个 page 类；本步先各写一个最小占位类，让编译过：

```csharp
using System.Windows;
using System.Windows.Media;
using WpfControls = System.Windows.Controls;

namespace DshBar
{
    /// <summary>控制台里的一页。Task 5~8 填实，本文件只保证可编译可渲染。</summary>
    internal class PageBase : WpfControls.ContentControl, IPanelPage
    {
        protected PageBase(string title)
        {
            Background = Brushes.Transparent;
            Content = new WpfControls.TextBlock
            {
                Text = title,
                FontSize = 16,
                Foreground = new SolidColorBrush(Color.FromRgb(0x1A, 0x1D, 0x23)),
            };
        }

        public virtual void Refresh(Snapshot snap) { }
    }

    internal sealed class OverviewPage : PageBase { public OverviewPage() : base("概览") { } }
    internal sealed class NotifyPage : PageBase { public NotifyPage() : base("通知") { } }
    internal sealed class AppearancePage : PageBase { public AppearancePage() : base("外观") { } }
    internal sealed class RuntimePage : PageBase { public RuntimePage() : base("运行") { } }
    internal sealed class BalancePage : PageBase { public BalancePage() : base("余额") { } }
    internal sealed class AboutPage : PageBase { public AboutPage() : base("关于") { } }
}
```

存为 `bar/Panel/Pages/Pages.cs`（Task 5~8 会把它拆成每页一个文件）。

- [ ] **Step 7: `App.cs` 加入口与门面**

`Main` 里现有的参数循环：

```csharp
            string demo = null;
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (args[i] == "--demo") demo = args[i + 1];
            }
```

整段替换为（循环上界改成 `args.Length`，这样 `--panel` 作为最后一个参数、不带页名时也能认）：

```csharp
            string demo = null, panel = null, shotPage = null, shotOut = null;
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--demo" && i + 1 < args.Length) demo = args[++i];
                else if (args[i] == "--panel") panel = i + 1 < args.Length ? args[++i] : "";
                else if (args[i] == "--panel-shot" && i + 2 < args.Length)
                {
                    shotPage = args[++i];
                    shotOut = args[++i];
                }
            }
```

紧接着的日志行改成同时带上面板参数：

```csharp
            Log($"DshBar 启动 interval={_settings.Interval} notify={_settings.Notify} demo={demo ?? "-"} panel={panel ?? "-"}");
```

**在 `_mutex = new Mutex(...)` 之前**插入 shot 模式（它不开窗口、不抢单实例，状态栏正在跑时也能出图）：

```csharp
            if (shotPage != null) return RenderShot(shotPage, shotOut);
```

并在 `if (!owned)` 分支里，把弹框改成转交：

```csharp
            if (!owned)
            {
                RequestPanel(panel);
                return 0;
            }
```

`RenderShot` / `RequestPanel` / 门面成员加在 `App` 类内（`StartClient()` 之前）：

```csharp
        internal static Settings Config => _settings;
        internal static Snapshot Latest => _last;
        internal static event Action SnapshotChanged;
        internal static string LogPath => LogFile;
        internal static string DataDir => StateDir;
        internal static string PanelRequestFile => Path.Combine(StateDir, "panel.request");

        internal static void RestartEngine() => RestartClient();
        internal static void RequestBalanceRefresh() => RefreshBalance();
        internal static bool AutostartOn() => AutostartEnabled();
        internal static void SetAutostart(bool on) => ToggleAutostart(on);

        internal static void OpenInExplorer(string path)
        {
            try
            {
                System.Diagnostics.Process.Start(
                    new System.Diagnostics.ProcessStartInfo(path) { UseShellExecute = true });
            }
            catch (Exception ex) { Log($"打开路径失败 {path}: {ex.Message}"); }
        }

        static void RequestPanel(string page)
        {
            try { File.WriteAllText(PanelRequestFile, page ?? ""); }
            catch (Exception ex) { Log($"转交面板请求失败: {ex.Message}"); }
        }

        /// <summary>跑一次引擎拿真实快照，只给 --panel-shot 用（常驻路径走 StateClient）。</summary>
        static void LoadOneShotSnapshot()
        {
            try
            {
                string engine = Path.Combine(AppContext.BaseDirectory, "dsh_state.py");
                if (!File.Exists(engine))
                    engine = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "dsh_state.py"));
                string python = ResolvePython();
                if (python == null || !File.Exists(engine)) return;
                var psi = new System.Diagnostics.ProcessStartInfo
                {
                    FileName = python,
                    Arguments = $"-X utf8 \"{engine}\" --json --no-balance",
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    StandardOutputEncoding = new UTF8Encoding(false),
                };
                using var proc = System.Diagnostics.Process.Start(psi);
                string line = proc.StandardOutput.ReadLine();
                proc.WaitForExit(20000);
                if (string.IsNullOrWhiteSpace(line)) return;
                _last = System.Text.Json.JsonSerializer.Deserialize<Snapshot>(line,
                    new System.Text.Json.JsonSerializerOptions { PropertyNameCaseInsensitive = true });
            }
            catch (Exception ex)
            {
                Log($"shot 取快照失败: {ex.Message}");
            }
        }

        /// <summary>离屏渲染某一页为 PNG 并打印统计，供冒烟做像素门禁。</summary>
        static int RenderShot(string pageKey, string outPath)
        {
            try
            {
                // shot 模式在单实例检查之前就 return 了，_settings 还没加载；
                // 而每一页构造时都要读 App.Config，不先加载就是空引用。
                _settings = Settings.Load(SettingsFile);
                // 离屏出图也要喂真实快照，否则概览页永远是空表，Task 8 的门禁无从判断。
                LoadOneShotSnapshot();
                var app = new System.Windows.Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
                Wpf.Ui.Appearance.ApplicationThemeManager.Apply(
                    Wpf.Ui.Appearance.ApplicationTheme.Light,
                    Wpf.Ui.Appearance.WindowBackdropType.None, false);
                FrameworkElement root;
                switch (pageKey)
                {
                    case "notify": root = new NotifyPage(); break;
                    case "appearance": root = new AppearancePage(); break;
                    case "runtime": root = new RuntimePage(); break;
                    case "balance": root = new BalancePage(); break;
                    case "about": root = new AboutPage(); break;
                    default: root = new OverviewPage(); break;
                }
                double w = 900, h = 620;
                root.Measure(new Size(w, h));
                root.Arrange(new Rect(0, 0, w, h));
                root.UpdateLayout();
                var rtb = new RenderTargetBitmap((int)w, (int)h, 96, 96, PixelFormats.Pbgra32);
                rtb.Render(root);
                var pixels = new byte[(int)w * (int)h * 4];
                rtb.CopyPixels(pixels, (int)w * 4, 0);
                var seen = new HashSet<uint>();
                for (int i = 0; i + 3 < pixels.Length; i += 4)
                    seen.Add((uint)(pixels[i] << 16 | pixels[i + 1] << 8 | pixels[i + 2]));
                if (!string.IsNullOrEmpty(outPath) && outPath != "-")
                {
                    var enc = new System.Windows.Media.Imaging.PngBitmapEncoder();
                    enc.Frames.Add(System.Windows.Media.Imaging.BitmapFrame.Create(rtb));
                    using var fs = File.Create(outPath);
                    enc.Save(fs);
                }
                Console.Out.WriteLine($"SHOT {pageKey} {(int)w} {(int)h} {seen.Count}");
                Console.Out.Flush();
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"SHOT-FAIL {ex.GetType().Name}: {ex.Message}");
                return 1;
            }
        }
```

输出协议：`SHOT <page> <宽> <高> <颜色种类>`，冒烟只解析这一行，不需要在 Python 侧解码 PNG；
PNG 本身留给人看。传 `-` 作为输出路径表示"只要统计、不落盘"。

`App.cs` 顶部补 `using`（注意别名，避免 CS0104）：

```csharp
using System.Windows.Media;
using System.Windows.Media.Imaging;
```

（`System.Windows.Media` 已有；`Media.Imaging` 需新增。`RenderTargetBitmap` 在 `System.Windows.Media.Imaging`。）

`Tick()` 里加面板请求轮询（紧跟 `_tray.Text = ...` 之后）：

```csharp
                var req = PanelRequestFile;
                if (File.Exists(req))
                {
                    string page = "";
                    try { page = File.ReadAllText(req).Trim(); File.Delete(req); }
                    catch { }
                    _window.Dispatcher.BeginInvoke(new Action(() =>
                        PanelWindow.Instance?.ShowOn(string.IsNullOrEmpty(page) ? "overview" : page)));
                }
```

`BuildTray()` 里菜单第一项之前插入打开面板项：

```csharp
            Add(menu, "打开面板", (s, e) => OpenPanel("overview"));
            menu.Items.Add(new ToolStripSeparator());
```

并在 `App` 内加：

```csharp
        static void OpenPanel(string page)
        {
            if (_panel == null) _panel = new PanelWindow();
            _panel.ShowOn(page);
        }

        static PanelWindow _panel;
```

`Main` 里 `if (demo != null) DemoOnce(demo); else StartClient();` 之后加：

```csharp
            if (panel != null) OpenPanel(string.IsNullOrEmpty(panel) ? "overview" : panel);
```

`Cleanup()` 里 `_window?.Close();` 之前加：

```csharp
                try { _panel?.ForceClose(); } catch { }
```

- [ ] **Step 8: 编译**

Run: `cd bar && dotnet build -c Release --nologo > ../b.log 2>&1; echo $?; cat ../b.log`
Expected: `0`，`0 个警告 0 个错误`。若报 CS0104，说明某文件同时 `using` 了两个 `Controls` 命名空间，按 Global Constraints 起别名。

- [ ] **Step 9: 跑 shot 模式确认渲染真的成立**

Run: `cd bar/bin/Release/net10.0-windows && ./DshBar.exe --panel-shot overview shot-probe.png; echo EXIT=$?`
Expected: 输出形如 `SHOT overview 900 620 214`，`EXIT=0`，颜色种类 > 200。
若颜色种类 ≤ 200 或报 `SHOT-FAIL`，**停下**：说明离屏渲染没走通，不要继续后面的页面任务。

Run: `rm -f shot-probe.png`

- [ ] **Step 10: 跑面板冒烟并做全量回归**

Run: `python smoke_test.py --all`
Expected: 退出码 0；新增 `== 控制台面板 ==`（6 页 × 2 项）与 `== 控制台窗口 ==`（5 项）全 ✓；原 64 项仍全 ✓。

- [ ] **Step 11: 让状态栏抓图按窗口类型选 `PrintWindow` flag**

spec §9 要求：分层窗口用 `PW_RENDERFULLCONTENT`，普通窗口用 `0`，抓回纯色视为失败并换另一种。
现有 `smoke_test.py` 的 `capture_bar()` 把 flag 写死成 `2`，对 `BarWindow`（`WS_EX_LAYERED`）有效，
但同样是 `FluentWindow` 就会拿到废图。改成两种 flag 都试：

把 `capture_bar` 里这段：

```python
    try:
        if not u.PrintWindow(hwnd, mdc, 2):  # PW_RENDERFULLCONTENT
            return 0, 0, b""
        info = BI(s=ctypes.sizeof(BI), w=w, h=-h, pl=1, bc=32, comp=0, size=w * h * 4)
        buf = ctypes.create_string_buffer(w * h * 4)
        if not g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(info), 0):
            return 0, 0, b""
    finally:
        u.ReleaseDC(hwnd, wdc)
        g.DeleteDC(mdc)
        g.DeleteObject(bmp)
    bgra = buf.raw
```

换成：

```python
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
```

Run: `python smoke_test.py --gui`
Expected: 退出码 0，`PrintWindow 抓到状态栏画面` / `画面已合成出内容（非纯色）` /
`状态色 … 已画在圆点上` 三项仍 ✓（说明改了 flag 策略没把状态栏那条门禁改坏）。

- [ ] **Step 12: 提交**

```bash
git add bar/DshBar.csproj bar/Panel/ bar/App.cs bar/Settings.cs smoke_test.py
git commit -m "feat(panel): 引入 WPF-UI，加控制台外壳与离屏渲染门禁"
```

---

### Task 4: `Snapshot.Sessions` 打通到 C#

**Files:**
- Modify: `bar/StateClient.cs`（`Usage`、`SessionRow`、`Snapshot.Sessions`）
- Test: `smoke_test.py`

**Interfaces:**
- Consumes: Task 2 的 `"sessions"` JSON 形状。
- Produces: `public sealed class SessionRow`，属性 `Key/Project/Title/Path/State/Turn(int?)/Step(int?)/AgeSec/LastEvent/LastTool/EndReason/Records/Todo/Usage/Pending`；`public sealed class Usage { long Input, Output, Total }`；`Snapshot.Sessions` 为 `List<SessionRow>`。概览页依赖。

- [ ] **Step 1: 收紧概览页的门禁**

`shots` 字典已在 Task 3 的 `check_panel_shell()` 里采集好了。本任务在循环结束后加一条：

```python
    check("概览页已画出表格（不再是一行占位文字）", shots.get("overview", 0) > 200,
          f"colors={shots.get('overview')}")
```

「概览页比关于页更丰富」这条对比断言放到 Task 8（概览页真正填实的那个任务），放在这里是 premature——
本任务只把模型打通 + 表格骨架立起来。

- [ ] **Step 2: 跑，确认失败**

Run: `python smoke_test.py --gui`
Expected: `概览页已画出表格（不再是一行占位文字）` 失败（此时概览页仍是 `PageBase` 占位，颜色种类只有两三种）。

- [ ] **Step 3: 加反序列化模型**

`bar/StateClient.cs` 中 `Brief` 类之后加：

```csharp
    public sealed class Usage
    {
        [JsonPropertyName("input")] public long Input { get; set; }
        [JsonPropertyName("output")] public long Output { get; set; }
        [JsonPropertyName("total")] public long Total { get; set; }
    }

    public sealed class SessionRow
    {
        [JsonPropertyName("key")] public string Key { get; set; }
        [JsonPropertyName("project")] public string Project { get; set; }
        [JsonPropertyName("title")] public string Title { get; set; }
        [JsonPropertyName("path")] public string Path { get; set; }
        [JsonPropertyName("state")] public string State { get; set; }
        [JsonPropertyName("turn")] public int? Turn { get; set; }
        [JsonPropertyName("step")] public int? Step { get; set; }
        [JsonPropertyName("age_sec")] public double AgeSec { get; set; }
        [JsonPropertyName("last_event")] public string LastEvent { get; set; }
        [JsonPropertyName("last_tool")] public string LastTool { get; set; }
        [JsonPropertyName("end_reason")] public string EndReason { get; set; }
        [JsonPropertyName("records")] public int Records { get; set; }
        [JsonPropertyName("todo")] public Todo Todo { get; set; }
        [JsonPropertyName("usage_total")] public Usage Usage { get; set; }
        [JsonPropertyName("pending")] public Pending Pending { get; set; }
    }
```

`Snapshot` 类里 `Recent` 之后加：

```csharp
        [JsonPropertyName("sessions")] public List<SessionRow> Sessions { get; set; }
```

- [ ] **Step 4: 填概览页占位，让它真的用 Sessions**

`bar/Panel/Pages/Pages.cs` 里把 `OverviewPage` 换成：

```csharp
    internal sealed class OverviewPage : WpfControls.ContentControl, IPanelPage
    {
        readonly WpfControls.DataGrid _grid = new WpfControls.DataGrid
        {
            AutoGenerateColumns = false,
            IsReadOnly = true,
            HeadersVisibility = WpfControls.DataGridHeadersVisibility.Column,
            GridLinesVisibility = WpfControls.DataGridGridLinesVisibility.Horizontal,
            Background = Brushes.White,
            BorderBrush = new SolidColorBrush(Color.FromRgb(0xE2, 0xE5, 0xEA)),
            BorderThickness = new Thickness(1),
            MinHeight = 260,
        };

        public OverviewPage()
        {
            foreach (var c in new[]
            {
                ("项目", "Project", 150.0), ("状态", "State", 110.0),
                ("轮次", "Turn", 60.0), ("静默秒", "AgeSec", 80.0),
                ("最近工具", "LastTool", 130.0), ("记录数", "Records", 80.0),
            })
            {
                _grid.Columns.Add(new WpfControls.DataGridTextColumn
                {
                    Header = c.Item1,
                    Binding = new System.Windows.Data.Binding(c.Item2),
                    Width = new WpfControls.DataGridLength(c.Item3),
                });
            }
            Content = new WpfControls.StackPanel { Children = { _grid } };
        }

        public void Refresh(Snapshot snap) => _grid.ItemsSource = snap?.Sessions;
    }
```

`Pages.cs` 顶部补：

```csharp
using System.Windows.Media;
using WpfControls = System.Windows.Controls;
```

- [ ] **Step 5: 跑，确认通过**

Run: `cd bar && dotnet build -c Release --nologo > ../b.log 2>&1; echo $?`
Expected: `0`，0 警告。

Run: `python smoke_test.py --gui`
Expected: 退出码 0，`概览页已画出表格（不再是一行占位文字）` ✓。

- [ ] **Step 6: 提交**

```bash
git add bar/StateClient.cs bar/Panel/Pages/Pages.cs smoke_test.py
git commit -m "feat(panel): 快照会话行打通到概览页表格"
```

---

### Task 5: `Ui` 分组/行工厂 + 外观页 + 主题与状态条生效

**Files:**
- Create: `bar/Panel/Ui.cs`
- Create: `bar/Panel/Pages/AppearancePage.cs`
- Modify: `bar/Panel/Pages/Pages.cs`（删掉 `AppearancePage` 占位）
- Modify: `bar/App.cs`（应用主题、`showBar` 生效）
- Test: `smoke_test.py`

**Interfaces:**
- Consumes: `App.Config`、`App.OpenInExplorer`。
- Produces:
  - `internal static class Ui`，`static FrameworkElement Ui.Row(string title, string desc, FrameworkElement field)`、`static FrameworkElement Ui.Group(string title, params FrameworkElement[] rows)`、画刷 `Ui.Ink / InkDim / CardBg / CardLine / PageBg`。
  - `App.ApplyTheme()`（把 `Config.Theme` 落到 `ApplicationThemeManager`）。
  - `App.ApplyBarVisibility()`（`showBar` 决定停靠/隐藏）。
  - `PageBase(string title)` 仍可用。

- [ ] **Step 1: 写 `bar/Panel/Ui.cs`**

```csharp
using System.Windows;
using System.Windows.Media;
using WpfControls = System.Windows.Controls;

namespace DshBar
{
    /// <summary>控制台的分组建块：白卡片 + 分隔线，行是「标题/说明 + 右侧控件」。
    /// 对齐 AF-Media-Bar 的 SettingsGroup / SettingsRow 词汇，但全部自绘，
    /// 不用 WPF-UI 的 CardControl（它是 ButtonBase，整卡可点的语义不对）。</summary>
    internal static class Ui
    {
        public static readonly Brush Ink = Freeze(Color.FromRgb(0x1A, 0x1D, 0x23));
        public static readonly Brush InkDim = Freeze(Color.FromRgb(0x5A, 0x62, 0x70));
        public static readonly Brush CardBg = Freeze(Color.FromRgb(0xFF, 0xFF, 0xFF));
        public static readonly Brush CardLine = Freeze(Color.FromRgb(0xE2, 0xE5, 0xEA));
        public static readonly Brush PageBg = Freeze(Color.FromRgb(0xF3, 0xF3, 0xF3));

        static Brush Freeze(Color c)
        {
            var b = new SolidColorBrush(c);
            b.Freeze();
            return b;
        }

        public static FrameworkElement Row(string title, string desc, FrameworkElement field)
        {
            var grid = new WpfControls.Grid { Margin = new Thickness(14, 11, 14, 11) };
            grid.ColumnDefinitions.Add(new WpfControls.ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new WpfControls.ColumnDefinition { Width = GridLength.Auto });

            var text = new WpfControls.StackPanel { VerticalAlignment = VerticalAlignment.Center };
            text.Children.Add(new WpfControls.TextBlock
            {
                Text = title,
                FontSize = 13.5,
                FontWeight = FontWeights.SemiBold,
                Foreground = Ink,
            });
            if (!string.IsNullOrEmpty(desc))
                text.Children.Add(new WpfControls.TextBlock
                {
                    Text = desc,
                    FontSize = 12,
                    Foreground = InkDim,
                    Margin = new Thickness(0, 3, 0, 0),
                    TextWrapping = TextWrapping.Wrap,
                });
            grid.Children.Add(text);

            if (field != null)
            {
                field.VerticalAlignment = VerticalAlignment.Center;
                field.Margin = new Thickness(16, 0, 0, 0);
                WpfControls.Grid.SetColumn(field, 1);
                grid.Children.Add(field);
            }
            return grid;
        }

        public static FrameworkElement Group(string title, params FrameworkElement[] rows)
        {
            var body = new WpfControls.StackPanel();
            for (int i = 0; i < rows.Length; i++)
            {
                if (i > 0)
                    body.Children.Add(new WpfControls.Border
                    {
                        Height = 1,
                        Background = CardLine,
                        Margin = new Thickness(14, 0, 14, 0),
                    });
                body.Children.Add(rows[i]);
            }
            var card = new WpfControls.Border
            {
                Background = CardBg,
                BorderBrush = CardLine,
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(8),
                Child = body,
            };
            var outer = new WpfControls.StackPanel { Margin = new Thickness(0, 0, 0, 18) };
            if (!string.IsNullOrEmpty(title))
                outer.Children.Add(new WpfControls.TextBlock
                {
                    Text = title,
                    FontSize = 13,
                    FontWeight = FontWeights.SemiBold,
                    Foreground = Ink,
                    Margin = new Thickness(2, 0, 0, 7),
                });
            outer.Children.Add(card);
            return outer;
        }

        public static WpfControls.TextBlock Heading(string text)
        {
            return new WpfControls.TextBlock
            {
                Text = text,
                FontSize = 20,
                FontWeight = FontWeights.SemiBold,
                Foreground = Ink,
                Margin = new Thickness(2, 0, 0, 14),
            };
        }

        public static WpfControls.StackPanel Column(params FrameworkElement[] children)
        {
            var sp = new WpfControls.StackPanel();
            foreach (var c in children) sp.Children.Add(c);
            return sp;
        }
    }
}
```

- [ ] **Step 2: 写 `bar/Panel/Pages/AppearancePage.cs`**

```csharp
using System.Windows;
using WpfControls = System.Windows.Controls;
using UiControls = Wpf.Ui.Controls;

namespace DshBar
{
    internal sealed class AppearancePage : WpfControls.ContentControl, IPanelPage
    {
        public AppearancePage()
        {
            var cfg = App.Config;

            var light = Radio("浅色", "light", cfg.Theme);
            var dark = Radio("深色", "dark", cfg.Theme);
            var sys = Radio("跟随系统", "system", cfg.Theme);
            foreach (var rb in new[] { light, dark, sys })
                rb.Checked += (s, e) =>
                {
                    App.Config.Theme = (string)((WpfControls.RadioButton)s).Tag;
                    App.ApplyTheme();
                    App.SaveSettings();
                };

            var showBar = new UiControls.ToggleSwitch
            {
                IsChecked = cfg.ShowBar,
                OnContent = "显示",
                OffContent = "隐藏",
            };
            showBar.Checked += (s, e) => SetBar(true);
            showBar.Unchecked += (s, e) => SetBar(false);

            Content = Ui.Column(
                Ui.Heading("外观"),
                Ui.Group("主题",
                    Ui.Row("配色",
                        "面板默认浅色（白底黑字）；任务栏状态条始终跟随系统深浅色，不受这里影响。",
                        Row(light, dark, sys))),
                Ui.Group("显示模式",
                    Ui.Row("任务栏状态条", "隐藏后仍可通过托盘图标打开本面板。", showBar)));
        }

        static WpfControls.RadioButton Radio(string text, string value, string current)
        {
            return new WpfControls.RadioButton
            {
                Content = text,
                Tag = value,
                GroupName = "theme",
                Margin = new Thickness(0, 0, 14, 0),
                IsChecked = current == value,
            };
        }

        /// <summary>Ui.Row 的右侧只收一个元素，需要横排时用这个包一层。</summary>
        static FrameworkElement Row(params FrameworkElement[] items)
        {
            var sp = new WpfControls.StackPanel { Orientation = WpfControls.Orientation.Horizontal };
            foreach (var i in items) sp.Children.Add(i);
            return sp;
        }

        void SetBar(bool on)
        {
            App.Config.ShowBar = on;
            App.ApplyBarVisibility();
            App.SaveSettings();
        }

        public void Refresh(Snapshot snap) { }
    }
}
```

注意：`Ui.Row2` 要到 Task 7 才加进 `Ui.cs`，本页先用私有 `Row` 助手，避免跨任务依赖倒置。

- [ ] **Step 3: `App.cs` 加生效逻辑**

```csharp
        internal static void ApplyTheme()
        {
            try
            {
                if (_settings.Theme == "system")
                    Wpf.Ui.Appearance.ApplicationThemeManager.ApplySystemTheme();
                else
                    Wpf.Ui.Appearance.ApplicationThemeManager.Apply(
                        _settings.Theme == "dark"
                            ? Wpf.Ui.Appearance.ApplicationTheme.Dark
                            : Wpf.Ui.Appearance.ApplicationTheme.Light,
                        Wpf.Ui.Appearance.WindowBackdropType.Mica, false);
            }
            catch (Exception ex) { Log($"应用主题失败: {ex.Message}"); }
        }

        internal static void ApplyBarVisibility()
        {
            try
            {
                var hwnd = new System.Windows.Interop.WindowInteropHelper(_window).Handle;
                if (hwnd == IntPtr.Zero) return;
                if (_settings.ShowBar) { DockNow(hwnd); }
                else { Native.Undock(hwnd); }
            }
            catch (Exception ex) { Log($"切换状态条可见性失败: {ex.Message}"); }
        }

        internal static void SaveSettings()
        {
            try { _settings?.Save(SettingsFile); }
            catch (Exception ex) { Log($"保存设置失败: {ex.Message}"); }
        }
```

`Main` 里 `_settings = Settings.Load(SettingsFile);` 之后加 `ApplyTheme();`。
`PlaceNow` 开头加保护（隐藏时不再落位）：

```csharp
            if (!_settings.ShowBar) return;
```

`Tick()` 里 `PlaceNow(hwnd);` 之前加：

```csharp
                if (!_settings.ShowBar) { _tray.Text = TooltipFor(_last); return; }
```

- [ ] **Step 4: 从 `Pages.cs` 删掉 `AppearancePage` 占位类**

只删 `internal sealed class AppearancePage : PageBase { ... }` 一行，其余占位保留。

- [ ] **Step 5: 编译并验证外观页**

Run: `cd bar && dotnet build -c Release --nologo > ../b.log 2>&1; echo $?; grep -a ": warning\|: error" ../b.log | head`
Expected: `0`，无输出（0 警告 0 错误）。

Run: `cd bar/bin/Release/net10.0-windows && ./DshBar.exe --panel-shot appearance probe.png`
Expected: `SHOT appearance 900 620 <colors>`，colors > 200。随后 `rm -f probe.png`。

- [ ] **Step 6: 加设置往返断言并跑全量**

`smoke_test.py` 的 `check_panel_shell()` 之后加：

```python
def check_settings_roundtrip() -> None:
    print("\n== 设置往返 ==")
    f = os.path.join(ds.state_dir(), "settings.json")
    backup = open(f, encoding="utf-8").read() if os.path.isfile(f) else None
    try:
        for theme in ("dark", "system", "light"):
            with open(f, "w", encoding="utf-8") as fh:
                json.dump({"interval": 3, "notify": False, "repeatSec": 45,
                           "theme": theme, "showBar": False}, fh)
            p = subprocess.run([BAR_EXE, "--panel-shot", "appearance", "-"],
                               capture_output=True, text=True, errors="replace",
                               timeout=120, cwd=os.path.dirname(BAR_EXE))
            check(f"theme={theme} 能被读回并渲染", p.returncode == 0 and "SHOT" in p.stdout,
                  f"退出码 {p.returncode} {(p.stdout + p.stderr)[-160:]}")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write('{"theme": "nonsense", "interval": 0}')
        p = subprocess.run([BAR_EXE, "--panel-shot", "appearance", "-"],
                           capture_output=True, text=True, errors="replace",
                           timeout=120, cwd=os.path.dirname(BAR_EXE))
        check("非法 theme 回退浅色且不崩", p.returncode == 0 and "SHOT" in p.stdout,
              f"退出码 {p.returncode} {(p.stdout + p.stderr)[-160:]}")
    finally:
        if backup is None:
            if os.path.isfile(f):
                os.remove(f)
        else:
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(backup)
```

`main()` 里 `check_panel_window()` 之后调用它。

Run: `python smoke_test.py --all`
Expected: 退出码 0。

- [ ] **Step 7: 提交**

```bash
git add bar/Panel/Ui.cs bar/Panel/Pages/AppearancePage.cs bar/Panel/Pages/Pages.cs bar/App.cs smoke_test.py
git commit -m "feat(panel): 自绘分组控件、外观页与主题/状态条生效"
```

---

### Task 6: 通知页与运行页

**Files:**
- Create: `bar/Panel/Pages/NotifyPage.cs`
- Create: `bar/Panel/Pages/RuntimePage.cs`
- Modify: `bar/Panel/Pages/Pages.cs`（删两个占位）
- Test: `smoke_test.py`

**Interfaces:**
- Consumes: `App.Config`、`App.RestartEngine()`、`App.RequestBalanceRefresh()`、`App.AutostartOn()/SetAutostart()`、`App.LogPath`、`App.DataDir`、`App.OpenInExplorer()`。
- Produces: 两个实现 `IPanelPage` 的页面类。

- [ ] **Step 1: 写 `NotifyPage.cs`**

```csharp
using System.Windows;
using WpfControls = System.Windows.Controls;
using UiControls = Wpf.Ui.Controls;

namespace DshBar
{
    internal sealed class NotifyPage : WpfControls.ContentControl, IPanelPage
    {
        public NotifyPage()
        {
            var cfg = App.Config;

            var notify = new UiControls.ToggleSwitch
            { IsChecked = cfg.Notify, OnContent = "开", OffContent = "关" };
            notify.Checked += (s, e) => Save(true, null);
            notify.Unchecked += (s, e) => Save(false, null);

            var repeat = new UiControls.NumberBox
            { Width = 120, Minimum = 0, Maximum = 3600, Value = cfg.RepeatSec };
            repeat.ValueChanged += (s, e) =>
            {
                int v = (int)(e.NewValue ?? 90);
                Save(null, v);
            };

            Content = Ui.Column(
                Ui.Heading("通知"),
                Ui.Group("系统通知",
                    Ui.Row("托盘气泡提醒", "「需要操作」与「出错了」时弹系统通知；回答完成只在从活动态转入时提醒一次。", notify),
                    Ui.Row("重复提醒间隔（秒）", "「需要操作」未处理时按这个间隔重复提醒；0 表示不重复。", repeat)));
        }

        static void Save(bool? notify, int? repeatSec)
        {
            if (notify.HasValue) App.Config.Notify = notify.Value;
            if (repeatSec.HasValue) App.Config.RepeatSec = repeatSec.Value < 0 ? 0 : repeatSec.Value;
            App.SaveSettings();
        }

        public void Refresh(Snapshot snap) { }
    }
}
```

- [ ] **Step 2: 写 `RuntimePage.cs`**

```csharp
using System;
using System.Windows;
using WpfControls = System.Windows.Controls;
using UiControls = Wpf.Ui.Controls;

namespace DshBar
{
    internal sealed class RuntimePage : WpfControls.ContentControl, IPanelPage
    {
        readonly TextBlock _thresholds = new WpfControls.TextBlock
        {
            FontSize = 12,
            Foreground = Ui.InkDim,
            TextWrapping = TextWrapping.Wrap,
        };

        public RuntimePage()
        {
            var interval = new UiControls.NumberBox
            { Width = 120, Minimum = 1, Maximum = 60, Value = App.Config.Interval };
            interval.ValueChanged += (s, e) =>
            {
                App.Config.Interval = Math.Max(1, (int)(e.NewValue ?? 2));
                App.SaveSettings();
                App.RestartEngine();
            };

            var autostart = new UiControls.ToggleSwitch
            { IsChecked = App.AutostartOn(), OnContent = "开", OffContent = "关" };
            autostart.Checked += (s, e) => App.SetAutostart(true);
            autostart.Unchecked += (s, e) => App.SetAutostart(false);

            Content = Ui.Column(
                Ui.Heading("运行"),
                Ui.Group("检测引擎",
                    Ui.Row("轮询间隔（秒）", "改完会自动重启检测进程生效。", interval),
                    Ui.Row("阈值", "阈值目前只读，改常量要动 dsh_state.py 顶部。", _thresholds),
                    Ui.Row("检测进程", "引擎异常退出本来也会自动重启；这里可以手动重启。",
                        PushButton("重启检测进程", () => App.RestartEngine()))),
                Ui.Group("开机与文件",
                    Ui.Row("开机自动启动", "写入当前用户的注册表 Run 项。", autostart),
                    Ui.Row("日志", "状态栏没出现、状态不对时先看它。",
                        PushButton("打开日志", () => App.OpenInExplorer(App.LogPath))),
                    Ui.Row("数据目录", "settings.json、余额 Key、刷新标记都在这里。",
                        PushButton("打开数据目录", () => App.OpenInExplorer(App.DataDir)))));
        }

        static FrameworkElement PushButton(string text, Action onClick)
        {
            var b = new UiControls.Button { Content = text, Padding = new Thickness(12, 5, 12, 5) };
            b.Click += (s, e) => onClick();
            return b;
        }

        public void Refresh(Snapshot snap)
        {
            _thresholds.Text =
                "活跃窗口 / 完成窗口 / 卡住窗口 / 待处理升级窗口等阈值定义在 dsh_state.py 顶部，" +
                "python test_dsh_state.py 会校验参与这些判定的边界。";
        }
    }
}
```

- [ ] **Step 3: 从 `Pages.cs` 删掉 `NotifyPage`、`RuntimePage` 两个占位类**

- [ ] **Step 4: 编译 + 跑两页 shot**

Run: `cd bar && dotnet build -c Release --nologo > ../b.log 2>&1; echo $?; grep -a ": warning\|: error" ../b.log | head`
Expected: `0`，无输出。

Run: `cd bar/bin/Release/net10.0-windows && ./DshBar.exe --panel-shot notify p1.png && ./DshBar.exe --panel-shot runtime p2.png && rm -f p1.png p2.png`
Expected: 两行 `SHOT ... 900 620 <colors>`，colors > 200。

- [ ] **Step 5: 加通知/运行设置的往返断言**

`smoke_test.py` 的 `check_settings_roundtrip()` 里，非法 theme 断言之前插入：

```python
        with open(f, "w", encoding="utf-8") as fh:
            json.dump({"interval": 7, "notify": False, "repeatSec": 33,
                       "theme": "light", "showBar": True}, fh)
        p = subprocess.run([BAR_EXE, "--panel-shot", "notify", "-"],
                           capture_output=True, text=True, errors="replace",
                           timeout=120, cwd=os.path.dirname(BAR_EXE))
        check("notify/repeatSec 写进设置后面板仍正常", p.returncode == 0 and "SHOT" in p.stdout,
              (p.stdout + p.stderr)[-160:])
```

- [ ] **Step 6: 跑全量回归**

Run: `python smoke_test.py --all`
Expected: 退出码 0。

- [ ] **Step 7: 提交**

```bash
git add bar/Panel/Pages/NotifyPage.cs bar/Panel/Pages/RuntimePage.cs bar/Panel/Pages/Pages.cs smoke_test.py
git commit -m "feat(panel): 通知页与运行页"
```

---

### Task 7: 余额页并删除 `KeyDialog`

**Files:**
- Create: `bar/Panel/Pages/BalancePage.cs`
- Delete: `bar/KeyDialog.cs`
- Modify: `bar/App.cs`（菜单项改为打开面板余额页；`KeyDialog.Ask()` 调用点替换）
- Test: `smoke_test.py`

**Interfaces:**
- Consumes: `App.RequestBalanceRefresh()`、`App.OpenInExplorer()`；引擎的 `--save-balance-key` / `--clear-balance-key`（stdin 传 Key，已存在且冒烟已覆盖）。
- Produces: `BalancePage`；`internal static void SaveBalanceKey(string key)`、`internal static void ClearBalanceKey()`（App 上的子进程封装，取代 KeyDialog 里的重复实现）。

- [ ] **Step 1: 把 KeyDialog 的子进程逻辑搬到 App**

`bar/App.cs` 内加（放在 `RefreshBalance()` 附近）：

```csharp
        static void RunEngineVerb(string verb, string stdin)
        {
            string engine = Path.Combine(AppContext.BaseDirectory, "dsh_state.py");
            if (!File.Exists(engine))
                engine = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "dsh_state.py"));
            string python = ResolvePython();
            if (python == null || !File.Exists(engine))
            {
                Balloon("状态检测引擎不可用", "请确认 dsh_state.py 与 python 3.14 就位", ToolTipIcon.Error);
                return;
            }
            try
            {
                var psi = new System.Diagnostics.ProcessStartInfo
                {
                    FileName = python,
                    Arguments = $"-X utf8 \"{engine}\" {verb}",
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardInput = stdin != null,
                    RedirectStandardOutput = true,
                    WorkingDirectory = Path.GetDirectoryName(engine),
                    StandardOutputEncoding = new UTF8Encoding(false),
                };
                using var proc = System.Diagnostics.Process.Start(psi);
                if (stdin != null)
                {
                    proc.StandardInput.Write(stdin);
                    proc.StandardInput.Close();
                }
                string output = proc.StandardOutput.ReadToEnd();
                proc.WaitForExit(20000);
                Log($"{verb} -> {output.Trim()}");
                RefreshBalance();
            }
            catch (Exception ex)
            {
                Balloon("余额 Key 操作失败", ex.Message, ToolTipIcon.Error);
            }
        }

        internal static void SaveBalanceKey(string key) => RunEngineVerb("--save-balance-key", key);
        internal static void ClearBalanceKey() => RunEngineVerb("--clear-balance-key", null);
```

- [ ] **Step 2: 写 `BalancePage.cs`**

```csharp
using System.Windows;
using WpfControls = System.Windows.Controls;
using UiControls = Wpf.Ui.Controls;

namespace DshBar
{
    internal sealed class BalancePage : WpfControls.ContentControl, IPanelPage
    {
        readonly WpfControls.PasswordBox _key = new WpfControls.PasswordBox { Width = 300 };
        readonly WpfControls.TextBlock _state = new WpfControls.TextBlock
        { FontSize = 12, Foreground = Ui.InkDim, TextWrapping = TextWrapping.Wrap };

        public BalancePage()
        {
            var save = new UiControls.Button { Content = "保存（当前用户加密）", Padding = new Thickness(12, 5, 12, 5) };
            save.Click += (s, e) =>
            {
                string k = _key.Password == null ? "" : _key.Password.Trim();
                if (k.Length > 0)
                {
                    App.SaveBalanceKey(k);
                    _key.Clear();
                }
            };
            var clear = new UiControls.Button { Content = "清除已存 Key", Padding = new Thickness(12, 5, 12, 5), Margin = new Thickness(8, 0, 0, 0) };
            clear.Click += (s, e) => { App.ClearBalanceKey(); _state.Text = "已请求清除。"; };
            var refresh = new UiControls.Button { Content = "立即刷新余额", Padding = new Thickness(12, 5, 12, 5) };
            refresh.Click += (s, e) => App.RequestBalanceRefresh();

            Content = Ui.Column(
                Ui.Heading("余额"),
                Ui.Group("Key",
                    Ui.Row("录入余额 Key", "平台控制台 → API Keys → 可查询余额的 Key。用当前 Windows 账户的 DPAPI 加密保存，不是明文。",
                        Ui.Row2(_key, save, clear)),
                    Ui.Row("生效优先级", "环境变量 DEEPSEEK_BALANCE_KEY > DEEPSEEK_API_KEY > 已保存的加密 Key > 明文文件。环境变量存在时会盖住已保存的 Key。", null),
                    _state),
                Ui.Group("查询",
                    Ui.Row("刷新", "默认 5 分钟查一次，这里可以立刻重查。", refresh)));
        }

        public void Refresh(Snapshot snap)
        {
            var b = snap?.Balance;
            if (b == null) return;
            _state.Text = b.Available
                ? $"当前余额 {b.Currency} {b.Total}（来源 {(string.IsNullOrEmpty(b.Source) ? "未知" : b.Source)}）"
                : $"余额不可用：{b.Error}";
        }
    }
}
```

`Ui.Row2` 是"把多个控件横向排在行的右侧"的小助手，加到 `bar/Panel/Ui.cs`：

```csharp
        public static FrameworkElement Row2(params FrameworkElement[] items)
        {
            var sp = new WpfControls.StackPanel { Orientation = WpfControls.Orientation.Horizontal };
            foreach (var i in items) sp.Children.Add(i);
            return sp;
        }
```

`Balance` 模型需要 `Source` 字段——`bar/StateClient.cs` 的 `Balance` 类里补：

```csharp
        [JsonPropertyName("source")] public string Source { get; set; }
```

- [ ] **Step 3: 换掉 KeyDialog 的调用点、删占位、删文件**

先从 `bar/Panel/Pages/Pages.cs` 删掉 `internal sealed class BalancePage : PageBase { ... }`
这一行占位类 —— **不删就是同名类重复定义，CS0101 直接编译不过**。

`bar/App.cs` 的 `BuildTray()` 里：

```csharp
            Add(menu, "设置余额 Key…", (s, e) => KeyDialog.Ask());
```

改为：

```csharp
            Add(menu, "设置余额 Key…", (s, e) => OpenPanel("balance"));
```

Run: `git rm bar/KeyDialog.cs`
Expected: `rm 'bar/KeyDialog.cs'`。

- [ ] **Step 4: 编译确认没有残留引用**

Run: `cd bar && dotnet build -c Release --nologo > ../b.log 2>&1; echo $?; grep -a "KeyDialog\|: error\|: warning" ../b.log | head`
Expected: `0`，无输出。若有 `CS0246 KeyDialog` 说明还有调用点，回到 Step 3 清干净。

- [ ] **Step 5: 跑余额页渲染与 DPAPI 往返**

Run: `cd bar/bin/Release/net10.0-windows && ./DshBar.exe --panel-shot balance p.png && rm -f p.png`
Expected: `SHOT balance 900 620 <colors>`，colors > 200。

Run: `python smoke_test.py`
Expected: 退出码 0（`== 余额 Key 存取 ==` 5 项仍全过——那是引擎侧，不受本次改动影响）。

- [ ] **Step 6: 提交**

```bash
git add bar/App.cs bar/Panel/Pages/BalancePage.cs bar/Panel/Ui.cs bar/StateClient.cs
git commit -m "feat(panel): 余额页并入面板，移除独立的 KeyDialog"
```

---

### Task 8: 概览页填实

**Files:**
- Create: `bar/Panel/Pages/OverviewPage.cs`
- Modify: `bar/Panel/Pages/Pages.cs`（删 `OverviewPage` 占位）
- Test: `smoke_test.py`

**Interfaces:**
- Consumes: `Snapshot.Sessions`（Task 4）、`Snapshot.State/Label/Color/StripRight/TipLines/Waiting`、`STATES` 的中文标签与配色（前端自己映射，见 Step 1 的 `StateLabel`）。
- Produces: 概览页 = 当前状态卡 + 会话表 + 待处理卡。

- [ ] **Step 1: 写状态标签与配色的前端映射**

`bar/Panel/Ui.cs` 末尾（`}` 之前）加：

```csharp
        static readonly System.Collections.Generic.Dictionary<string, (string Label, string Hex)> StateMap =
            new System.Collections.Generic.Dictionary<string, (string, string)>
        {
            { "needs_action", ("需要操作", "#DC2626") },
            { "error", ("出错了", "#BE123C") },
            { "stalled", ("疑似卡住", "#7C3AED") },
            { "thinking", ("正在思考", "#D97706") },
            { "tool_running", ("正在执行工具", "#0D9488") },
            { "answering", ("正在回答", "#2563EB") },
            { "done", ("回答完成", "#16A34A") },
            { "aborted", ("已中断", "#64748B") },
            { "idle", ("待命", "#6B7280") },
            { "offline", ("未运行", "#9CA3AF") },
            { "unknown", ("未知", "#9CA3AF") },
        };

        public static string LabelOf(string state)
        {
            return StateMap.TryGetValue(state ?? "", out var v) ? v.Label : "未知";
        }

        public static Brush BrushOf(string hex)
        {
            try { return new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex)); }
            catch { return InkDim; }
        }
```

`Ui.cs` 顶部补 `using System.Windows;`（`Color`/`ColorConverter` 需要）。

- [ ] **Step 2: 写 `OverviewPage.cs`**

```csharp
using System;
using System.Windows;
using System.Windows.Media;
using WpfControls = System.Windows.Controls;

namespace DshBar
{
    internal sealed class OverviewPage : WpfControls.ContentControl, IPanelPage
    {
        readonly WpfControls.Border _accent = new WpfControls.Border
        { Width = 4, CornerRadius = new CornerRadius(2), HorizontalAlignment = HorizontalAlignment.Left };
        readonly WpfControls.TextBlock _big = new WpfControls.TextBlock
        { FontSize = 26, FontWeight = FontWeights.SemiBold, Foreground = Ui.Ink, VerticalAlignment = VerticalAlignment.Center };
        readonly WpfControls.TextBlock _sub = new WpfControls.TextBlock { FontSize = 12.5, Foreground = Ui.InkDim, Margin = new Thickness(0, 4, 0, 0) };
        readonly WpfControls.TextBlock _waiting = new WpfControls.TextBlock { FontSize = 12.5, Foreground = Ui.Ink, TextWrapping = TextWrapping.Wrap };

        readonly WpfControls.DataGrid _grid = new WpfControls.DataGrid
        {
            AutoGenerateColumns = false,
            IsReadOnly = true,
            HeadersVisibility = WpfControls.DataGridHeadersVisibility.Column,
            GridLinesVisibility = WpfControls.DataGridGridLinesVisibility.Horizontal,
            Background = Brushes.White,
            BorderBrush = Ui.CardLine,
            BorderThickness = new Thickness(1),
            MinHeight = 280,
            FontSize = 12.5,
        };

        public OverviewPage()
        {
            AddColumn("项目", "Project", 150);
            AddColumn("状态", "StateLabel", 110);
            AddColumn("轮次", "TurnText", 80);
            AddColumn("静默", "AgeText", 80);
            AddColumn("任务", "TodoText", 90);
            AddColumn("最近工具", "LastTool", 130);
            AddColumn("tokens", "UsageText", 110);
            AddColumn("标题", "Title", 200);

            var statusCard = new WpfControls.Border
            {
                Background = Ui.CardBg,
                BorderBrush = Ui.CardLine,
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(8),
                Padding = new Thickness(14, 10, 14, 10),
                Child = new WpfControls.Grid { Children = { _accent, new WpfControls.StackPanel { Margin = new Thickness(16, 0, 0, 0), Children = { _big, _sub } } } },
            };

            Content = Ui.Column(
                Ui.Heading("概览"),
                statusCard,
                Ui.Group("会话", _grid),
                Ui.Group("等你处理", _waiting));
        }

        void AddColumn(string header, string binding, double width)
        {
            _grid.Columns.Add(new WpfControls.DataGridTextColumn
            {
                Header = header,
                Binding = new System.Windows.Data.Binding(binding),
                Width = new WpfControls.DataGridLength(width),
            });
        }

        public void Refresh(Snapshot snap)
        {
            if (snap == null) return;
            _big.Text = Ui.LabelOf(snap.State);
            _accent.Background = Ui.BrushOf(snap.Color);
            var s = snap.Session;
            _sub.Text = s == null
                ? "没有找到 dsh 会话记录"
                : $"{s.Project} · 静默 {Math.Round(s.AgeSec)} 秒 · 余额 {snap.StripRight ?? "--"} · 共 {Count(snap)} 个会话";
            _grid.ItemsSource = SessionView.Of(snap.Sessions);
            if (snap.Waiting == null || snap.Waiting.Count == 0)
                _waiting.Text = "没有待处理的问题";
            else
                foreach (var w in snap.Waiting)
                    _waiting.Text += $"{w.Project}：{w.Text}\n";
        }

        static int Count(Snapshot snap) => snap.Sessions?.Count ?? 0;
    }

    /// <summary>给 DataGrid 用的展示层投影：把原始字段变成中文可读文本。</summary>
    internal sealed class SessionView
    {
        public string Project { get; set; }
        public string StateLabel { get; set; }
        public string TurnText { get; set; }
        public string AgeText { get; set; }
        public string TodoText { get; set; }
        public string LastTool { get; set; }
        public string UsageText { get; set; }
        public string Title { get; set; }

        public static System.Collections.Generic.IList<SessionView> Of(System.Collections.Generic.List<SessionRow> rows)
        {
            var list = new System.Collections.Generic.List<SessionView>();
            if (rows == null) return list;
            foreach (var r in rows)
            {
                list.Add(new SessionView
                {
                    Project = r.Project,
                    StateLabel = Ui.LabelOf(r.State),
                    TurnText = r.Turn.HasValue ? $"T{r.Turn}" + (r.Step.HasValue ? $"/S{r.Step}" : "") : "",
                    AgeText = $"{Math.Round(r.AgeSec)}s",
                    TodoText = r.Todo == null || r.Todo.Total == 0 ? "" : $"{r.Todo.Done}/{r.Todo.Total}",
                    LastTool = r.LastTool ?? "",
                    UsageText = r.Usage?.Total > 0 ? r.Usage.Total.ToString("N0") : "",
                    Title = r.Title ?? "",
                });
            }
            return list;
        }
    }
}
```

- [ ] **Step 3: 从 `Pages.cs` 删掉 `OverviewPage` 占位类**

若 `Pages.cs` 此时只剩 `PageBase` 且已无子类引用，保留 `PageBase` 给后续页面复用；若确认无人使用则整个文件删除。判定方法：编译后 grep。

Run: `grep -rn "PageBase" bar/ | grep -v "Pages.cs"`
Expected: 无输出 → 删掉 `bar/Panel/Pages/Pages.cs`；有输出 → 保留。

- [ ] **Step 4: 编译 + 概览页门禁**

Run: `cd bar && dotnet build -c Release --nologo > ../b.log 2>&1; echo $?; grep -a ": warning\|: error" ../b.log | head`
Expected: `0`，无输出。

`smoke_test.py` 的 `check_panel_shell()` 里，紧接 Task 4 加的那条概览断言之后补一条对比：

```python
    check("概览页比关于页更丰富（会话表已填上真实数据）",
          shots.get("overview", 0) > shots.get("about", 0),
          f"overview={shots.get('overview')} about={shots.get('about')}")
```

Run: `python smoke_test.py --gui`
Expected: 退出码 0，且 `概览页比关于页更丰富（会话表已填上真实数据）` ✓。

- [ ] **Step 5: 人工目视**

Run: `cd bar/bin/Release/net10.0-windows && ./DshBar.exe --panel overview`
Expected: 任务栏状态条照常；面板居中打开，浅色白底，左侧 6 项导航可点，概览页顶部大字显示当前状态、会话表有行、余额非空或显示 `--`。看完托盘「退出」。

- [ ] **Step 6: 提交**

```bash
git add bar/Panel/
git commit -m "feat(panel): 概览页填实（当前状态卡 + 全部会话表 + 待处理）"
```

---

### Task 9: 关于页与开源清单的应用内呈现

**Files:**
- Create: `bar/Panel/Pages/AboutPage.cs`
- Modify: `bar/Panel/Pages/Pages.cs`（按 Task 8 Step 3 的结论处理）
- Test: `smoke_test.py`

**Interfaces:**
- Consumes: 程序集 `Version`、`LICENSE` 与 `THIRD-PARTY-NOTICES.md` 的路径。
- Produces: 关于页显示版本号、MIT 摘要、第三方清单（含 WPF-UI 传递依赖与 Segoe 字体的例外说明），两个按钮打开 `LICENSE` / `THIRD-PARTY-NOTICES.md`。

- [ ] **Step 1: App 暴露仓库根目录与版本**

`bar/App.cs` 加：

```csharp
        internal static string RepoRoot
        {
            get
            {
                string here = AppContext.BaseDirectory;
                var dir = new DirectoryInfo(here);
                for (int i = 0; i < 8 && dir != null; i++, dir = dir.Parent)
                    if (File.Exists(Path.Combine(dir.FullName, "THIRD-PARTY-NOTICES.md")))
                        return dir.FullName;
                return Path.GetFullPath(Path.Combine(here, "..", "..", "..", ".."));
            }
        }

        internal static string VersionText
        {
            get
            {
                var v = typeof(App).Assembly.GetName().Version;
                return v == null ? "未知" : $"{v.Major}.{v.Minor}.{v.Build}";
            }
        }
```

- [ ] **Step 2: 写 `AboutPage.cs`**

```csharp
using System.IO;
using System.Windows;
using System.Windows.Media;
using WpfControls = System.Windows.Controls;
using UiControls = Wpf.Ui.Controls;

namespace DshBar
{
    internal sealed class AboutPage : WpfControls.ContentControl, IPanelPage
    {
        static readonly string[][] Notices =
        {
            new[] { "WPF-UI 4.2.0", "MIT", "© 2021-2025 Leszek Pomianowski and WPF UI Contributors", "控制台窗口外壳与表单控件" },
            new[] { "VirtualizingWrapPanel 2.0.6", "MIT", "© 2019 S. Bäumlisberger", "WPF-UI 内含，随其一并传递" },
            new[] { "fluentui-system-icons 1.1.242", "MIT", "© 2020 Microsoft Corporation", "WPF-UI 内含图标" },
            new[] { "dotnet/wpf 8.0", "MIT", "© Microsoft Corporation", "WPF-UI 内含" },
            new[] { "microsoft-ui-xaml 3.0", "MIT", "© Microsoft Corporation", "WPF-UI 内含" },
            new[] { "Segoe Fluent Icons 3.0", "微软专有字体许可（非 MIT）", "© Microsoft Corporation", "仅引用系统已装字体，不随包分发" },
            new[] { "AF-Media-Bar", "MIT", "© 2026 AmorFate", "停靠方式与分组/行布局的设计参照，未复制源码" },
            new[] { ".NET 10 Windows Desktop / Python 3.14", "运行时依赖", "-", "不随附其代码" },
        };

        public AboutPage()
        {
            var rows = new WpfControls.StackPanel();
            foreach (var n in Notices)
            {
                rows.Children.Add(new WpfControls.Grid { Margin = new Thickness(14, 8, 14, 8), Children =
                {
                    new WpfControls.StackPanel { Children =
                    {
                        new WpfControls.TextBlock { Text = n[0] + "  ·  " + n[1], FontSize = 13, FontWeight = FontWeights.SemiBold, Foreground = Ui.Ink },
                        new WpfControls.TextBlock { Text = n[2] + " — " + n[3], FontSize = 12, Foreground = Ui.InkDim, TextWrapping = TextWrapping.Wrap },
                    } },
                } });
                rows.Children.Add(new WpfControls.Border { Height = 1, Background = Ui.CardLine, Margin = new Thickness(14, 0, 14, 0) });
            }

            var openLicense = new UiControls.Button { Content = "打开 LICENSE", Padding = new Thickness(12, 5, 12, 5) };
            openLicense.Click += (s, e) => App.OpenInExplorer(Path.Combine(App.RepoRoot, "LICENSE"));
            var openNotices = new UiControls.Button { Content = "打开完整第三方声明", Padding = new Thickness(12, 5, 12, 5), Margin = new Thickness(8, 0, 0, 0) };
            openNotices.Click += (s, e) => App.OpenInExplorer(Path.Combine(App.RepoRoot, "THIRD-PARTY-NOTICES.md"));

            Content = Ui.Column(
                Ui.Heading("关于"),
                Ui.Group("版本",
                    Ui.Row("dsh 任务栏状态栏", $"版本 {App.VersionText} · 许可证 MIT · © 2026 Wildcreator", null),
                    Ui.Row("开源声明", "本项目与全部第三方（含 WPF-UI 的传递依赖）的许可证如下。",
                        Ui.Row2(openLicense, openNotices))),
                Ui.Group("第三方清单", rows));
        }

        public void Refresh(Snapshot snap) { }
    }
}
```

- [ ] **Step 3: 从 `Pages.cs` 删掉 `AboutPage` 占位类，并按需处理 `PageBase`**

- [ ] **Step 4: 编译 + 关于页断言**

Run: `cd bar && dotnet build -c Release --nologo > ../b.log 2>&1; echo $?; grep -a ": warning\|: error" ../b.log | head`
Expected: `0`，无输出。

`smoke_test.py` 的 `check_panel_shell()` 循环之后已有 overview/about 对比断言；再补一条"关于页画出了清单"的断言：

```python
    if "about" in shots:
        check("关于页画出了第三方清单（颜色种类足够多）", shots["about"] > 250,
              f"colors={shots['about']}")
```

Run: `python smoke_test.py --gui`
Expected: 退出码 0。

- [ ] **Step 5: 提交**

```bash
git add bar/Panel/ bar/App.cs smoke_test.py
git commit -m "feat(panel): 关于页在应用内呈现版本与开源清单"
```

---

### Task 10: 二次实例转交与全量收尾

**Files:**
- Modify: `bar/App.cs`（转交路径已在 Task 3 建好，本任务补验证与收尾）
- Test: `smoke_test.py`

**Interfaces:**
- Consumes: Task 3 的 `--panel`、`panel.request` 轮询、`PanelWindow.Instance`。
- Produces: `python smoke_test.py --all` 是这条链路的唯一权威门禁。

- [ ] **Step 1: 写转交断言**

`smoke_test.py` 的 `check_panel_window()` 之后加：

```python
def check_panel_handoff() -> None:
    print("\n== 二次实例转交 ==")
    if not os.path.isfile(BAR_EXE):
        print("  （跳过：无产物）")
        return
    req = os.path.join(ds.state_dir(), "panel.request")
    if os.path.isfile(req):
        os.remove(req)
    first = subprocess.Popen([BAR_EXE], cwd=os.path.dirname(BAR_EXE))
    try:
        u = _user32()
        t0 = time.time()
        while time.time() - t0 < 25 and not top_window("dsh 控制台"):
            time.sleep(0.3)
        check("首个实例未开面板", not top_window("dsh 控制台"), "不该自动打开面板")
        p = subprocess.Popen([BAR_EXE, "--panel", "balance"], cwd=os.path.dirname(BAR_EXE))
        rc = p.wait(timeout=30)
        check("第二个实例静默退出且码为 0", rc == 0, f"退出码 {rc}")
        check("第二个实例没有常驻", not bar_processes() or len(bar_processes()) == 1,
              str(bar_processes()))
        t1 = time.time()
        while time.time() - t1 < 20 and not top_window("dsh 控制台"):
            time.sleep(0.4)
        check("原实例收到请求并打开面板", bool(top_window("dsh 控制台")), "面板没出现")
        check("请求文件已被消费掉", not os.path.isfile(req), req)
    finally:
        subprocess.run(["taskkill", "/f", "/im", "DshBar.exe"], capture_output=True)
        time.sleep(3)
        check("收尾无残留", not bar_processes(), str(bar_processes()))
```

`main()` 里 `check_panel_window()` 之后调用（受 `--gui`/`--all` 门控）。

- [ ] **Step 2: 跑，确认失败或暴露真实缺陷**

Run: `python smoke_test.py --gui`
Expected: 可能失败于「首个实例未开面板」或「面板没出现」。**逐条按实际输出修 App.cs**，不要放宽断言。常见成因与对策：
- 「首个实例未开面板」失败 → `Main` 里 `panel != null` 判空写成了 `!= ""`，普通启动时 `panel` 必须保持 `null`。
- 「面板没出现」失败 → `Tick()` 里的请求轮询没跑到（`_settings.ShowBar` 为 false 时提前 `return` 了）；把请求轮询移到那句保护**之前**。
- 「请求文件已被消费掉」失败 → 读文件后没 `File.Delete`。

- [ ] **Step 3: 全量回归**

Run: `python test_dsh_state.py --live; echo EXIT=$?`
Expected: `EXIT=0`。

Run: `python smoke_test.py --all; echo EXIT=$?`
Expected: `EXIT=0`，全部 ✓，无「跳过」。

- [ ] **Step 4: 人工走一遍完整产品路径**

1. `start-bar.bat` → 任务栏出现状态条，浅色/深色跟随系统。
2. 托盘右键 → 「打开面板」→ 概览页有会话表与当前状态。
3. 外观页切「深色」→ 面板即时变深；切回「浅色」。
4. 隐藏任务栏状态条 → 状态条消失，托盘仍在；从托盘打开面板 → 能开；再显示状态条 → 重新停靠。
5. 运行页改轮询间隔 → 日志出现「引擎已启动 PID=…」；打开日志/数据目录。
6. 通知页改重复提醒间隔 → 重启面板后数值仍在。
7. 关于页两个按钮能打开对应文件。
8. 托盘「退出」→ 进程与 python 子进程都干净退出。

任何一步不符就是缺陷，改到符合为止；**不允许改断言来迁就实现**。

- [ ] **Step 5: README 收尾**

README 新增 `## 控制台面板` 一节：三个入口（托盘「打开面板」/ 状态栏右键 / `DshBar.exe --panel [页名]`）、6 页各自做什么、`--panel-shot` 是给冒烟用的离屏渲染。文件清单表补 `bar/Panel/` 一行。

- [ ] **Step 6: 提交**

```bash
git add smoke_test.py README.md bar/App.cs
git commit -m "feat(panel): 二次实例改为转交打开面板"
```

---

## 完成标准

- `python smoke_test.py --all` 退出码 0，无「跳过」项。
- `python test_dsh_state.py --live` 退出码 0。
- `cd bar && dotnet build -c Release` 0 错误 0 警告。
- 面板 6 页均可从托盘/右键/命令行到达，浅色默认可读，全部文案中文。
- `LICENSE` 与 `THIRD-PARTY-NOTICES.md` 覆盖 WPF-UI 及其 5 项传递依赖、AF-Media-Bar、运行时依赖，且 Segoe 字体的非 MIT 例外已显式标注。
- README 不再声称编译不需要联网，且新增控制台面板与开源协议两节。
