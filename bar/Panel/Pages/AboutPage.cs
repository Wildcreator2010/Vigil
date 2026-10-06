using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using WpfControls = System.Windows.Controls;
using UiControls = Wpf.Ui.Controls;

namespace Vigil
{
    /// <summary>
    /// 关于页 = spec 增补 V5 的四块，一块不少：
    /// ① 身份区（Logo 大图 + 项目名 + 版本 + 作者 + 仓库链接）
    /// ② 开源许可清单（**从 THIRD-PARTY-NOTICES.md 解析**，见 Credits.cs）
    /// ③ 运行时与素材声明（非 MIT 那几项单独标出来）
    /// ④ 数据与诊断区（安装位置 / 版本 / 检测引擎 / 日志与数据目录入口）
    /// 外加 MIT 全文（spec §8：清单不能只躺在仓库里）。
    ///
    /// 数字与名字一律不在这儿复制：版本取 <see cref="App.VersionText"/>（归 csproj 的
    /// &lt;Version&gt;），作者与仓库取程序集元数据（归 &lt;Authors&gt; / &lt;RepositoryUrl&gt;），
    /// 许可清单归那份 markdown。这一页任何一格自己拼出来的字符串，都是将来会漂移的地方。
    ///
    /// 「检测引擎」那一格是换电脑排障的第一现场：状态栏说「引擎不可用」时，先来看它
    /// 认的到底是随附的 runtime\python\python.exe 还是宿主 PATH 上某个坏掉的 python。
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
                Identity(),
                CreditList(),
                RuntimeStatement(),
                MitText(),
                Ui.Group("安装与诊断",
                    Ui.Row("安装位置", "状态栏与随附检测引擎所在的目录。卸载要认的就是这个目录。", _where),
                    Ui.Row("版本", "数字版本归工程文件；后面的种加词是发布代号，不参与比较。", _version),
                    Ui.Row("检测引擎",
                        "优先用随附的 runtime\\python\\python.exe，不受宿主 PATH 影响。" +
                        "命令行里 Vigil.exe --engine-probe 打印的就是这一格的判断结果。", _engine)),
                Ui.Group("数据与日志",
                    Ui.Row("日志", "状态不对时先看它；按钮在资源管理器里选中 bar.log。",
                        Action("打开日志位置", () => App.RevealInExplorer(App.LogPath))),
                    Ui.Row("数据目录", "settings.json、余额 Key、刷新标记都在这里。",
                        Action("打开数据目录", () => App.OpenInExplorer(App.DataDir)))));
            HorizontalContentAlignment = HorizontalAlignment.Stretch;
            VerticalContentAlignment = VerticalAlignment.Stretch;
            var backdrop = new WpfControls.Border { Child = scroll };
            Ui.Ref(backdrop, WpfControls.Border.BackgroundProperty, Ui.PageKey);
            Content = backdrop;
            Fill();
        }

        static WpfControls.TextBlock Value() => new WpfControls.TextBlock
        {
            FontSize = 12,
            TextWrapping = TextWrapping.Wrap,
            MaxWidth = 340,
            TextAlignment = TextAlignment.Right,
        };

        /// <summary>① 身份区。Logo 是 V6 那份抠过透明底的 256 大图，程序集资源里的 logo.png。</summary>
        static FrameworkElement Identity()
        {
            var head = new WpfControls.StackPanel { Margin = new Thickness(0, 0, 0, 18) };
            try
            {
                head.Children.Add(new WpfControls.Image
                {
                    Source = new BitmapImage(new Uri("pack://application:,,,/assets/logo.png")),
                    Width = 96,
                    Height = 96,
                    Margin = new Thickness(0, 0, 0, 10),
                    Stretch = Stretch.Uniform,
                });
            }
            catch (Exception)
            {
                // Logo 读不到（资源没打进去）不该让整页开不出来：身份区少一张图，
                // 文字那几行仍然把话说完。真丢了有冒烟的门禁喊。
            }

            var title = new WpfControls.TextBlock
            {
                Text = App.ProductName,
                FontSize = 26,
                FontWeight = FontWeights.SemiBold,
            };
            Ui.Ref(title, WpfControls.TextBlock.ForegroundProperty, Ui.InkKey);
            head.Children.Add(title);
            head.Children.Add(Subtle(App.VersionText));

            var author = new WpfControls.TextBlock { Text = "作者 " + App.AuthorText };
            Ui.Ref(author, WpfControls.TextBlock.ForegroundProperty, Ui.InkDimKey);
            author.Margin = new Thickness(0, 6, 0, 0);
            head.Children.Add(author);

            string repo = App.RepoUrl;
            if (!string.IsNullOrEmpty(repo))
            {
                // WPF-UI 的 HyperlinkButton 自己走 NavigateUri，不需要我们 Process.Start。
                var link = new UiControls.HyperlinkButton
                {
                    Content = repo,
                    NavigateUri = repo,
                    Margin = new Thickness(-12, 4, 0, 0),
                    FontSize = 12.5,
                    Appearance = UiControls.ControlAppearance.Transparent,
                };
                // 链接前景必须显式接品牌橙：它默认吃系统强调色，本机那一个是紫色，
                // 而 spec 增补 V2 明文「不得再出现紫色」（离屏截图实证过一回）。
                link.SetResourceReference(
                    System.Windows.Controls.Control.ForegroundProperty, "VigilAccentBrush");
                head.Children.Add(link);
            }
            return head;
        }

        static WpfControls.TextBlock Subtle(string text)
        {
            var t = new WpfControls.TextBlock { Text = text, FontSize = 13 };
            Ui.Ref(t, WpfControls.TextBlock.ForegroundProperty, Ui.InkDimKey);
            return t;
        }

        /// <summary>
        /// ② 许可清单：一条一张卡，标题 = 名称 + 许可证，正文 = 用途 + 上游地址。
        /// 条目由 <see cref="Credits.Parse"/> 从 THIRD-PARTY-NOTICES.md 读出来。
        /// </summary>
        static FrameworkElement CreditList()
        {
            List<Credit> items = Credits.Parse();
            if (items.Count == 0)
            {
                // 文件没跟着分发时不能装作"没有第三方"——那是合规页最坏的失败模式：
                // 看着是空的、绿的，实际是读不到。
                return Ui.Group("开源许可清单",
                    Ui.StackedRow("读不到 THIRD-PARTY-NOTICES.md",
                        "清单是从那份文件解析出来的，文件不在 exe 旁边就什么都显示不了。" +
                        "期望位置：" + Credits.NoticesPath, null));
            }

            var rows = new FrameworkElement[items.Count];
            for (int i = 0; i < items.Count; i++)
            {
                var c = items[i];
                string right = string.IsNullOrEmpty(c.Url)
                    ? c.ShortLicense : c.ShortLicense + "\n" + c.Url;
                var value = new WpfControls.TextBlock
                {
                    Text = right,
                    FontSize = 11.5,
                    TextWrapping = TextWrapping.Wrap,
                    TextAlignment = TextAlignment.Right,
                    MaxWidth = 320,
                };
                Ui.Ref(value, WpfControls.TextBlock.ForegroundProperty, Ui.InkDimKey);
                rows[i] = Ui.Row(c.Name, c.Purpose, value);
            }
            return Ui.Group("开源许可清单（共 " + items.Count + " 项）", rows);
        }

        /// <summary>
        /// ③ 运行时与素材声明：把非 MIT 的那几项单独列出来。
        /// 混在 MIT 列表里没人会注意到「Segoe Fluent Icons 是微软专有字体许可」，
        /// 而这一项恰恰是分发时最容易踩错的（spec §12 把它单列成一条风险）。
        /// </summary>
        static FrameworkElement RuntimeStatement()
        {
            var rows = new List<FrameworkElement>();
            foreach (var c in Credits.Parse())
            {
                if (!c.NonMit) continue;
                rows.Add(Ui.Row(c.Name + " —— 非 MIT", c.Purpose,
                    LicenseTag(c.License)));
            }
            rows.Add(Ui.StackedRow("运行时依赖",
                ".NET 10 Windows Desktop 运行时与 Python 3.14 标准库是运行环境要求；" +
                "自包含安装包会把 .NET 与 CPython 一起带走，其许可证原文随附在 licenses\\ 目录。",
                null));
            return Ui.Group("运行时与素材声明", rows.ToArray());
        }

        static FrameworkElement LicenseTag(string license)
        {
            var tag = new WpfControls.Border
            {
                CornerRadius = new CornerRadius(8),
                Padding = new Thickness(8, 2, 8, 2),
                Background = new SolidColorBrush(Color.FromArgb(0x26, 0xC6, 0x5B, 0x51)),
                VerticalAlignment = VerticalAlignment.Center,
                HorizontalAlignment = HorizontalAlignment.Right,
                MaxWidth = 320,
            };
            var text = new WpfControls.TextBlock
            {
                Text = "非 MIT · 专有许可",
                FontSize = 11.5,
                TextWrapping = TextWrapping.Wrap,
            };
            Ui.Ref(text, WpfControls.TextBlock.ForegroundProperty, Ui.InkKey);
            tag.Child = text;
            return tag;
        }

        /// <summary>MIT 全文：渲染仓库里那份 LICENSE，不在代码里再抄一遍。</summary>
        static FrameworkElement MitText()
        {
            string mit = Credits.LicenseText();
            var body = new WpfControls.TextBlock
            {
                Text = string.IsNullOrEmpty(mit) ? "读不到 LICENSE 文件。" : mit,
                FontSize = 11.5,
                TextWrapping = TextWrapping.Wrap,
                FontFamily = new FontFamily("Consolas, Microsoft YaHei UI"),
            };
            Ui.Ref(body, WpfControls.TextBlock.ForegroundProperty, Ui.InkDimKey);
            return Ui.Group("开源协议", Ui.StackedRow(
                "MIT 许可证全文",
                "与仓库根目录的 LICENSE 同一份文件，逐字渲染，不另抄。", body));
        }

        static FrameworkElement Action(string text, Action onClick)
        {
            var b = new UiControls.Button
            {
                Content = text,
                Padding = new Thickness(12, 5, 12, 5),
                Margin = new Thickness(8, 0, 0, 0),
            };
            b.Click += (s, e) => onClick();
            return b;
        }

        /// <summary>安装位置 / 版本 / 引擎都是启动时就定死的信息，不跟快照走；Refresh 留空是契约要求。</summary>
        public void Refresh(Snapshot snap) { }

        void Fill()
        {
            _where.Text = AppContext.BaseDirectory;
            _version.Text = App.VersionText;
            _engine.Text = App.EnginePathForDisplay();
        }
    }
}
