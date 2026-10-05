using System;
using System.Windows;
using WpfControls = System.Windows.Controls;

namespace Vigil
{
    /// <summary>
    /// 关于页：品牌行 + 安装位置 + 版本 + 检测引擎用的是哪个解释器。
    ///
    /// 「检测引擎」那一格是换电脑排障的第一现场：状态栏说「引擎不可用」时，先来看它
    /// 认的到底是随附的 runtime\python\python.exe 还是宿主 PATH 上某个坏掉的 python，
    /// 不用去翻 bar.log。开发目录里随附解释器不存在，这里就如实显示系统解释器，不伪装。
    ///
    /// 数字一律不在这儿复制：版本取 App.VersionText（归 csproj 的 &lt;Version&gt;），
    /// 解释器取 App.EnginePathForDisplay()（归 ResolvePython 那三级解析）。
    /// </summary>
    internal sealed class AboutPage : WpfControls.ContentControl, IPanelPage
    {
        readonly WpfControls.TextBlock _where = Value();
        readonly WpfControls.TextBlock _version = Value();
        readonly WpfControls.TextBlock _engine = Value();

        public AboutPage()
        {
            Ui.Ref(_where, WpfControls.TextBlock.ForegroundProperty, Ui.InkDimKey);
            Ui.Ref(_version, WpfControls.TextBlock.ForegroundProperty, Ui.InkDimKey);
            Ui.Ref(_engine, WpfControls.TextBlock.ForegroundProperty, Ui.InkDimKey);

            var scroll = new WpfControls.ScrollViewer
            {
                HorizontalScrollBarVisibility = WpfControls.ScrollBarVisibility.Disabled,
                VerticalScrollBarVisibility = WpfControls.ScrollBarVisibility.Auto,
            };
            scroll.Content = Ui.Column(
                Ui.Heading("Vigil"),
                Ui.Group("安装",
                    Ui.Row("安装位置", "状态栏与随附检测引擎所在的目录。卸载要认的就是这个目录。", _where),
                    Ui.Row("版本", "数字版本归工程文件；后面的种加词是发布代号，不参与比较。", _version),
                    Ui.Row("检测引擎",
                        "优先用随附的 runtime\\python\\python.exe，不受宿主 PATH 影响。" +
                        "命令行里 Vigil.exe --engine-probe 打印的就是这一格的判断结果。", _engine)));
            HorizontalContentAlignment = HorizontalAlignment.Stretch;
            VerticalContentAlignment = VerticalAlignment.Stretch;
            var backdrop = new WpfControls.Border { Child = scroll };
            Ui.Ref(backdrop, WpfControls.Border.BackgroundProperty, Ui.PageKey);
            Content = backdrop;
            Fill();
        }

        /// <summary>这三格都是静态信息，不需要跟着快照刷新；Refresh 留空是契约要求。</summary>
        public void Refresh(Snapshot snap) { }

        static WpfControls.TextBlock Value() => new WpfControls.TextBlock
        {
            FontSize = 12,
            TextWrapping = TextWrapping.Wrap,
            MaxWidth = 340,
            TextAlignment = TextAlignment.Right,
        };

        void Fill()
        {
            _where.Text = AppContext.BaseDirectory;
            _version.Text = App.VersionText;
            _engine.Text = App.EnginePathForDisplay();
        }
    }
}
