#!/usr/bin/env python3
"""打印 bar/Vigil.csproj 里的 <Version>，供 package.cmd 当版本单一来源用。

为什么要单独一个脚本：`for /f ... ('python -c "...')` 里那对双引号会被 cmd 的
解析器吃掉一半，调试起来极其费劲。脚本只往 stdout 印一行版本号，别的什么都不印。
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CSPROJ = os.path.join(os.path.dirname(HERE), "bar", "Vigil.csproj")


def main() -> int:
    text = open(CSPROJ, encoding="utf-8").read()
    m = re.search(r"<Version>([^<]+)</Version>", text)
    if not m:
        print(f"读不到 <Version>：{CSPROJ}", file=sys.stderr)
        return 1
    print(m.group(1).strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
