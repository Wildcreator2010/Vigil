#!/usr/bin/env python3
"""校验 dist/ 分发产物：文件齐不齐 + 随包那颗引擎能不能真干活。

  python tools/verify_package.py                自动取 dist/ 下最新的 Vigil-*-win-x64
  python tools/verify_package.py <dist 目录>     指定目录

退出码 0 通过，非 0 时把每条失败原样打印。`package.cmd` 与
`python smoke_test.py --package` 都调它，别再抄第二份校验逻辑。

这里刻意用**产物自己那份**解释器和**产物自己那份** Vigil.exe 跑真活：
"目录里有 python.exe" 不等于 "它能解 zstd"，而后者才是零前置的全部承诺。
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 校验和不在这里查：以前查的是 CPython 那个 zip，现在包里根本没有 zip 了。
MUST_EXIST_APP = (
    "Vigil.exe", "Vigil.dll", "Vigil.runtimeconfig.json", "Vigil.deps.json",
    "vigil-engine.exe",
    # 引擎的回退级：vigil-engine.exe 不在时才用得上（bar/Engine.cs）。它只是一份脚本，
    # 40KB，跟着走不亏；真不带它，回退级就退化成"这机器碰巧装了 Python 3.14"。
    "dsh_state.py",
    "Wpf.Ui.dll", "Vigil-Setup.exe",
)
MUST_EXIST_ROOT = ("Vigil-Setup.exe", "LICENSE.txt", "THIRD-PARTY-NOTICES.md", "安装说明.txt",
                   # 自包含载荷把 .NET 运行时二进制一起分发了，MIT 要求版权声明随副本走。
                   os.path.join("licenses", "dotnet-runtime-MIT.txt"),
                   os.path.join("licenses", "dotnet-windowsdesktop-MIT.txt"),
                   os.path.join("licenses", "README.txt"))


def file_version(path: str) -> str:
    ps = f"(Get-Item -LiteralPath '{path}').VersionInfo.FileVersion"
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, errors="replace", timeout=90)
    return r.stdout.strip()


def verify(dist_dir: str) -> list[str]:
    bad: list[str] = []
    # 必须转绝对路径：下面几处 subprocess 都带 cwd=app，相对路径会被叠成
    # `app\dist\...\dsh_state.py` 这种不存在的地方（实测红过两次）。
    dist_dir = os.path.abspath(dist_dir)
    app = os.path.join(dist_dir, "app")
    if not os.path.isdir(app):
        return [f"没有载荷目录 {app}"]

    for rel in MUST_EXIST_APP:
        if not os.path.isfile(os.path.join(app, rel)):
            bad.append(f"缺 app\\{rel}")
    for rel in MUST_EXIST_ROOT:
        if not os.path.isfile(os.path.join(dist_dir, rel)):
            bad.append(f"缺 {rel}")

    eng = os.path.join(app, "vigil-engine.exe")
    script = os.path.join(app, "dsh_state.py")
    exe = os.path.join(app, "Vigil.exe")

    # ① 随包的 C++ 引擎必须真能出 ok:true（--no-balance：校验不该依赖网络）
    if os.path.isfile(eng):
        r = subprocess.run([eng, "--json", "--no-balance"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180, cwd=app)
        lines = (r.stdout or "").strip().splitlines()
        snap = None
        try:
            snap = json.loads(lines[0]) if lines else None
        except Exception:
            snap = None
        if r.returncode != 0 or not snap or snap.get("ok") is not True:
            bad.append(f"随包引擎跑不动：rc={r.returncode} "
                       f"err={(r.stderr or '')[-200:]} out={(r.stdout or '')[-200:]}")
        else:
            print(f"  OK 随包引擎出快照：state={snap.get('state')} "
                  f"会话扫描={snap.get('sessions_scanned')}")

    # ② 产物里的 Vigil 必须认那份 C++ 引擎。
    # 上一版这条比的是 python= 指向随附解释器；现在 python= 应当是 none —— 不是故障，
    # 是"包里就没有解释器"这件事的证据。所以判据换成 kind + engine 两格。
    if os.path.isfile(exe):
        p = subprocess.run([exe, "--engine-probe"], capture_output=True, text=True,
                           errors="replace", timeout=180, cwd=app)
        kv = dict(l.split("=", 1) for l in (p.stdout or "").splitlines() if "=" in l)
        if kv.get("kind") != "native":
            bad.append(f"--engine-probe 没走随包引擎：kind={kv.get('kind')} 实得 {kv}")
        elif os.path.normcase(kv.get("engine", "")) != os.path.normcase(eng):
            bad.append(f"--engine-probe 报的引擎不在产物里：{kv.get('engine')} 应为 {eng}")
        else:
            print("  OK Vigil --engine-probe 认的是随包的 vigil-engine.exe")

    # ②b 反向证据：载荷里不该再有解释器那棵树。留着就是 24MB，而且
    # "没有 python" 才是"目标机零前置"这句话的可检查形式。
    leftover = [p for p in glob.glob(os.path.join(app, "runtime", "**", "*"), recursive=True)
                if os.path.isfile(p)]
    if leftover:
        bad.append(f"载荷里还留着 runtime\\ 那 {len(leftover)} 个文件（CPython 已不再是引擎）："
                   + ", ".join(os.path.relpath(x, app) for x in leftover[:4]))

    # ③ 两个 exe 的版本必须相等（package.cmd 用同一个 -p:Version 喂两边）
    v_app = file_version(exe) if os.path.isfile(exe) else ""
    v_setup = file_version(os.path.join(dist_dir, "Vigil-Setup.exe"))
    print(f"  · Vigil.exe FileVersion={v_app}  Vigil-Setup.exe FileVersion={v_setup}")
    if not v_app or v_app != v_setup:
        bad.append(f"两个 exe 版本不一致：{v_app!r} vs {v_setup!r}")

    # ④ 载荷不该带开发期的垃圾
    junk = [p for p in glob.glob(os.path.join(app, "*.log"))
            + glob.glob(os.path.join(app, "*.pdb"))
            if os.path.basename(p) != "Vigil.pdb"]
    if junk:
        bad.append("载荷里混进了非产物文件：" + ", ".join(os.path.basename(j) for j in junk[:5]))
    return bad


def dir_size_mb(path: str) -> float:
    n = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                n += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return n / (1024 * 1024)


def main() -> int:
    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        found = sorted(glob.glob(os.path.join(ROOT, "dist", "Vigil-*-win-x64")))
        if not found:
            print("dist/ 下没有 Vigil-*-win-x64，先跑 package.cmd")
            return 1
        target = found[-1]
    bad = verify(target)
    print(f"\n校验 {os.path.relpath(target, ROOT)}：{'通过' if not bad else f'{len(bad)} 项失败'}"
          f"（载荷 {dir_size_mb(os.path.join(target, 'app')):.0f}MB）")
    for b in bad:
        print(f"  FAIL {b}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
