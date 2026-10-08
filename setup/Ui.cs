using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;

namespace Vigil.Setup
{
    /// <summary>
    /// 向导的界面：纯 C# 构建的 WPF，浅色白底黑字，文案全中文。
    ///
    /// 三件不成样子但必须记住的事：
    ///  ① 窗口标题恒为「Vigil 安装」/「Vigil 卸载」，安装状态只改进页副标题和主按钮文案。
    ///     标题跟着状态变的话，脚本与排障的人就找不到这扇窗了（冒烟的 setup_window_dump 认标题）。
    ///  ② 复制 185MB 要几十秒，一定放在 DispatcherPriority.Background 里跑；写在点击事件
    ///     本体里窗口会画成「未响应」，人以为装死了就强杀，留下半截目录。
    ///  ③ 不用 WPF-UI（lepo.co）：那个包没有 net48 目标。这里要的就是几个基础控件，
    ///     Fluent 观感不值得为它把向导拖回 .NET 10 自包含。
    /// </summary>
    internal static class Ui
    {
        static readonly Brush Ink = Brushes.Black;
        static readonly Brush Dim = new SolidColorBrush(Color.FromRgb(0x6B, 0x6F, 0x73));
        static readonly Brush Line = new SolidColorBrush(Color.FromRgb(0xD9, 0xDC, 0xE1));
        static readonly Brush Paper = Brushes.White;
        static readonly Brush Accent = new SolidColorBrush(Color.FromRgb(0xD8, 0x7D, 0x44));

        public static Window InstallWindow(Installer.Options o, Action<string> log)
        {
            var dirLabel = new TextBlock { Text = "安装目录", FontSize = 12, Foreground = Ink, Margin = new Thickness(0, 0, 0, 4) };
            var dirBox = new TextBox { Text = o.TargetDir, MinWidth = 380, FontSize = 13, VerticalAlignment = VerticalAlignment.Bottom };
            var browse = new Button { Content = "浏览…", Margin = new Thickness(8, 0, 0, 0), Padding = new Thickness(10, 4, 10, 4), VerticalAlignment = VerticalAlignment.Bottom };
            var dirRow = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 0, 14) };
            dirRow.Children.Add(new StackPanel { Children = { dirLabel, dirBox } });
            dirRow.Children.Add(browse);

            var auto = new CheckBox { Content = "开机自动启动", IsChecked = true, Foreground = Ink, Margin = new Thickness(0, 0, 0, 6) };
            // 一台机器上只有一个开机自启槽（两版各写一条 = 开机两版都启动，第二版
            // 必然弹「同时只能开一个」，用户一开机就被骚扰）。所以槽已被别的版本占着时，
            // 这里必须**说出来**并且默认不勾 —— 不打招呼就把别人的自启拨走是错的。
            TextBlock autoNotice = null;
            string owner = Installer.AutostartOwner();
            if (owner.Length > 0 && !Installer.RunPointsAtTarget(owner, o.TargetDir))
            {
                auto.IsChecked = false;
                auto.Content = "开机自动启动（改由本版本负责）";
                autoNotice = new TextBlock
                {
                    Text = "这台机器上已经有一个 Vigil 注册了开机自启：" + owner
                           + "\n自启槽只有一个，勾上就是把自启交给正在安装的这一版；不勾则保持原样。",
                    FontSize = 12, Foreground = Dim, Margin = new Thickness(0, 0, 0, 10),
                    TextWrapping = TextWrapping.Wrap,
                };
            }
            var startmenu = new CheckBox { Content = "创建开始菜单快捷方式", IsChecked = true, Foreground = Ink, Margin = new Thickness(0, 0, 0, 6) };
            var desk = new CheckBox { Content = "创建桌面快捷方式", IsChecked = false, Foreground = Ink, Margin = new Thickness(0, 0, 0, 14) };
            var launch = new CheckBox { Content = "装完立即启动 Vigil", IsChecked = true, Foreground = Ink, Margin = new Thickness(0, 0, 0, 14) };

            var status = new TextBlock { FontSize = 12, Foreground = Dim, Margin = new Thickness(0, 0, 0, 12), TextWrapping = TextWrapping.Wrap };
            var bar = new ProgressBar { Height = 4, Margin = new Thickness(0, 0, 0, 8), Foreground = Accent };
            var box = LogBox();

            var primary = new Button
            {
                Content = "安装", MinWidth = 120, Height = 30, Background = Accent,
                Foreground = Brushes.White, BorderThickness = new Thickness(0),
            };
            var cancel = new Button { Content = "关闭", MinWidth = 90, Height = 30, Margin = new Thickness(8, 0, 0, 0) };
            var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
            buttons.Children.Add(primary);
            buttons.Children.Add(cancel);

            var col = new StackPanel();
            col.Children.Add(new TextBlock { Text = "Vigil 安装", FontSize = 20, FontWeight = FontWeights.SemiBold, Foreground = Ink });
            col.Children.Add(new TextBlock
            {
                Text = "版本 " + Installer.Version() + " · 零前置：不需要另外装 .NET 或 Python",
                FontSize = 12, Foreground = Dim, Margin = new Thickness(0, 2, 0, 16),
            });
            col.Children.Add(status);
            col.Children.Add(dirRow);
            col.Children.Add(auto);
            if (autoNotice != null) col.Children.Add(autoNotice);
            col.Children.Add(startmenu);
            col.Children.Add(desk);
            col.Children.Add(launch);
            col.Children.Add(box);
            col.Children.Add(bar);
            col.Children.Add(buttons);

            var win = NewWindow("Vigil 安装", 560, 520, col);
            bool done = false;

            Action<string> append = m => box.Dispatcher.Invoke(() =>
            {
                box.Text += m + "\n";
                box.ScrollToEnd();
                log(m);
            });

