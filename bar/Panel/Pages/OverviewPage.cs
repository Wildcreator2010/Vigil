using System;
using System.Collections.Generic;
using System.Windows;
using WpfControls = System.Windows.Controls;

namespace Vigil
{
    /// <summary>
    /// 概览页 = 当前状态卡 + 全部会话表 + 等你处理。
    ///
    /// 三件事决定这页的形状，改动前请先读完：
    /// ① **竖向约束链上不许出现无限高容器**。页面根是 `Auto / * / Auto` 三行 Grid，
    ///    表格那一行吃掉剩余高度；`*` 行里也不能套竖向 StackPanel（Ui.Group 的卡体就是
    ///    StackPanel —— 它给子元素的竖向约束是无限高），否则 DataGrid 按行数长到 ~1150px
    ///    再被视口裁掉：第 16~60 行（SESSIONS_IN_SNAPSHOT 上限 60）既滚不到也看不见，
    ///    而离屏出图与真窗口一样被裁，**像素门禁看不见这个缺陷**（Task 4 轮 2 的原话）。
    ///    所以表头那一行用手写的 `Auto + *` Grid，不用 Ui.Group。
    /// ② **这页不放整页 ScrollViewer**：它是 Global Constraints「需要整页滚动的页面自己放」
    ///    那条的例外 —— 整页滚与表体滚只能选一个，这一页要的是表头固定、只滚表体
    ///    （由 smoke_test.check_panel_real_scroll 在真窗口上钉住）。
    /// ③ **颜色只走主题键**（Task 5 的白底豁免在本任务收口）：表底 `Ui.CardKey`、
    ///    描边 `Ui.LineKey`，不再写 `Brushes.White` / `#E2E5EA`。状态条那一格的色带是
    ///    **数据驱动**的（引擎报什么色画什么色），所以走 `Ui.BrushOf`，这是唯一的例外。
    ///    配套的像素门禁（smoke_test 的 shot_table_stats / real_window_stats）同轮改成
    ///    「按当前主题现量纸色」，两边一起走。
    ///
    /// Refresh 每帧都跑（引擎几秒一帧，面板开着就一直在拉），所以这一页里**没有任何
    /// `Text +=`**：待处理那一格每次都整串重算再赋值，append 会把历史帧的条目叠上去。
    /// </summary>
    internal sealed class OverviewPage : WpfControls.ContentControl, IPanelPage
    {
        readonly WpfControls.Border _accent = new WpfControls.Border
        { Width = 4, CornerRadius = new CornerRadius(2), HorizontalAlignment = HorizontalAlignment.Left };

        readonly WpfControls.TextBlock _big = new WpfControls.TextBlock
        { FontSize = 26, FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center };

        readonly WpfControls.TextBlock _sub = new WpfControls.TextBlock
        { FontSize = 12.5, Margin = new Thickness(0, 4, 0, 0), TextWrapping = TextWrapping.Wrap };

        readonly WpfControls.TextBlock _waiting = new WpfControls.TextBlock
        { FontSize = 12.5, TextWrapping = TextWrapping.Wrap };

        readonly WpfControls.DataGrid _grid = new WpfControls.DataGrid
        {
            AutoGenerateColumns = false,
            IsReadOnly = true,
            HeadersVisibility = WpfControls.DataGridHeadersVisibility.Column,
            GridLinesVisibility = WpfControls.DataGridGridLinesVisibility.Horizontal,
            BorderThickness = new Thickness(1),
            MinHeight = 280,
            FontSize = 12.5,
        };

