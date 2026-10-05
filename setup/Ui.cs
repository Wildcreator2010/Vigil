using System;
using System.Windows;

namespace Vigil.Setup
{
    /// <summary>
    /// 向导界面。本文件当前是**空壳**：只保证静默模式（冒烟走的这条）能编译能跑，
    /// 真正的浅色表单在 Task 3 填进来。空壳期间双击 Vigil-Setup.exe 只会看到一扇白窗。
    /// </summary>
    internal static class Ui
    {
        public static Window InstallWindow(Installer.Options o, Action<string> log)
            => new Window { Title = "Vigil 安装", Width = 520, Height = 360, Background = System.Windows.Media.Brushes.White };

        public static Window UninstallWindow(Installer.Options o, Action<string> log)
            => new Window { Title = "Vigil 卸载", Width = 520, Height = 300, Background = System.Windows.Media.Brushes.White };
    }
}
