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
    ///
    /// 【Task 5 Step 0 #1 的明文豁免：表格白底暂留写死色】
    /// `Brushes.White` / `#E2E5EA` 与 c35cfbf 的主题刷裁决确有冲突 —— 深色主题下这页是
    /// 「深色外壳 + 白底表格」（Task 5 真窗口截图为证），本豁免承认这个过渡态，理由与边界：
    /// ① 实测（Task 5 离屏取样）：候选主题键在浅底下合成 #FEFEFE 而非 #FFFFFF
    ///   （CardBackgroundFillColorDefault = #B3FEFEFE 半透明白贴在 #FAFAFA 上），
    ///   而 Task 4 签认的四条像素门禁（shot_table_stats 的 painted/ink/hlines/vscroll 的
    ///   「非白」判据、real_window_stats 的顶沿/表头带/整页滚动条指纹、row_data_gate 的
    ///   相对基线）全部钉在**逐字节 #FFFFFF** 上；换刷即八处口径集体失效，
    ///   按 Global Constraints ② 必须同轮全部重derive —— 那正是 Task 8「概览页填实
    ///   （交互与配色）」的验收面（3803390 已把顺延项落成 Task 8 显式验收项）。
    /// ② 主题生效链路本身不靠这张表守：「theme=dark 与 light 的 appearance 四角取样必须
    ///   不同」那条门禁（smoke_test.check_settings_roundtrip）在删掉字典合并或 ApplyTheme
    ///   时翻红，白底豁免不会让 N3 缺口复发。
    /// ③ 豁免范围只有这两个字面量。Task 8 换主题刷时，必须在同一提交里把
    ///   smoke_test.py 的白口径改成「按当前主题现量纸色」或钉浅主题跑，两处一起走。
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

    // NotifyPage / RuntimePage 已在 Task 6 迁到 Pages/NotifyPage.cs 与 Pages/RuntimePage.cs，
    // BalancePage 在 Task 7 迁到 Pages/BalancePage.cs（都是真实控件），
    // 这里别再留同名占位 —— 留着就是 CS0101 重复定义。
    internal sealed class AboutPage : PageBase { public AboutPage() : base("关于") { } }
}