        public OverviewPage()
        {
            // Task 5 的白底豁免在这里收口：表底/描边改挂主题键（写死 Brushes.White 时
            // 离屏实测 399904 个逐字节 #FFFFFF，深色主题下就是「深色外壳 + 白底表格」）。
            Ui.Ref(_grid, WpfControls.DataGrid.BackgroundProperty, Ui.CardKey);
            Ui.Ref(_grid, WpfControls.DataGrid.BorderBrushProperty, Ui.LineKey);
            Ui.Ref(_big, WpfControls.TextBlock.ForegroundProperty, Ui.InkKey);
            Ui.Ref(_sub, WpfControls.TextBlock.ForegroundProperty, Ui.InkDimKey);
            Ui.Ref(_waiting, WpfControls.TextBlock.ForegroundProperty, Ui.InkKey);
            AddColumn("项目", "Project", 150);
            AddColumn("状态", "StateLabel", 110);
            AddColumn("轮次", "TurnText", 80);
            AddColumn("静默", "AgeText", 80);
            AddColumn("任务", "TodoText", 90);
            AddColumn("最近工具", "LastTool", 130);
            AddColumn("tokens", "UsageText", 110);
            AddColumn("标题", "Title", 200);

            var statusCard = new WpfControls.Border
            {
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(8),
                Padding = new Thickness(14, 10, 14, 10),
                Child = new WpfControls.Grid
                {
                    Children =
                    {
                        _accent,
                        new WpfControls.StackPanel { Margin = new Thickness(16, 0, 0, 0), Children = { _big, _sub } },
                    },
                },
            };
            Ui.Ref(statusCard, WpfControls.Border.BackgroundProperty, Ui.CardKey);
            Ui.Ref(statusCard, WpfControls.Border.BorderBrushProperty, Ui.LineKey);

            // 页面根必须是 Grid，不是 Ui.Column（=竖向 StackPanel =无限高约束），理由见 ①。
            var root = new WpfControls.Grid();
            root.RowDefinitions.Add(new WpfControls.RowDefinition { Height = GridLength.Auto });   // 标题 + 状态卡
            root.RowDefinitions.Add(new WpfControls.RowDefinition                                 // 会话表（吃掉剩余高度）
            { Height = new GridLength(1, GridUnitType.Star) });
            root.RowDefinitions.Add(new WpfControls.RowDefinition { Height = GridLength.Auto });   // 等你处理

            var head = Ui.Column(Ui.Heading("概览"), statusCard);
            WpfControls.Grid.SetRow(head, 0);
            var table = TableArea();
            WpfControls.Grid.SetRow(table, 1);
            var wait = Ui.Group("等你处理", _waiting);
            WpfControls.Grid.SetRow(wait, 2);
            root.Children.Add(head);
            root.Children.Add(table);
            root.Children.Add(wait);
            // 整页底色必须由页面自己铺一层挂 Ui.PageKey 的 Border —— 和其他五页同一口径。
            // 少这一层时，卡片底下露出的是窗口自己的背衬：真窗口里 Mica/Acrylic 会把桌面
            // 透上来（实测 ~#D3D3D3），而离屏 --panel-shot 那块是透明，于是半透明白卡片
            // 压在两种底上，量出来 #F2F2F2 对 #FEFEFE，比样当场红（概览页是唯一漏铺的页）。
            HorizontalContentAlignment = HorizontalAlignment.Stretch;
            VerticalContentAlignment = VerticalAlignment.Stretch;
            var backdrop = new WpfControls.Border { Child = root };
            Ui.Ref(backdrop, WpfControls.Border.BackgroundProperty, Ui.PageKey);
            Content = backdrop;
        }

