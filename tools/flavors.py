#!/usr/bin/env python3
r"""两个并存版本各自的发布参数：一处写，打包、产物校验、冒烟都从这里读。

为什么要有这个文件：版本号/代号/slug 以前只有 bar\Vigil.csproj 那一份默认值，出第二版
就得在 package.cmd 里现场 -p: 覆盖 —— 而"现场覆盖"没有判据。打歪一次的后果不是编译错误，
是 Python 引擎那份装进了 Programs\Vigil-Lilium，用户装了 A 打开是 B，两台机器上都复现不了。
参数收在这里以后，产物还得按同一张表**自证身份**（verify_package.py 跑产物自己的
`--engine-probe`，比的就是这里的 codename/slug/kind）。

  python tools/flavors.py --list                    两个 flavor 的名字
  python tools/flavors.py cpp version               1.1.0
  python tools/flavors.py python codename           Vachellia farnesiana
  python tools/flavors.py python dir                Vigil-1.0.0-vachellia-farnesiana-win-x64

cpp 的 version 就是 bar\Vigil.csproj 的 <Version>（主线版本号，跟着产品走）；
python 那一版钉在 1.0.0 —— 它是"引擎还是 CPython 的那一版"的最后一个编号，
之后主线再怎么涨都不把它带上，两版的数字差本身就是"哪一版是哪一版"的判据。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)          # 让 import read_version 在任意 cwd 下都成立

from read_version import csproj_version

PYTHON_ENGINE_VERSION = "1.0.0"

# engine 那一格是 `Vigil.exe --engine-probe` 里 kind= 的期望值：
# native = 随包的 vigil-engine.exe（C++，自带 zstd）；python = 随包的 app\runtime\python。
FLAVORS: dict[str, dict[str, str]] = {
    "cpp": {"codename": "Lilium", "slug": "Lilium", "engine": "native"},
    "python": {"codename": "Vachellia farnesiana", "slug": "Vachellia", "engine": "python"},
}


def _one(flavor: str) -> dict[str, str]:
    try:
        return FLAVORS[flavor]
    except KeyError:
        print(f"未知 flavor：{flavor!r}（认 {', '.join(FLAVORS)}）", file=sys.stderr)
        raise SystemExit(2)


def version(flavor: str) -> str:
    _one(flavor)          # 未注册的 flavor 必须在这里就炸，不能悄悄退回 Python 那一版
    return csproj_version() if flavor == "cpp" else PYTHON_ENGINE_VERSION


def codename(flavor: str) -> str:
    return _one(flavor)["codename"]


def slug(flavor: str) -> str:
    return _one(flavor)["slug"]


def engine_kind(flavor: str) -> str:
    return _one(flavor)["engine"]


def dist_name(flavor: str, rid: str = "win-x64") -> str:
    """分发目录/压缩包的名字。代号进名字，装出来的两版从目录上就分得开。"""
    return f"Vigil-{version(flavor)}-{codename(flavor).lower().replace(' ', '-')}-{rid}"


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "--list":
        for f in FLAVORS:
            print(f)
        return 0
    if len(argv) != 3:
        print("用法: flavors.py --list | <flavor> <version|codename|slug|engine|dir>",
              file=sys.stderr)
        return 2
    flavor, field = argv[1], argv[2]
    got = {"version": version, "codename": codename, "slug": slug, "engine": engine_kind}
    if field == "dir":
        print(dist_name(flavor))
    elif field in got:
        print(got[field](flavor))
    else:
        print(f"未知字段：{field}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
