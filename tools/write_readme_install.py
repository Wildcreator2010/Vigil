#!/usr/bin/env python3
"""生成分发物里的「安装说明.txt」。

用文件写入而不是 cmd 的 echo：`cmd` 默认代码页不是 UTF-8，echo 中文进文件必然乱码。

两版并存以后这份说明**必须按载荷说话**：Vachellia 那一版的引擎是随包的解释器，
Lilium 那一版是 vigil-engine.exe，安装目录与卸载项名字也各带 slug。写一份通用文案
等于给一半用户发错地图 —— 他照说明去找 app\\vigil-engine.exe，找到自己那版里没有，
只会以为包坏了。所以这里读的是**这个目录里实际有什么**（判据与 verify_package.py 同一处）。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import flavors

HEAD = """Vigil 安装说明 · {ver} · {code}

一、把整个文件夹解压出来（不要只在压缩包里双击 Vigil-Setup.exe，那样复制不出载荷）。
二、双击 Vigil-Setup.exe。
   · Windows 可能弹 SmartScreen「Windows 已保护你的电脑」—— 这份产物没有代码签名，
     点「更多信息」→「仍要运行」。
   · 全程不弹 UAC：只写当前用户（%LOCALAPPDATA%\\Programs\\{dir} 与 HKCU）。
三、按提示安装。装完状态栏就出现在任务栏里，托盘图标在通知区。

前提：Win10 1607+ 或 Win11，x64。什么都不用另外装 —— .NET 运行时随自包含载荷走，
状态引擎{engine_line}
不支持 Win7/8（.NET 10 的 WPF 只到 Win10）。

同一台机器可以同时装下两版（{other} 那一版也在），但**同时只能开一版**：两版都启动会抢
同一条任务栏带和同一份设置，所以第二个开起来的会弹一句「已经有一版在运行」，
那个窗口的第一个按钮直接把正在跑的那一版的面板叫出来。设置与余额 Key 是两版共用的
（%LOCALAPPDATA%\\Vigil），换版本不用重新录 Key。开机自启也只有一个位置，装第二版时
向导会问自启交给哪一版，而且随时能在面板里关掉。

命令行/无人值守安装：
    Vigil-Setup.exe /S /D=D:\\{dir}        静默安装到指定目录
    Vigil-Setup.exe /UNINSTALL /S        静默卸载（默认保留设置与余额 Key）

卸载：开始菜单 {dir} 里，或「设置 → 应用 → 显示名「{display}」→ 卸载」。

第一次查余额：余额 Key 是按当前 Windows 账户加密存的（DPAPI），换电脑必须重新录入 ——
右键状态栏 → 设置余额 Key…

排障：
  · 状态栏没出现：看 %LOCALAPPDATA%\\Vigil\\bar.log；安装过程看 setup.log。
  · 想知道它认的是哪个引擎：Vigil.exe --engine-probe
    （这一版应输出 kind={kind}{expect}）。
  · 想单独跑状态引擎：{solo}
    · --json 出一行机器可读快照，--watch 常驻每 2 秒一行 NDJSON，--states 打印状态图例。
{fallback}"""

CPP = dict(
    engine_line="是 app\\vigil-engine.exe（C++，解压会话文件的能力编在它自己怀里，"
                "不需要 Python）。",
    kind="native",
    expect=" 与 app\\vigil-engine.exe",
    solo="app\\vigil-engine.exe --pretty",
    fallback="    · 回退级（vigil-engine.exe 缺失时程序自己会走）："
             "python -X utf8 app\\dsh_state.py --pretty —— 需要本机装有 Python 3.14。",
)
PYTHON = dict(
    engine_line="是 app\\runtime\\python 里那份随包的 CPython，跑 app\\dsh_state.py —— "
                "所以本机装了 Python 也用不着，包里的解释器优先。",
    kind="python",
    expect=" 与 app\\runtime\\python\\python.exe",
    solo="app\\runtime\\python\\python.exe -X utf8 app\\dsh_state.py --pretty",
    fallback="    · 这一版的引擎就是那份解释器；它不在（被杀软删过、或解压不完整）时"
             "才会退回本机 PATH 上的 Python 3.14。",
)


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: write_readme_install.py <分发物目录>")
        return 2
    dist = os.path.abspath(sys.argv[1])
    # 中文文件名由这里拼，不由 package.cmd 传：cmd 按 OEM 代码页解析批处理，
    # 把「安装说明.txt」写进 .cmd 再传给子进程就是一串乱码文件名。
    out = os.path.join(dist, "安装说明.txt")
    flavor = "cpp" if os.path.isfile(os.path.join(dist, "app", "vigil-engine.exe")) else "python"
    f = flavors.slug(flavor)
    text = HEAD.format(ver=flavors.version(flavor), code=flavors.codename(flavor),
                       dir="Vigil-" + f, display="Vigil " + flavors.codename(flavor),
                       other="Lilium" if flavor == "python" else "Vachellia farnesiana",
                       **({"cpp": CPP, "python": PYTHON}[flavor]))
    with open(out, "w", encoding="utf-8-sig", newline="\r\n") as fh:
        fh.write(text)
    print(f"  OK 已写 {out}（{flavor} 版）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
