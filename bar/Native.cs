using System;
using System.Runtime.InteropServices;
using System.Text;

namespace Vigil
{
    [StructLayout(LayoutKind.Sequential)]
    public struct Rect
    {
        public int Left, Top, Right, Bottom;
        public int Width => Right - Left;
        public int Height => Bottom - Top;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct Point
    {
        public int X, Y;
    }

    /// <summary>
    /// 任务栏停靠层。顺序很关键：先把样式改成 WS_CHILD，再 SetParent，
    /// 然后用父（任务栏）客户区坐标落位。窗口还必须带 WS_EX_LAYERED
    /// （由 WPF AllowsTransparency 提供），否则像素不会合成到任务栏的 XAML 层之上。
    /// </summary>
    internal static class Native
    {
        public const int GWL_STYLE = -16;
        public const int GWL_EXSTYLE = -20;
        public const int WS_POPUP = unchecked((int)0x80000000);
        public const int WS_CHILD = 0x40000000;
        public const int WS_VISIBLE = 0x10000000;
        public const uint SWP_NOSIZE = 0x0001, SWP_NOMOVE = 0x0002;
        public const uint SWP_NOZORDER = 0x0004;
        public const uint SWP_NOACTIVATE = 0x0010;
        public const uint SWP_SHOWWINDOW = 0x0040;
        public const uint SWP_HIDEWINDOW = 0x0080;

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        public static extern IntPtr FindWindow(string className, string windowName);

        // ---- 系统背衬（Mica / Acrylic）----------------------------------------------------
        //
        // 为什么绕开 WPF-UI 的 FluentWindow.WindowBackdropType：那个属性硬性要求先
        // `ExtendsContentIntoTitleBar=true`，否则在**赋值当场**抛 InvalidOperationException
        // （WPF-UI 4.2 实测：extends=false 时 Mica 和 Acrylic 都抛，只有 None 能过）。
        // 而控制台用的是标准系统标题栏，改成自绘标题栏会把整页像素口径挪位。
        // OS 层并没有这个前提：DwmSetWindowAttribute 在保留标准标题栏的窗口上实测返回 S_OK。
        //
        // DWMWA_SYSTEMBACKDROP_TYPE 是 Windows 11 22H2 起的；Win10 与更早的 Win11 会返回
        // 失败 HRESULT，调用方按"没有材质、窗口照常可用"处理（本项目支持面下限是 Win10 1607）。
        public const int DWMWA_SYSTEMBACKDROP_TYPE = 1025;
        public const int DWMSBT_NONE = 1;
        public const int DWMSBT_MAINWINDOW = 2;       // Mica
        public const int DWMSBT_TRANSIENTWINDOW = 3;  // Acrylic

        [DllImport("dwmapi.dll")]
        static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int attrValue, int attrSize);

        /// <summary>返回 HRESULT（0 = 生效）。窗口还没 HWND 时别调。</summary>
        public static int SetSystemBackdrop(IntPtr hwnd, int value)
        {
            int v = value;
            return DwmSetWindowAttribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, ref v, 4);
        }

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        public static extern IntPtr FindWindowEx(IntPtr parent, IntPtr childAfter, string className, string windowName);

        [DllImport("user32.dll")]
        public static extern bool GetWindowRect(IntPtr hWnd, out Rect rect);

        [DllImport("user32.dll")]
        public static extern IntPtr SetParent(IntPtr child, IntPtr newParent);

        [DllImport("user32.dll", EntryPoint = "GetWindowLongW")]
        public static extern int GetWindowLong(IntPtr hWnd, int index);

        [DllImport("user32.dll", EntryPoint = "SetWindowLongW")]
        public static extern int SetWindowLong(IntPtr hWnd, int index, int value);

        [DllImport("user32.dll")]
        public static extern bool SetWindowPos(IntPtr hWnd, IntPtr insertAfter, int x, int y, int cx, int cy, uint flags);

