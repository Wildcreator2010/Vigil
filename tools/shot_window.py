"""把真窗口的「Vigil 控制台」抓到前台并截屏，用于人眼复核外壳观感（背衬、圆角、侧栏）。

离屏的 `--panel-shot` 只画内容列，DWM 的 Mica/Acrylic 背衬与圆角它看不见，
所以外壳是不是「深色壳 + 白内容」这种拼缝，只能靠真窗口截屏。

用法: python tools/shot_window.py <out.png> [page]
本环境的坑：前台锁会拒绝跨进程 SetForegroundWindow，且窗口被别家盖住时 DWM 不重新合成，
所以要 Alt 键 + AttachThreadInput 反复抢，抢到才算数。
"""
import ctypes
import ctypes.wintypes as W
import os
import subprocess
import sys
import time

from PIL import ImageGrab

TITLE = "Vigil 控制台"
EXE = os.path.join("bar", "bin", "Release", "net10.0-windows", "Vigil.exe")
SW_RESTORE = 9
WM_CLOSE = 0x0010
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
DWMWA_CLOAKED = 14

u = ctypes.windll.user32
k = ctypes.windll.kernel32
dwm = ctypes.windll.dwmapi

# 必须先声明 DPI 感知：本机 125% 缩放下，不感知的进程拿到的 GetWindowRect 是**虚拟坐标**
# （物理/1.25），照它去 ImageGrab 会在图四周留下 72×34 的黑边、右边还切掉一列——
# 那是取样口径错位，不是产品有暗角。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
except Exception:
    u.SetProcessDPIAware()


def find_panel(timeout=20.0):
    hwnd = 0
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.25)
        hwnd = u.FindWindowW(None, TITLE)
        if hwnd and u.IsWindowVisible(hwnd):
            return hwnd
    return 0


def steal_foreground(hwnd, tries=6):
    """抢前台。返回是否真的抢到——没抢到时截到的是盖在上面的别家窗口。

    先试 SwitchToThisWindow（不按键）；Alt 那招留作兜底，它会把开始菜单/搜索也弹出来
    糊在截图上，实测坑过一次。
    """
    for i in range(tries):
        if i:
            u.keybd_event(0x12, 0, 0, 0)          # Alt 按下
            u.keybd_event(0x12, 0, 2, 0)          # Alt 抬起：解除前台锁
        fg = u.GetForegroundWindow()
        cur = k.GetCurrentThreadId()
        fg_t = u.GetWindowThreadProcessId(fg, 0)
        u.AttachThreadInput(fg_t, cur, True)
        u.ShowWindow(hwnd, SW_RESTORE)
        u.BringWindowToTop(hwnd)
        u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        if i == 0:
            u.SwitchToThisWindow(hwnd, True)
        u.SetForegroundWindow(hwnd)
        u.SetActiveWindow(hwnd)
        u.AttachThreadInput(fg_t, cur, False)
        time.sleep(0.9)
        if u.GetForegroundWindow() == hwnd:
            return True
        u.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    return False


def capture(out, page="overview"):
    proc = subprocess.Popen([EXE, "--panel", page], cwd=os.getcwd())
    hwnd = 0
    try:
        hwnd = find_panel()
        if not hwnd:
            raise SystemExit("找不到窗口：" + TITLE)
        got = steal_foreground(hwnd)
        time.sleep(3.5)  # 等背衬合成 + 引擎第一帧快照（概览页要等 python 起来）
        cloaked = ctypes.c_int(0)
        dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), 4)
        rect = W.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(rect))
        img = ImageGrab.grab(
            bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True
        )
        img.save(out)
        print("saved", out, img.size)
        print("foreground_is_panel", got, "cloaked", cloaked.value,
              "rect", rect.left, rect.top, rect.right, rect.bottom)
        if not got:
            print("警告：没抢到前台，截到的可能是盖在上面的窗口")
    finally:
        if hwnd:
            u.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        time.sleep(1.5)
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    capture(sys.argv[1] if len(sys.argv) > 1 else "window.png",
            sys.argv[2] if len(sys.argv) > 2 else "overview")
