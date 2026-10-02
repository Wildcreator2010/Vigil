using System;
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
        static readonly string[] Keys = { "overview", "notify", "appearance", "runtime", "balance", "about" };
        static readonly string[] Labels = { "概览", "通知", "外观", "运行", "余额", "关于" };

        readonly Dictionary<string, Win.FrameworkElement> _pages =
            new Dictionary<string, Win.FrameworkElement>();
        bool _navigating;
        bool _reallyClosing;

        public static PanelWindow Instance { get; private set; }

        public PanelWindow()
        {
            InitializeComponent();
            for (int i = 0; i < Keys.Length; i++)
                Nav.Items.Add(new WpfControls.ListBoxItem { Content = Labels[i], Tag = Keys[i] });
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
            int idx = Array.IndexOf(Keys, key);
            if (idx < 0) idx = 0;
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
            string key = Keys[index];
            if (!_pages.TryGetValue(key, out var page))
            {
                page = Build(key);
                _pages[key] = page;
            }
            Host.Content = page;
            if (page is IPanelPage p) p.Refresh(App.Latest);
        }

        static Win.FrameworkElement Build(string key)
        {
            switch (key)
            {
                case "notify": return new NotifyPage();
                case "appearance": return new AppearancePage();
                case "runtime": return new RuntimePage();
                case "balance": return new BalancePage();
                case "about": return new AboutPage();
                default: return new OverviewPage();
            }
        }
    }

    internal interface IPanelPage
    {
        void Refresh(Snapshot snap);
    }
}
