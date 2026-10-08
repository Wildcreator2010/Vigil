"""量打包变体的**体积**与**冷启动**，一把尺子比着看。

为什么需要它：'用 C++ 重构能省多少' 这种问题不该靠感觉回答。185MB 的自包含分发物里，
CPython 只占 24MB，剩下是托管运行时 —— 到底是哪一刀有效，得量出来。那一刀之后
载荷里已经没有解释器了（引擎换成 vigil-engine.exe，0.5MB），这把尺子仍然成立，
但**必须把引擎拷进每个变体的产物**：`dotnet publish` 不会带它（csproj 里没这条，
package.cmd 是手工 copy 的），不拷的话量的就是"找不到 exe → 退到本机 python"
那条回退路，与用户实际拿到的东西不是同一件事。

顺带这把尺子还会报一条真缺陷：每个变体先跑一次 `Vigil.exe --engine-probe` 并断言
`kind=native`。单文件变体的 `AppContext.BaseDirectory` 是解包临时目录，
"随包那颗 exe 还找不找得到"在那里是个未知数 —— 量之前先问，别等用户遇到。

用法（在仓库根跑）:
    python tools/measure_package.py                 # 全部变体
    python tools/measure_package.py base r2r        # 只跑指定的

变体只是**发布参数**的不同，产品代码一行不动，所以任何一次数字变化都能归因到参数上。
产物落在 dist-measure/，不进版本库。

冷启动量的是「进程起来 → 状态栏真的停靠进 Shell_TrayWnd」，这是用户眼里"它活了"的时刻。
每个变体跑 N 次取中位数与最差值：单次数在这台机器上没有意义。
"""
from __future__ import annotations

import os
import shutil
import statistics
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import smoke_test as st          # noqa: E402  复用停靠检测与 settings 隔离

RID = "win-x64"
OUT_ROOT = os.path.join(HERE, "dist-measure")
RUNS = 5                        # 冷启动重复次数
SETTLE = 0.35                   # 轮询间隔（秒）
TIMEOUT = 45.0                  # 单次冷启动上限
ENGINE = os.path.join(HERE, "engine", "vigil-engine.exe")

VARIANTS = {
    # package.cmd 现在这一条：自包含、不裁剪、不 R2R
    "base": [],
    # 预编译成原生机器码：换启动时间，代价是发布更慢、体积略涨
    "r2r": ["-p:PublishReadyToRun=true"],
    # 单文件 + 自解压时压缩：换磁盘/分发体积
    "single": ["-p:PublishSingleFile=true",
               "-p:IncludeNativeLibrariesForSelfExtract=true",
               "-p:EnableCompressionInSingleFile=true"],
    # 两者一起
    "r2r+single": ["-p:PublishReadyToRun=true",
                   "-p:PublishSingleFile=true",
                   "-p:IncludeNativeLibrariesForSelfExtract=true",
                   "-p:EnableCompressionInSingleFile=true"],
}


def tree_bytes(path: str) -> int:
    total = 0
    for _r, _d, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(path, f))
            except OSError:
                pass
    return total


def mb(n: int) -> str:
    return f"{n / 1024 / 1024:8.1f}MB"


def publish(variant: str, extra: list[str]) -> str | None:
    out = os.path.join(OUT_ROOT, variant)
    if os.path.isdir(out):
        shutil.rmtree(out, ignore_errors=True)
    ver = subprocess.run([sys.executable, "-X", "utf8", os.path.join(HERE, "tools", "read_version.py")],
                         capture_output=True, text=True, cwd=HERE).stdout.strip()
    args = [os.path.join(HERE, "bar", "Vigil.csproj"), "-c", "Release", "-r", RID,
            "--self-contained", "true", f"-p:Version={ver or '1.0.0'}",
            "-o", out, "--nologo"] + extra
    t0 = time.time()
    log = out + ".log"
    os.makedirs(OUT_ROOT, exist_ok=True)
    with open(log, "wb") as fh:
        r = subprocess.run(["dotnet", "publish", *args], stdout=fh, stderr=subprocess.STDOUT, cwd=HERE)
    dt = time.time() - t0
    if r.returncode != 0:
        print(f"  [FAIL] {variant} 发布失败（{dt:.0f}s），见 {os.path.basename(log)}")
        tail = open(log, encoding="utf-8", errors="replace").read().splitlines()[-6:]
        for line in tail:
            print("      " + line[:160])
        return None
    print(f"  发布耗时 {dt:.0f}s")
    return out


def zip_size(out: str) -> int:
    """按 deflate 压一遍，近似分发 zip 的量级（和 package.cmd 的 bsdtar 不逐字节同，
    但比的是变体之间的相对差，够用）。"""
    zp = out + ".measure.zip"
    if os.path.isfile(zp):
        os.remove(zp)
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _d, fs in os.walk(out):
            for f in fs:
                p = os.path.join(root, f)
                z.write(p, os.path.relpath(p, out))
    n = os.path.getsize(zp)
    os.remove(zp)
    return n


def ship_engine(out: str) -> bool:
    """把引擎拷进这一份变体产物，与 package.cmd 的 [2/6] 同一件事。

    csproj 不带它（那是 engine\\build.cmd 的产物，进 bin 会把"没编引擎"变成
    构建失败），所以 publish 出来的目录里**没有** vigil-engine.exe —— 不补这一步，
    量的每一条冷启动走的都是回退级。
    """
    if not os.path.isfile(ENGINE):
        print("  [跳过] 没有 engine/vigil-engine.exe：先跑 engine/build.cmd，"
              "否则这份产物量的不是用户拿到的东西")
        return False
    shutil.copy2(ENGINE, os.path.join(out, "vigil-engine.exe"))
    return True