        /// <summary>
        /// 「会话」标题 + 表格。**不能**用 Ui.Group 包表格：它的卡体是竖向 StackPanel，
        /// 会把有限高度换成无限高（见类注释 ①）。这里用 Auto + * 两行把标题占的那截扣掉，
        /// 剩下的有限高度原样交给表格，多出的行落在表格自己那个 ScrollViewer 里。
        /// </summary>
        WpfControls.Grid TableArea()
        {
            var title = new WpfControls.TextBlock
            {
                Text = "会话",
                FontSize = 13,
                FontWeight = FontWeights.SemiBold,
                Margin = new Thickness(2, 0, 0, 7),
            };
            Ui.Ref(title, WpfControls.TextBlock.ForegroundProperty, Ui.InkKey);

            var area = new WpfControls.Grid { Margin = new Thickness(0, 0, 0, 18) };
            area.RowDefinitions.Add(new WpfControls.RowDefinition { Height = GridLength.Auto });
            area.RowDefinitions.Add(new WpfControls.RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
            WpfControls.Grid.SetRow(title, 0);
            WpfControls.Grid.SetRow(_grid, 1);
            area.Children.Add(title);
            area.Children.Add(_grid);
            return area;
        }

        void AddColumn(string header, string binding, double width)
        {
            _grid.Columns.Add(new WpfControls.DataGridTextColumn
            {
                Header = header,
                Binding = new System.Windows.Data.Binding(binding),
                Width = new WpfControls.DataGridLength(width),
            });
        }

        public void Refresh(Snapshot snap)
        {
            if (snap == null) return;
            _big.Text = Ui.LabelOf(snap.State);
            _accent.Background = Ui.BrushOf(snap.Color);
            var s = snap.Session;
            _sub.Text = s == null
                ? "没有找到 dsh 会话记录"
                : $"{s.Project} · 静默 {Math.Round(s.AgeSec)} 秒 · 余额 {snap.StripRight ?? "--"} · 共 {Count(snap)} 个会话";
            _grid.ItemsSource = SessionView.Of(snap.Sessions);
            // 整串算完一次赋值：Refresh 每帧都进来，`_waiting.Text +=` 会把历史帧的
            // 待处理条目一路叠下去（同一件事每帧叠一遍，几分钟后这一屏全是重复）。
            _waiting.Text = WaitingText(snap);
        }

        static string WaitingText(Snapshot snap)
        {
            var items = snap.Waiting;
            if (items == null || items.Count == 0) return "没有待处理的问题";
            var parts = new List<string>(items.Count);
            foreach (var w in items) parts.Add($"{w.Project}：{w.Text}");
            return string.Join("\n", parts);
        }

        static int Count(Snapshot snap) => snap.Sessions?.Count ?? 0;
    }

    /// <summary>
    /// 给 DataGrid 用的展示层投影：把原始字段变成中文可读文本。
    /// 表格的每一列都绑到这里的一个 string —— 直出原始值会出「133.9 与 30517 混排且无单位」
    /// 那种排（Task 4 复核顺延项 2），所以 AgeSec/Turn/Usage 都在这一层格式化。
    /// </summary>
    internal sealed class SessionView
    {
        public string Project { get; set; }
        public string StateLabel { get; set; }
        public string TurnText { get; set; }
        public string AgeText { get; set; }
        public string TodoText { get; set; }
        public string LastTool { get; set; }
        public string UsageText { get; set; }
        public string Title { get; set; }

        public static IList<SessionView> Of(List<SessionRow> rows)
        {
            var list = new List<SessionView>();
            if (rows == null) return list;
            foreach (var r in rows)
            {
                list.Add(new SessionView
                {
                    Project = r.Project,
                    StateLabel = Ui.LabelOf(r.State),
                    TurnText = r.Turn.HasValue ? $"T{r.Turn}" + (r.Step.HasValue ? $"/S{r.Step}" : "") : "",
                    AgeText = Age(r.AgeSec),
                    TodoText = r.Todo == null || r.Todo.Total == 0 ? "—" : $"{r.Todo.Done}/{r.Todo.Total}",
                    LastTool = string.IsNullOrEmpty(r.LastTool) ? "—" : r.LastTool,
                    // 本机 33 个会话的 usage 全是 0（dsh 只在成功响应里写 token 数），
                    // 空着会让整列看着像坏了，所以统一给一个「—」。
                    UsageText = r.Usage?.Total > 0 ? r.Usage.Total.ToString("N0") : "—",
                    Title = string.IsNullOrEmpty(r.Title) ? "—" : r.Title,
                });
            }
            return list;
        }

        /// <summary>
        /// 秒 → 人话。原来这一列直出 <c>Math.Round(age_sec) + \"s</c>，于是同一列里
        /// 既有 <c>26s</c> 又有 <c>22395s</c>：后者要心算才知道是六小时。
        /// 只在两个数量级之间换单位，超过一天用 d。
        /// </summary>
        static string Age(double sec)
        {
            if (sec < 0 || double.IsNaN(sec) || double.IsInfinity(sec)) return "—";
            if (sec < 60) return $"{(long)sec}s";
            if (sec < 3600) return $"{(long)(sec / 60)}m";
            if (sec < 86400) return $"{(long)(sec / 3600)}h{(long)(sec % 3600 / 60):D2}m";
            return $"{(long)(sec / 86400)}d{(long)(sec % 86400 / 3600)}h";
        }
    }
}
