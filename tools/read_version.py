#!/usr/bin/env python3
"""读 bar/Vigil.csproj 的 <Version>，供打包脚本当版本单一来源用。

为什么要单独一个脚本：`for /f ... ('python -c "...')` 里那对双引号会被 cmd 的
解析器吃掉一半，调试起来极其费劲。脚本只往 stdout 印一行版本号，别的什么都不印。

两个版本并存以后，"当前这一版是多少"仍只有 csproj 那一处；"Python 引擎那一版
是多少"在 tools/flavors.py。这里只负责读，不负责判 —— flavors.py 反过来 import
本文件的 csproj_version()，两个脚本共用同一条正则。
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CSPROJ = os.path.join(os.path.dirname(HERE), "bar", "Vigil.csproj")


def csproj_version(path: str = CSPROJ) -> str:
    """bar/Vigil.csproj 里的 <Version>；读不到返回空串（调用方按"打包失败"处理）。"""
    m = re.search(r"<Version>([^<]+)</Version>", open(path, encoding="utf-8").read())
    return m.group(1).strip() if m else ""


def main() -> int:
    v = csproj_version()
    if not v:
        print(f"读不到 <Version>：{CSPROJ}", file=sys.stderr)
        return 1
    print(v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
