#!/usr/bin/env python3
"""校验 dist/ 分发产物：是哪一版、文件齐不齐、随包那颗引擎能不能真干活。

  python tools/verify_package.py                自动取 dist/ 下最新的 Vigil-*-win-x64
  python tools/verify_package.py <dist 目录>     指定目录

退出码 0 通过，非 0 时把每条失败原样打印。`package.cmd` 与
`python smoke_test.py --package` 都调它，别再抄第二份校验逻辑。

这里刻意用**产物自己那份**解释器和**产物自己那份** Vigil.exe 跑真活：
"目录里有 python.exe" 不等于 "它能解 zstd"，而后者才是零前置的全部承诺。

两版并存以后多了一条判据：**产物得自证它是哪一版**。识别靠载荷里那颗引擎
（有 vigil-engine.exe 就是 Lilium，只有随包解释器就是 Vachellia），识别完再拿
tools/flavors.py 的期望值逐项比：目录名、两个 exe 的 FileVersion、--engine-probe
的 kind/codename/slug、该随包的许可证原文。打歪一份的表现不是编译错误，是
"Python 引擎那版装进了 Programs\\Vigil-Lilium"，而那在打包机上看不出来。
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import flavors

MUST_EXIST_APP = (
    "Vigil.exe", "Vigil.dll", "Vigil.runtimeconfig.json", "Vigil.deps.json",
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
# 引擎本体与随包许可证原文：两版各有各的凭据，缺哪个就说明那一版的载荷少了一块。
ENGINE_FILE = {"cpp": "vigil-engine.exe",
               "python": os.path.join("runtime", "python", "python.exe")}
LICENSE_FILE = {"cpp": os.path.join("licenses", "zstd-BSD.txt"),
                "python": os.path.join("licenses", "python-PSF.txt")}


def file_version(path: str) -> str:
    ps = f"(Get-Item -LiteralPath '{path}').VersionInfo.FileVersion"
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, errors="replace", timeout=90)
    return r.stdout.strip()


def three_part(v: str) -> str:
    """FileVersion 是四段（1.1.0.0），工程里写的是三段 —— 补齐才好比。"""
    parts = v.split(".")
    return ".".join(parts[:3]) if len(parts) >= 3 else v


def probe(exe: str, app: str) -> dict[str, str]:
    p = subprocess.run([exe, "--engine-probe"], capture_output=True, text=True,
                       errors="replace", timeout=180, cwd=app)
    return dict(l.split("=", 1) for l in (p.stdout or "").splitlines() if "=" in l)


def snapshot_json(argv: list[str], app: str) -> tuple[int, dict | None, str, str]:
    """跑一次真快照。--no-balance：校验不该依赖网络。"""
    r = subprocess.run(argv + ["--json", "--no-balance"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180, cwd=app)
    lines = (r.stdout or "").strip().splitlines()
    try:
        snap = json.loads(lines[0]) if lines else None
    except Exception:
        snap = None
    return r.returncode, snap, r.stdout or "", r.stderr or ""


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
    bundled_py = os.path.join(app, "runtime", "python", "python.exe")
    script = os.path.join(app, "dsh_state.py")
    exe = os.path.join(app, "Vigil.exe")

    # 哪一版由载荷自己说：引擎在就是 cpp，随包解释器在就是 python。两个都在/都不在
    # 都是打包流水线打歪了，后面所有判据都失去意义，所以这里就停。
    if os.path.isfile(eng) and os.path.isfile(bundled_py):
        return ["载荷里 vigil-engine.exe 与 runtime\\python 同时在场，认不出是哪一版"]
    flavor = "cpp" if os.path.isfile(eng) else "python" if os.path.isfile(bundled_py) else ""
    if not flavor:
        return [f"载荷里没有引擎：既没 app\\vigil-engine.exe 也没 app\\runtime\\python\\python.exe"]
    want_ver, want_code = flavors.version(flavor), flavors.codename(flavor)
    want_slug, want_kind = flavors.slug(flavor), flavors.engine_kind(flavor)
    print(f"  · 识别为 {flavor} 版：代号 {want_code}，版本 {want_ver}，引擎 {want_kind}")

    want_dir = flavors.dist_name(flavor)
    if os.path.basename(dist_dir) != want_dir:
        bad.append(f"目录名与内容不符：{os.path.basename(dist_dir)} 应为 {want_dir}")

    # ① 随包那颗引擎必须真能出 ok:true
    rc, snap, out, err = (snapshot_json([eng], app) if flavor == "cpp"
                          else snapshot_json([bundled_py, "-X", "utf8", script], app))
    if rc != 0 or not snap or snap.get("ok") is not True:
        bad.append(f"随包引擎跑不动（{flavor}）：rc={rc} err={err[-200:]} out={out[-200:]}")
    else:
        print(f"  OK 随包引擎出快照：state={snap.get('state')} "
              f"会话扫描={snap.get('sessions_scanned')}")

    # ② 产物里的 Vigil 认的必须是这一版该认的那一级，而且它得知道自己是哪一版
    if os.path.isfile(exe):
        kv = probe(exe, app)
        if kv.get("kind") != want_kind:
            bad.append(f"--engine-probe 的引擎级不对：kind={kv.get('kind')} 应为 {want_kind}"
                       f"（{flavor} 版）实得 {kv}")
        elif os.path.normcase(kv.get("engine", "")) != os.path.normcase(
                eng if flavor == "cpp" else script):
            bad.append(f"--engine-probe 报的引擎不在产物该在的位置：{kv.get('engine')}")
        elif flavor == "python" and os.path.normcase(bundled_py) not in os.path.normcase(
                kv.get("python", "")):
            # 随包解释器在场却选了宿主 python，等于"零前置"是假的 —— 宿主恰好装了
            # 一个能解 zstd 的 3.14，产物就永远看不出来。
            bad.append(f"--engine-probe 没用随包解释器：python={kv.get('python')} 应为 {bundled_py}")
        elif kv.get("codename") != want_code or kv.get("slug") != want_slug:
            bad.append(f"产物不自认 {want_code}：codename={kv.get('codename')} "
                       f"slug={kv.get('slug')}（publish 少了 -p:ProductCodename/-p:ProductSlug？）")
        else:
            print(f"  OK Vigil --engine-probe：kind={kv['kind']} "
                  f"codename={kv['codename']} slug={kv['slug']}")

    # ②b 反向证据：cpp 那版的载荷里不该再留着解释器那棵树。留着就是 24MB，而且
    # "没有 python" 才是"目标机零前置"这句话的可检查形式。
    if flavor == "cpp":
        leftover = [p for p in glob.glob(os.path.join(app, "runtime", "**", "*"), recursive=True)
                    if os.path.isfile(p)]
        if leftover:
            bad.append(f"载荷里还留着 runtime\\ 那 {len(leftover)} 个文件（C++ 引擎不需要解释器）："
                       + ", ".join(os.path.relpath(x, app) for x in leftover[:4]))

    # ③ 两个 exe 的版本必须相等，且等于这一版在 flavors.py 里的号
    v_app = file_version(exe) if os.path.isfile(exe) else ""
    v_setup = file_version(os.path.join(dist_dir, "Vigil-Setup.exe"))
    print(f"  · Vigil.exe FileVersion={v_app}  Vigil-Setup.exe FileVersion={v_setup}")
    if not v_app or v_app != v_setup:
        bad.append(f"两个 exe 版本不一致：{v_app!r} vs {v_setup!r}")
    elif three_part(v_app) != want_ver:
        bad.append(f"{flavor} 版的版本号应为 {want_ver}，实得 {v_app}")

    # ④ 这一版该随包的许可证原文
    lic = os.path.join(dist_dir, LICENSE_FILE[flavor])
    if not os.path.isfile(lic):
        bad.append(f"缺 {LICENSE_FILE[flavor]}（{flavor} 版的随包义务原文）")

    # ⑤ 载荷不该带开发期的垃圾
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
