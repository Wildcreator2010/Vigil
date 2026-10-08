"""四个对照器共用的前置检查：二进制必须存在，而且必须比源码新。

为什么要管新鲜度：build.cmd 失败时旧 exe 原封不动留在 engine\\ 里，对照器
拿着它照样一路报绿。这件事真发生过一次 —— 把 Round1 改坏触发 C4505 编译
失败，四个对照器全绿，测的其实是上一次成功构建的那个 exe。变异测试的"红"
和产品代码的"绿"共用同一个入口，入口不能只看文件在不在。
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "vigil-engine.exe")
SRC = os.path.join(HERE, "vigil_engine.cpp")


def require_exe() -> str:
    """返回 exe 路径；不满足"已构建且不比源码旧"就直接退出进程。"""
    if not os.path.isfile(EXE):
        print("先编引擎：engine/build.cmd")
        raise SystemExit(2)
    src_t = os.path.getmtime(SRC)
    exe_t = os.path.getmtime(EXE)
    if src_t > exe_t:
        print(f"✗ vigil-engine.exe 比 vigil_engine.cpp 旧（exe {exe_t:.0f} < 源码 {src_t:.0f}）。\n"
              "  现在对照的是上一个二进制 —— 要么最近一次构建没成功，要么改完源码没重编。\n"
              "  重跑 engine/build.cmd 再看结论。")
        raise SystemExit(2)
    # zstd 解码器也参与同一份二进制：源码换了没重编同样会得出假绿，但对照器
    # 只测判定逻辑，压缩层出问题表现为"读不到会话"，那时上面的检查就够看了。
    return EXE
