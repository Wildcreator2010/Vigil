using System;
using System.Windows;
using WpfControls = System.Windows.Controls;
using UiControls = Wpf.Ui.Controls;

namespace DshBar
{
    /// <summary>
    /// 运行页：检测引擎的轮询间隔、阈值只读展示、手动重启，以及开机自启与两个目录入口。
    ///
    /// 生效矩阵（spec §6）在这里是分叉的：
    ///   · interval —— 引擎子进程的轮询周期是 StateClient 构造时定死的，
    ///     所以保存之后必须 App.RestartEngine()，日志会多出一行「引擎已启动 PID=…」；
    ///   · 开机自启 —— 复用已有的 ToggleAutostart()，写 HKCU\...\Run\dsh-status，不进 settings.json；
    ///   · 两个文件入口 —— 数据目录走 App.OpenInExplorer()（目录本来就能开），
    ///     日志走 App.RevealInExplorer()：在资源管理器里把 bar.log 选中。
    ///     后者刻意不用 ShellExecute 开文件 —— `.log` 没有可用的打开方式时那会落到系统的
    ///     「选取应用」对话框，落点不归我们控制（spec §5 要的是日志目录，定位到文件是它的加强版）。
    /// 颜色、页内 ScrollViewer、先赋初值后订阅这三条与 NotifyPage 同源，理由也相同。
    /// </summary>
    internal sealed class RuntimePage : WpfControls.ContentControl, IPanelPage
    {
        // 阈值那一行是「引擎此刻看到了什么」，每个快照刷一次（Pages.cs 的 Refresh 契约）。
        // 常量本身（ACTIVE_WINDOW 等）不在这里复制 —— 复制一份数字就会和 dsh_state.py 漂移。
        readonly WpfControls.TextBlock _thresholds = new WpfControls.TextBlock
        {
            FontSize = 12,
            MaxWidth = 320,
            TextAlignment = TextAlignment.Right,
            TextWrapping = TextWrapping.Wrap,
        };

        public RuntimePage()
        {
            Ui.Ref(_thresholds, WpfControls.TextBlock.ForegroundProperty, Ui.InkDimKey);

            var interval = new UiControls.NumberBox
            {
                Width = 150,
                Minimum = 1,
                Maximum = 60,
                SmallChange = 1,
                SpinButtonPlacementMode = UiControls.NumberBoxSpinButtonPlacementMode.Inline,
            };
            interval.Value = App.Config.Interval;
            interval.ValueChanged += (s, e) =>
            {
                App.Config.Interval = Math.Max(1, (int)(e.NewValue ?? 1));
                App.SaveSettings();
                // 轮询周期是引擎子进程的启动参数，改完只能重启它才生效。
                App.RestartEngine();
            };

            var autostart = new UiControls.ToggleSwitch { OnContent = "开", OffContent = "关" };
            autostart.IsChecked = App.AutostartOn();
            autostart.Checked += (s, e) => App.SetAutostart(true);
            autostart.Unchecked += (s, e) => App.SetAutostart(false);

            var scroll = new WpfControls.ScrollViewer
            {
                HorizontalScrollBarVisibility = WpfControls.ScrollBarVisibility.Disabled,
                VerticalScrollBarVisibility = WpfControls.ScrollBarVisibility.Auto,
            };
            scroll.Content = Ui.Column(
                Ui.Heading("运行"),
                Ui.Group("检测引擎",
                    Ui.Row("轮询间隔（秒）", "改完自动重启检测进程生效，不用重开面板。", interval),
                    Ui.Row("阈值（只读）",
                        "活跃 / 完成 / 卡住 / 待处理升级这些判定窗口定义在 dsh_state.py 顶部，" +
                        "面板不复制数字：改常量要动引擎，并由 python test_dsh_state.py 校验边界。",
                        _thresholds),
                    Ui.Row("检测进程", "引擎异常退出本来也会自动重启；状态不对时可以手动重启一次。",
                        PushButton("重启检测进程", () => App.RestartEngine()))),
                Ui.Group("开机与文件",
                    Ui.Row("开机自动启动", "写入当前用户的注册表 Run 项，只影响本机这个账户。", autostart),
                    Ui.Row("日志",
                        "状态栏没出现、状态不对时先看它。按钮会在资源管理器里选中 bar.log —— " +
                        "开的是它所在的文件夹，不需要 .log 先有一个可用的打开方式。",
                        PushButton("打开日志位置", () => App.RevealInExplorer(App.LogPath))),
                    Ui.Row("数据目录", "settings.json、余额 Key、刷新标记都在这里。",
                        PushButton("打开数据目录", () => App.OpenInExplorer(App.DataDir)))));
            HorizontalContentAlignment = HorizontalAlignment.Stretch;
            VerticalContentAlignment = VerticalAlignment.Stretch;
            var backdrop = new WpfControls.Border { Child = scroll };
            Ui.Ref(backdrop, WpfControls.Border.BackgroundProperty, Ui.PageKey);
            Content = backdrop;
        }

        /// <summary>一个动作按钮：文本 + 点击。Wpf.Ui 的 Button 自带主题样式，前景不用再接键。</summary>
        static FrameworkElement PushButton(string text, Action onClick)
        {
            var b = new UiControls.Button { Content = text, Padding = new Thickness(12, 5, 12, 5) };
            b.Click += (s, e) => onClick();
            return b;
        }

        /// <summary>
        /// 只读展示跟着快照走：没有快照（面板在第一帧之前打开、或上一帧 ok:false）与有快照
        /// 是两种文案，冒烟那条「阈值行不是写死的一行字」的门禁钉的就是这个差别。
        /// </summary>
        public void Refresh(Snapshot snap)
        {
            if (snap == null || !snap.Ok)
            {
                _thresholds.Text = "引擎还没有可用快照，等第一帧。";
                return;
            }
            var s = snap.Session;
            // Sessions 可以是 null（--watch 的异常帧不带这个键），那一格如实写「未上报」，
            // 别和「扫到 0 个会话」混成同一个 0。
            string scanned = snap.Sessions == null ? "未上报" : snap.Sessions.Count.ToString();
            _thresholds.Text =
                "当前判定：" + (snap.Label ?? snap.State ?? "-") +
                (s == null ? "｜没有活跃会话" : $"｜会话 {s.Project}｜静默 {s.AgeSec:F0} 秒") +
                $"｜本次扫描 {scanned} 个会话";
        }
    }
}
