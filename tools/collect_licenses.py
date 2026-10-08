#!/usr/bin/env python3
"""把随产物分发的第三方许可证原文收进 `dist/<name>/licenses/`。

为什么需要这一步：`--self-contained` 的载荷里不只是我们的代码 —— 它把 .NET 运行时的
二进制一起分发了，而那份 MIT 要求"所有副本或实质部分都保留版权声明"。
`vigil-engine.exe` 同理：它把 vendored 的 zstd 解压子集编在了自己怀里，BSD-3 的
版权声明要跟着走。只在本仓库的声明文件里写一句"人家是 MIT/BSD"不满足这个条件，
原文必须跟着产物走。

  python tools/collect_licenses.py <dist 目录>

从 NuGet 缓存里取（版本取缓存里最高的那个），取不到就非 0 退出 —— 宁可打包失败，
也不要发出去一个缺许可证的包。
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG_DIR = os.path.join(os.path.expanduser("~"), ".nuget", "packages")

# (NuGet 包名, 输出文件名, 声明里怎么称呼它)
DOTNET_PACKS = (
    ("microsoft.netcore.app.runtime.win-x64", "dotnet-runtime-MIT.txt",
     ".NET Runtime 10（自包含载荷里的 coreclr / System.* 程序集）"),
    ("microsoft.windowsdesktop.app.runtime.win-x64", "dotnet-windowsdesktop-MIT.txt",
     ".NET 10 Windows Desktop Runtime（WPF / WinForms 程序集）"),
)


def newest_license(pkg: str) -> tuple[str, str] | None:
    """返回 (版本号, 许可证文本)。LICENSE.TXT 和 LICENSE 两种命名都认。"""
    base = os.path.join(PKG_DIR, pkg)
    if not os.path.isdir(base):
        return None

    def key(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in re.findall(r"\d+", v)) or (0,)

    for ver in sorted(os.listdir(base), key=key, reverse=True):
        for name in ("LICENSE.TXT", "LICENSE", "LICENSE.txt"):
            p = os.path.join(base, ver, name)
            if os.path.isfile(p):
                with open(p, encoding="utf-8", errors="replace") as f:
                    return ver, f.read()
    return None


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: collect_licenses.py <dist 目录>")
        return 2
    dist = os.path.abspath(sys.argv[1])
    out = os.path.join(dist, "licenses")
    os.makedirs(out, exist_ok=True)
    missing: list[str] = []
    lines = ["本目录收录随 Vigil 分发物一起提供的第三方许可证原文。",
             "Vigil 自身的 MIT 许可证在上一级的 LICENSE.txt。", ""]

    for pkg, fname, role in DOTNET_PACKS:
        got = newest_license(pkg)
        if not got:
            missing.append(f"{pkg}（NuGet 缓存里没有：{PKG_DIR}）")
            continue
        ver, text = got
        with open(os.path.join(out, fname), "w", encoding="utf-8", newline="\r\n") as f:
            f.write(text)
        lines.append(f"{fname}\n  组件：{role}\n  来源包：{pkg} {ver}\n  许可证：MIT\n")
        print(f"  OK {fname}  <- {pkg} {ver}")

    # zstd 的原文从**仓库里那份 vendored 源码**取，而不是从 THIRD-PARTY-NOTICES 抄：
    # 声明文件是人写的摘要，摘要会漂；随包分发的那 11 个 .c 自己带的 LICENSE 才是凭据。
    # 上一版这里收的是 embeddable 解释器的 PSF 全文 —— 载荷不再带 CPython，
    # 那条义务跟着走；换成编译进 vigil-engine.exe 的解压子集。
    zsrc = os.path.join(ROOT, "engine", "third_party", "zstd", "LICENSE")
    if os.path.isfile(zsrc):
        with open(zsrc, encoding="utf-8", errors="replace") as f:
            body = f.read()
        with open(os.path.join(out, "zstd-BSD.txt"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write(body)
        lines.append("zstd-BSD.txt\n  组件：libzstd 解压子集（编进 app\\vigil-engine.exe）\n"
                     "  来源：engine\\third_party\\zstd，原文即本文件\n"
                     "  许可证：BSD-3-Clause（与 Apache-2.0 双许可，本项目取 BSD-3）\n")
        print("  OK zstd-BSD.txt  <- engine\\third_party\\zstd\\LICENSE")
    else:
        missing.append("engine\\third_party\\zstd\\LICENSE（vendored zstd 的原文，随包分发义务）")

    with open(os.path.join(out, "README.txt"), "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write("\n".join(lines))

    if missing:
        for m in missing:
            print(f"  FAIL 缺 {m}")
        return 1
    print(f"  OK 许可证原文已收进 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
