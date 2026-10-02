using System.Collections.Generic;
using Win = System.Windows;
using WpfControls = System.Windows.Controls;

namespace DshBar
{
    /// PanelWindow 必须是 public：WPF 标记编译器为 x:Class 生成的那半个分部写死了
    /// `public partial class`，代码侧声明成 internal 会撞 CS0262（分部可访问性冲突）。
    /// BarWindow/KeyDialog 那种纯代码窗口不受此限，仍是 internal。
    public sealed partial class PanelWindow : Wpf.Ui.Controls.FluentWindow
    {
        readonly Dictionary<string, Win.FrameworkElement> _pages =
            new Dictionary<string, Win.FrameworkElement>();
        bool _navigating;
        bool _reallyClosing;

        public static PanelWindow Instance { get; private set; }

        public PanelWindow()
        {
            InitializeComponent();
            for (int i = 0; i < PanelPages.Keys.Length; i++)
                Nav.Items.Add(new WpfControls.ListBoxItem { Content = PanelPages.Labels[i], Tag = PanelPages.Keys[i] });
            Nav.SelectedIndex = 0;
            Instance = this;
            App.SnapshotChanged += OnSnapshotChanged;
            // 关窗只是藏起来：视觉树留着，下次打开不重建；真正销毁只在退出时
            Closing += (s, e) =>
            {
                if (!_reallyClosing) { e.Cancel = true; Hide(); }
            };
        }

        /// <summary>只在 App.Cleanup 里调用，绕开上面的拦截。</summary>
        internal void ForceClose()
        {
            _reallyClosing = true;
            App.SnapshotChanged -= OnSnapshotChanged;
            if (Instance == this) Instance = null;
            Close();
        }

        void OnSnapshotChanged() => Dispatcher.Invoke(() =>
        {
            if (Host.Content is IPanelPage p) p.Refresh(App.Latest);
        });

        public void ShowOn(string key)
        {
            Navigate(key ?? "overview");
            if (!IsVisible) Show();
            Activate();
        }

        void Navigate(string key)
        {
            int idx = PanelPages.IndexOf(key);
            if (Nav.SelectedIndex == idx) { Render(idx); return; }
            _navigating = true;
            Nav.SelectedIndex = idx;
            _navigating = false;
        }

        void OnNavChanged(object sender, WpfControls.SelectionChangedEventArgs e)
        {
            if (!_navigating && Nav.SelectedIndex >= 0) Render(Nav.SelectedIndex);
        }

        void Render(int index)
        {
            string key = PanelPages.Keys[index];
            if (!_pages.TryGetValue(key, out var page))
            {
                // key 来自 PanelPages.Keys，TryCreate 必定成功；这个兜底只是防住「以后加了 key
                // 却忘了在工厂里登记」这类改动，导航路径不能因为找不到页就开不出窗口。
                if (!PanelPages.TryCreate(key, out page)) page = new OverviewPage();
                _pages[key] = page;
            }
            Host.Content = page;
            if (page is IPanelPage p) p.Refresh(App.Latest);
        }
    }

    internal interface IPanelPage
    {
        void Refresh(Snapshot snap);
    }
}