def probe_kind(exe: str) -> str:
    """跑一次产物自己的 `--engine-probe`，返回 kind（native / python / none / ?）。"""
    try:
        p = subprocess.run([exe, "--engine-probe"], capture_output=True, text=True,
                           errors="replace", timeout=180, cwd=os.path.dirname(exe))
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"  [探针失败] {type(exc).__name__}: {exc}")
        return "?"
    kv = dict(l.split("=", 1) for l in (p.stdout or "").splitlines() if "=" in l)
    return kv.get("kind", "?")


def cold_start(exe: str) -> float:
    """进程起来到状态栏停靠进任务栏的秒数；超时返回 TIMEOUT。"""
    st.subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
    time.sleep(1.0)
    t0 = time.time()
    proc = subprocess.Popen([exe], cwd=os.path.dirname(exe))
    try:
        while time.time() - t0 < TIMEOUT:
            if proc.poll() is not None:
                return -1.0                     # 进程自己退了：这一版坏了
            if st.find_bar_windows():
                return time.time() - t0
            time.sleep(SETTLE)
        return TIMEOUT
    finally:
        proc.terminate()
        st.subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
        time.sleep(0.8)


def main() -> int:
    wanted = [a for a in sys.argv[1:] if not a.startswith("--")] or list(VARIANTS)
    bad = [w for w in wanted if w not in VARIANTS]
    if bad:
        print(f"未知变体 {bad}，可选：{', '.join(VARIANTS)}")
        return 2
    if not shutil.which("dotnet"):
        print("PATH 里没有 dotnet")
        return 1

    settings = os.path.join(st.ds.state_dir(), "settings.json")
    backup = open(settings, encoding="utf-8").read() if os.path.isfile(settings) else None
    rows = []
    try:
        # 冷启动要求状态条真的会停靠，也要排除通知弹窗抢时间
        st.write_settings(theme="light", backdrop="mica", showBar=True,
                          notify=False, interval=2, repeatSec=0)
        for v in wanted:
            print(f"\n== {v} ==")
            out = publish(v, VARIANTS[v])
            if not out:
                continue
            if not ship_engine(out):
                continue
            raw = tree_bytes(out)
            files = sum(len(fs) for _r, _d, fs in os.walk(out))
            zipped = zip_size(out)
            exe = os.path.join(out, "Vigil.exe")
            kind = probe_kind(exe)
            if kind != "native":
                # 这条不是装饰：单文件变体的 BaseDirectory 是解包临时目录，
                # 随包那颗 exe 在那里找不找得到是未知的。kind 不是 native，
                # 下面那组冷启动数字量的就不是用户会走的那条路。
                print(f"  [警告] 这份变体的产物认的不是随包引擎（kind={kind}）："
                      "冷启动数字与本仓库的分发形状不可比")
            times = [t for t in (cold_start(exe) for _ in range(RUNS)) if t >= 0]
            print(f"  载荷 {mb(raw)} / {files} 个文件，zip 后 {mb(zipped)}，引擎={kind}")
            if times:
                print(f"  冷启动（→ 状态栏停靠）中位 {statistics.median(times)*1000:.0f}ms"
                      f"，最差 {max(times)*1000:.0f}ms，样本 {len(times)}/{RUNS}")
            rows.append((v, raw, zipped, kind,
                         statistics.median(times) * 1000 if times else float("nan")))
            # 每个变体 ~162MB（自包含运行时 + 0.5MB 引擎），四个就是 650MB。
            # 本机磁盘只剩几十 G，量完即删；要留着看产物加 --keep。
            if "--keep" not in sys.argv:
                shutil.rmtree(out, ignore_errors=True)
                for leftover in (out + ".log",):
                    if os.path.isfile(leftover):
                        os.remove(leftover)
    finally:
        st.subprocess.run(["taskkill", "/f", "/im", "Vigil.exe"], capture_output=True)
        # 引擎子进程在父进程被强杀后是靠管道断裂自退的（README 承诺，且
        # compare_watch.orphan_check 钉着）；这里不等它，直接收 —— 量测段每次都
        # 起一个新 Vigil，残留会一路叠，下一轮的"无残留"就不再是证据。
        st.subprocess.run(["taskkill", "/f", "/im", "vigil-engine.exe"], capture_output=True)
        if backup is not None:
            with open(settings, "w", encoding="utf-8") as fh:
                fh.write(backup)
        elif os.path.isfile(settings):
            os.remove(settings)

    if len(rows) > 1:
        base = next((r for r in rows if r[0] == "base"), rows[0])
        print("\n== 对照 base ==")
        print(f"{'变体':<12}{'载荷':>10}{'zip':>10}{'冷启动':>10}{'vs base':>26}")
        for v, raw, zipped, kind, ms in rows:
            print(f"{v:<12}{raw / 1048576:9.1f}M{zipped / 1048576:9.1f}M{ms:9.0f}ms"
                  f"   载荷{(raw - base[1]) / 1048576:+7.1f}M  启动{ms - base[4]:+7.0f}ms"
                  f"  引擎={kind}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
