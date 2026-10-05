#!/usr/bin/env python3
"""把随产物分发的第三方许可证原文收进 `dist/<name>/licenses/`。

为什么需要这一步：`--self-contained` 的载荷里不只是我们的代码 —— 它把 .NET 运行时的
二进制一起分发了，而那份 MIT 要求"所有副本或实质部分都保留版权声明"。Python 同理，
embeddable 包里的 `LICENSE.txt` 是 PSF 许可全文。只在本仓库的声明文件里写一句
"人家是 MIT" 不满足这个条件，原文必须跟着产物走。

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

    py_lic = os.path.join(dist, "app", "runtime", "python", "LICENSE.txt")
    if os.path.isfile(py_lic):
        with open(py_lic, encoding="utf-8", errors="replace") as f:
            body = f.read()
        with open(os.path.join(out, "python-PSF.txt"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write(body)
        lines.append("python-PSF.txt\n  组件：CPython 3.14.7 embeddable（app\\runtime\\python）\n"
                     "  许可证：Python Software Foundation License\n"
                     "  同一份原文也随附在 app\\runtime\\python\\LICENSE.txt\n")
        print("  OK python-PSF.txt  <- app\\runtime\\python\\LICENSE.txt")
    else:
        missing.append("app\\runtime\\python\\LICENSE.txt（embeddable 包解压缺失）")

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
