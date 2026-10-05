#!/usr/bin/env python3
"""生成分发物里的「安装说明.txt」。

用文件写入而不是 cmd 的 echo：`cmd` 默认代码页不是 UTF-8，echo 中文进文件必然乱码。
"""
from __future__ import annotations

import os
import sys

TEXT = """Vigil 安装说明

一、把整个文件夹解压出来（不要只在压缩包里双击 Vigil-Setup.exe，那样复制不出载荷）。
二、双击 Vigil-Setup.exe。
   · Windows 可能弹 SmartScreen「Windows 已保护你的电脑」—— 这份产物没有代码签名，
     点「更多信息」→「仍要运行」。
   · 全程不弹 UAC：只写当前用户（%LOCALAPPDATA%\\Programs\\Vigil 与 HKCU）。
三、按提示安装。装完状态栏就出现在任务栏里，托盘图标在通知区。

前提：Win10 1607+ 或 Win11，x64。什么都不用另外装 —— .NET 运行时和解会话文件用的
Python 都随附在 app\\runtime\\python 里。不支持 Win7/8（.NET 10 的 WPF 只到 Win10）。

命令行/无人值守安装：
    Vigil-Setup.exe /S /D=D:\\Vigil        静默安装到指定目录
    Vigil-Setup.exe /UNINSTALL /S        静默卸载（默认保留设置与余额 Key）

卸载：开始菜单 Vigil 目录里，或「设置 → 应用 → Vigil → 卸载」。

第一次查余额：余额 Key 是按当前 Windows 账户加密存的（DPAPI），换电脑必须重新录入 ——
右键状态栏 → 设置余额 Key…。

排障：
  · 状态栏没出现：看 %LOCALAPPDATA%\\Vigil\\bar.log；安装过程看 setup.log。
  · 想知道它认的是哪个 Python：Vigil.exe --engine-probe
    （正常应输出 app\\runtime\\python\\python.exe）。
  · 想单独用状态引擎：
    app\\runtime\\python\\python.exe app\\dsh_state.py --pretty
"""


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: write_readme_install.py <分发物目录>")
        return 2
    # 中文文件名由这里拼，不由 package.cmd 传：cmd 按 OEM 代码页解析批处理，
    # 把「安装说明.txt」写进 .cmd 再传给子进程就是一串乱码文件名。
    out = os.path.join(sys.argv[1], "安装说明.txt")
    with open(out, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(TEXT)
    print(f"  OK 已写 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