        [DllImport("user32.dll")]
        public static extern bool ScreenToClient(IntPtr hWnd, ref Point point);

        [DllImport("user32.dll")]
        public static extern bool IsWindowVisible(IntPtr hWnd);

        [DllImport("user32.dll")]
        public static extern IntPtr WindowFromPoint(Point point);

        [DllImport("user32.dll")]
        public static extern int GetClassName(IntPtr hWnd, StringBuilder sb, int max);

        [DllImport("user32.dll")]
        public static extern bool SetForegroundWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        public static extern bool ShowWindowAsync(IntPtr hWnd, int cmdShow);

        [DllImport("user32.dll")]
        public static extern bool DestroyIcon(IntPtr hIcon);

        [DllImport("user32.dll")]
        public static extern bool SetProcessDpiAwarenessContext(IntPtr context);

        [DllImport("user32.dll")]
        public static extern bool GetCursorPos(out Point point);

        public static string ClassName(IntPtr hWnd)
        {
            if (hWnd == IntPtr.Zero) return "";
            var sb = new StringBuilder(256);
            GetClassName(hWnd, sb, 256);
            return sb.ToString();
        }

        public static Rect Taskbar()
        {
            IntPtr tb = FindWindow("Shell_TrayWnd", null);
            GetWindowRect(tb, out Rect r);
            return r;
        }

        public static IntPtr TaskbarHandle() => FindWindow("Shell_TrayWnd", null);

        private static Rect ChildRect(IntPtr parent, string className)
        {
            IntPtr h = FindWindowEx(parent, IntPtr.Zero, className, null);
            if (h == IntPtr.Zero) return default;
            GetWindowRect(h, out Rect r);
            return r;
        }

        /// <summary>
        /// 任务栏带内可放状态栏的水平区间：优先开始按钮左侧，其次应用图标组与托盘之间。
        /// </summary>
        public static (int left, int right) FreeRange(Rect band, int minWidth)
        {
            IntPtr tb = TaskbarHandle();
            int bandRight = band.Right;
            var start = ChildRect(tb, "Start");
            var cluster = ChildRect(tb, "MSTaskSwWClass");
            var tray = ChildRect(tb, "TrayNotifyWnd");
            if (tray.Width > 20) bandRight = Math.Min(bandRight, tray.Left);

            int startLeft = start.Width > 0 ? start.Left : band.Left + 600;
            if (cluster.Width > 0) startLeft = Math.Min(startLeft, cluster.Left);
            int left = band.Left + 8;
            int right = startLeft - 8;
            if (right - left >= minWidth) return (left, right);

            int afterIcons = cluster.Width > 0 ? cluster.Right + 8 : band.Left + 8;
            if (bandRight - afterIcons >= minWidth) return (afterIcons, bandRight);
            return (left, Math.Max(left + minWidth, bandRight));
        }

        public static void Dock(IntPtr hwnd, IntPtr taskbar)
        {
            int style = GetWindowLong(hwnd, GWL_STYLE);
            SetWindowLong(hwnd, GWL_STYLE, (style & ~WS_POPUP) | WS_CHILD);
            SetParent(hwnd, taskbar);
        }

        public static void Place(IntPtr hwnd, IntPtr taskbar, Rect screenRect)
        {
            var p = new Point { X = screenRect.Left, Y = screenRect.Top };
            ScreenToClient(taskbar, ref p);
            SetWindowPos(hwnd, IntPtr.Zero, p.X, p.Y, screenRect.Width, screenRect.Height,
                SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW);
        }

        public static void Undock(IntPtr hwnd)
        {
            if (hwnd == IntPtr.Zero) return;
            SetWindowPos(hwnd, IntPtr.Zero, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_HIDEWINDOW);
            SetParent(hwnd, IntPtr.Zero);
            int style = GetWindowLong(hwnd, GWL_STYLE);
            SetWindowLong(hwnd, GWL_STYLE, (style & ~WS_CHILD) | WS_POPUP);
        }
    }
}
