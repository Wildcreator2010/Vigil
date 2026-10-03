using System.Windows;
using System.Windows.Media;
using WpfControls = System.Windows.Controls;

namespace DshBar
{
    /// <summary>控制台里的一页。Task 5~8 填实，本文件只保证可编译可渲染。</summary>
    internal class PageBase : WpfControls.ContentControl, IPanelPage
    {
        protected PageBase(string title)
        {
            // 页面本身透明，让控制台外壳的主题底色透出来；文字用主题刷而不是写死的
            // #1A1D23，否则切到 dark 就是「深色外壳 + 黑底黑字」。
            Background = Brushes.Transparent;
            var text = new WpfControls.TextBlock
            {
                Text = title,
                FontSize = 16,
            };
            text.SetResourceReference(WpfControls.TextBlock.ForegroundProperty, "TextFillColorPrimaryBrush");
            Content = text;
        }

        public virtual void Refresh(Snapshot snap) { }
    }

    /// <summary>
    /// 概览页的表格骨架：六列全是 Snapshot.Sessions 里的真实字段，Task 8 往这里填交互与配色。
    /// 列宽/表头文案是骨架的一部分，冒烟那条「概览页已画出表格」的门禁钉的就是这张表
    /// 真的铺满了离屏画面（口径是像素结构，不是色数，见 smoke_test.py 的 shot_table_stats）。
    /// Refresh 里用 snap?.Sessions：面板可能在第一帧快照之前就被打开（App.Latest 还是 null），
    /// --watch 的异常帧也不带 sessions（Snapshot.Sessions 可为 null），空表是合法状态不是崩溃。
    /// Records 是 int?：引擎那边 `"records": s.get("records")` 可以出 null，
    /// 非可空 int 会让整帧反序列化失败、状态栏静默停更（见 StateClient.SessionRow），
    /// null 时这一格和 Turn 一样留白 —— DataGridTextColumn 绑到 null 就是空串。
    /// </summary>
    internal sealed class OverviewPage : WpfControls.ContentControl, IPanelPage
    {
        readonly WpfControls.DataGrid _grid = new WpfControls.DataGrid
        {
            AutoGenerateColumns = false,
            IsReadOnly = true,
            HeadersVisibility = WpfControls.DataGridHeadersVisibility.Column,
            GridLinesVisibility = WpfControls.DataGridGridLinesVisibility.Horizontal,
            Background = Brushes.White,
            BorderBrush = new SolidColorBrush(Color.FromRgb(0xE2, 0xE5, 0xEA)),
            BorderThickness = new Thickness(1),
            MinHeight = 260,
        };

        public OverviewPage()
        {
            foreach (var c in new[]
            {
                ("项目", "Project", 150.0), ("状态", "State", 110.0),
                ("轮次", "Turn", 60.0), ("静默秒", "AgeSec", 80.0),
                ("最近工具", "LastTool", 130.0), ("记录数", "Records", 80.0),
            })
            {
                _grid.Columns.Add(new WpfControls.DataGridTextColumn
                {
                    Header = c.Item1,
                    Binding = new System.Windows.Data.Binding(c.Item2),
                    Width = new WpfControls.DataGridLength(c.Item3),
                });
            }
            Content = new WpfControls.Grid
            {
                // 表格必须拿到**有限高度**，它自己模板里那个 ScrollViewer 才生效。
                // 之前这里套的是竖向 StackPanel：StackPanel 给子元素无限高度，
                // DataGrid 于是按行数撑到 ~1150px 再被 620 的视口裁掉，
                // ScrollViewer 的 extent == viewport → 永不滚动 → 第 16~60 行
                // （SESSIONS_IN_SNAPSHOT 上限 60）在真实面板里既不能靠表格滚动、
                // 排版成本还随行数线性上升。离屏出图同样只画到 620，像素门禁完全看不出来。
                // 现在用一行 `*` 的行定义把父级的有限高度原样传给表格：
                // 视口受限、多出的行落在表格自己的滚动里、表头固定在顶上。
                RowDefinitions =
                {
                    new WpfControls.RowDefinition { Height = new GridLength(1, GridUnitType.Star) },
                },
                Children = { _grid },
            };
        }

        public void Refresh(Snapshot snap) => _grid.ItemsSource = snap?.Sessions;
    }

    internal sealed class NotifyPage : PageBase { public NotifyPage() : base("通知") { } }
    internal sealed class AppearancePage : PageBase { public AppearancePage() : base("外观") { } }
    internal sealed class RuntimePage : PageBase { public RuntimePage() : base("运行") { } }
    internal sealed class BalancePage : PageBase { public BalancePage() : base("余额") { } }
    internal sealed class AboutPage : PageBase { public AboutPage() : base("关于") { } }
}
