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
            Background = Brushes.Transparent;
            Content = new WpfControls.TextBlock
            {
                Text = title,
                FontSize = 16,
                Foreground = new SolidColorBrush(Color.FromRgb(0x1A, 0x1D, 0x23)),
            };
        }

        public virtual void Refresh(Snapshot snap) { }
    }

    internal sealed class OverviewPage : PageBase { public OverviewPage() : base("概览") { } }
    internal sealed class NotifyPage : PageBase { public NotifyPage() : base("通知") { } }
    internal sealed class AppearancePage : PageBase { public AppearancePage() : base("外观") { } }
    internal sealed class RuntimePage : PageBase { public RuntimePage() : base("运行") { } }
    internal sealed class BalancePage : PageBase { public BalancePage() : base("余额") { } }
    internal sealed class AboutPage : PageBase { public AboutPage() : base("关于") { } }
}