            // 主按钮文案随安装状态变：没装过→安装，同版本→修复，装了旧版→升级到 X。
            Func<string> refresh = () =>
            {
                if (!Installer.IsInstalled(dirBox.Text.Trim()))
                {
                    status.Text = "这台电脑上还没装过。";
                    return "安装";
                }
                var cur = Installer.InstalledVersion(dirBox.Text.Trim());
                status.Text = "检测到已安装 " + (cur ?? "某个版本") + "。";
                return cur == Installer.Version() ? "修复" : "升级到 " + Installer.Version();
            };
            primary.Content = refresh();
            dirBox.TextChanged += (s, e) => { if (!done) primary.Content = refresh(); };

            primary.Click += (s, e) =>
            {
                if (done) { win.Close(); return; }
                o.TargetDir = dirBox.Text.Trim();
                o.PayloadDir = Installer.PayloadDirOf(Installer.AppDir());
                o.Autostart = auto.IsChecked == true;
                o.StartMenuShortcut = startmenu.IsChecked == true;
                o.DesktopShortcut = desk.IsChecked == true;
                o.LaunchAfter = launch.IsChecked == true;
                o.Log = append;
                primary.IsEnabled = false;
                bar.IsIndeterminate = true;
                win.Dispatcher.BeginInvoke(DispatcherPriority.Background, new Action(() =>
                {
                    bool ok = false;
                    try { ok = Installer.Install(o); }
                    catch (Exception ex) { append("✗ 未捕获的异常：" + ex.Message); }
                    bar.IsIndeterminate = false;
                    primary.IsEnabled = true;
                    cancel.IsEnabled = true;
                    if (ok)
                    {
                        done = true;
                        primary.Content = "完成";
                        primary.IsEnabled = true;
                        append("余额 Key 是按当前 Windows 账户加密存的，换电脑要重新录入。");
                    }
                    else primary.Content = refresh();
                }));
            };
            browse.Click += (s, e) =>
            {
                var dlg = new System.Windows.Forms.FolderBrowserDialog();
                try { if (System.IO.Directory.Exists(dirBox.Text)) dlg.SelectedPath = dirBox.Text; }
                catch { }
                if (dlg.ShowDialog() == System.Windows.Forms.DialogResult.OK) dirBox.Text = dlg.SelectedPath;
            };
            cancel.Click += (s, e) => win.Close();
            return win;
        }

        public static Window UninstallWindow(Installer.Options o, Action<string> log)
        {
            var purge = new CheckBox
            {
                Content = "连同设置与余额 Key 一起删除",
                Foreground = Ink,
                Margin = new Thickness(0, 0, 0, 14),
            };
            var box = LogBox();
            var go = new Button
            {
                Content = "卸载", MinWidth = 120, Height = 30, Background = Accent,
                Foreground = Brushes.White, BorderThickness = new Thickness(0),
            };
            var cancel = new Button { Content = "取消", MinWidth = 90, Height = 30, Margin = new Thickness(8, 0, 0, 0) };
            var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
            buttons.Children.Add(go);
            buttons.Children.Add(cancel);

            var col = new StackPanel();
            col.Children.Add(new TextBlock { Text = "卸载 Vigil", FontSize = 20, FontWeight = FontWeights.SemiBold, Foreground = Ink });
            col.Children.Add(new TextBlock
            {
                FontSize = 13, Foreground = Ink, TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 12, 0, 12),
                Text = "将要删除：\n" +
                       "  · 安装目录与其中全部文件（" + o.TargetDir + "）\n" +
                       "  · 开机自动启动项\n" +
                       "  · 开始菜单 / 桌面快捷方式\n" +
                       "  · 「设置 → 应用」里的卸载入口\n\n" +
                       "默认保留：设置、日志与余额 Key。",
            });
            col.Children.Add(purge);
            col.Children.Add(box);
            col.Children.Add(buttons);

            var win = NewWindow("Vigil 卸载", 560, 480, col);
            Action<string> append = m => box.Dispatcher.Invoke(() =>
            {
                box.Text += m + "\n";
                box.ScrollToEnd();
                log(m);
            });
            go.Click += (s, e) =>
            {
                o.PurgeData = purge.IsChecked == true;
                o.Log = append;
                go.IsEnabled = cancel.IsEnabled = false;
                win.Dispatcher.BeginInvoke(DispatcherPriority.Background, new Action(() =>
                {
                    bool ok = false;
                    try { ok = Installer.Uninstall(o); }
                    catch (Exception ex) { append("✗ 未捕获的异常：" + ex.Message); }
                    go.IsEnabled = true;
                    cancel.IsEnabled = true;
                    // 卸载会把自己所在的目录一起删掉（DeleteWithTempGuard 里换了进程），
                    // 成功就别留着这扇窗骗人。
                    if (ok) win.Dispatcher.BeginInvoke(new Action(() => win.Close()));
                }));
            };
            cancel.Click += (s, e) => win.Close();
            return win;
        }

        static TextBox LogBox() => new TextBox
        {
            IsReadOnly = true,
            Height = 140,
            FontFamily = new FontFamily("Consolas, Microsoft YaHei UI"),
            FontSize = 12,
            Foreground = Dim,
            Background = Paper,
            BorderBrush = Line,
            TextWrapping = TextWrapping.Wrap,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            Margin = new Thickness(0, 0, 0, 10),
        };

        static Window NewWindow(string title, double w, double h, UIElement content) => new Window
        {
            Title = title,
            Width = w,
            Height = h,
            Background = Paper,
            WindowStartupLocation = WindowStartupLocation.CenterScreen,
            ResizeMode = ResizeMode.CanMinimize,
            Content = content,
        };
    }
}
