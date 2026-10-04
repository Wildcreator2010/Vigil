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

    // NotifyPage / RuntimePage 已在 Task 6 迁到 Pages/NotifyPage.cs 与 Pages/RuntimePage.cs，
    // BalancePage 在 Task 7 迁到 Pages/BalancePage.cs（都是真实控件），
    // 这里别再留同名占位 —— 留着就是 CS0101 重复定义。
    //
    // OverviewPage 在 Task 8 迁到 Pages/OverviewPage.cs 并填实（状态卡 + 会话表 + 待处理）。
    // 【Task 5 Step 0 #1 的白底豁免已在同一个提交里收口】原先豁免的是那张表写死的
    // `Brushes.White` / `#E2E5EA`，八处以逐字节 #FFFFFF 为判据的像素口径清单
    // （shot_table_stats 的 painted/ink/hlines/vscroll、real_window_stats 的顶沿/表头带/
    // 分隔线、row_data_gate 的相对基线）现在已全部改成「按当前主题现量纸色」：
    // 离屏侧认出图里最常见的不透明色当纸色，真窗口侧认出取样带里的众数色当纸色，
    // 深色主题下跟着走，不再钉白色。反证（表底换回写死白、断言不同步改）见
    // task-8-report.md；新增的 `概览页表底已交回主题键` 那条是这一收口的守门人。
    internal sealed class AboutPage : PageBase { public AboutPage() : base("关于") { } }
}

