using System;
using System.Collections.Generic;
using Win = System.Windows;
using WpfControls = System.Windows.Controls;

namespace Vigil
{
    /// PanelWindow 必须是 public：WPF 标记编译器为 x:Class 生成的那半个分部写死了
    /// `public partial class`，代码侧声明成 internal 会撞 CS0262（分部可访问性冲突）。
    /// BarWindow 那种纯代码窗口不受此限，仍是 internal。
    public sealed partial class PanelWindow : Wpf.Ui.Controls.FluentWindow
    {
        readonly Dictionary<string, Win.FrameworkElement> _pages =
            new Dictionary<string, Win.FrameworkElement>();
        bool _navigating;
        bool _reallyClosing;
        // 想要哪一档系统背衬（Native.DWMSBT_*）。构造期还没有 HWND，所以先记着，
        // SourceInitialized 时再补上去 —— 见 ApplyBackdrop()。
        int _backdrop = Native.DWMSBT_MAINWINDOW;
        bool _backdropHooked;

        public static PanelWindow Instance { get; private set; }

        public PanelWindow()
        {
            InitializeComponent();
            for (int i = 0; i < PanelPages.Keys.Length; i++)
                Nav.Items.Add(new WpfControls.ListBoxItem { Content = PanelPages.Labels[i], Tag = PanelPages.Keys[i] });
            Nav.SelectedIndex = 0;
            // 版本代号写在侧栏底部：用户要的是「版本号叫 Vachellia farnesiana」，
            // 它得在界面上看得见，不能只躺在 csproj 里。
            VersionLine.Text = App.VersionText;
            Instance = this;
            App.SnapshotChanged += OnSnapshotChanged;
            // 新建的窗口不会自己继承上次 ApplyTheme 时设过的材质，得当场补一次；
            // 否则「关了再开」会退回 XAML 里写死的那一档。
            ApplyMaterial();
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

        /// <summary>
        /// 按当前材质重画外壳：窗口背衬、侧栏通透度、以及每一张卡。
        ///
        /// 背衬走 <see cref="Native.SetSystemBackdrop"/>，**不碰** FluentWindow.WindowBackdropType：
        /// 那个属性要求先 ExtendsContentIntoTitleBar=true，否则赋值当场就抛
        /// InvalidOperationException —— 而它在构造函数里就被调过一次，于是整个控制台在任何
        /// 机器上都开不出来（SafeBackdrop 的默认值是 "acrylic"，连"设置里没这一项"都躲不过）。
        /// 实测细节与理由见 Native.cs 里那段注释。
        ///
        /// glass 档的系统材质仍是 Mica：WPF-UI 4.2 没有 Liquid Glass，
        /// 「液态」是靠侧栏与卡片那两层半透明 + 大圆角 + 内高光近似出来的。
        /// </summary>
        internal void ApplyMaterial()
        {
            string b = Settings.SafeBackdrop(App.Config?.Backdrop);
            _backdrop = b == "mica" || b == "glass"
                ? Native.DWMSBT_MAINWINDOW
                : Native.DWMSBT_TRANSIENTWINDOW;
            ApplyBackdrop();
            Ui.Glass = b == "glass";
            if (Ui.Glass)
            {
                // 侧栏跟着透：Mica 会把它染上桌面色调，三档里只有这一档看得见"融进去"
                Rail.Background = new System.Windows.Media.SolidColorBrush(
                    Wpf.Ui.Appearance.ApplicationThemeManager.GetAppTheme()
                        == Wpf.Ui.Appearance.ApplicationTheme.Dark
                        ? System.Windows.Media.Color.FromArgb(0x66, 0x20, 0x20, 0x20)
                        : System.Windows.Media.Color.FromArgb(0x66, 0xFF, 0xFF, 0xFF));
            }
            else
            {
                Rail.SetResourceReference(
                    WpfControls.Border.BackgroundProperty, "LayerFillColorDefaultBrush");
            }
            Ui.RefreshMaterial();
        }

        /// <summary>
        /// 把 <see cref="_backdrop"/> 落到真实窗口上。背衬是 DWM 的属性，必须有 HWND，
        /// 而 ApplyMaterial() 在构造期就会被调一次（那时还没有），所以这里挂一次
        /// SourceInitialized 补上；之后再改材质就是当场生效。
        /// 不支持的系统（Win10 / 早于 22H2 的 Win11）返回失败 HRESULT：只记一行日志，
        /// 窗口照常可用 —— 材质是观感，不是功能。
        /// </summary>
        void ApplyBackdrop()
        {
            var h = new System.Windows.Interop.WindowInteropHelper(this).Handle;
            if (h == IntPtr.Zero)
            {
                if (!_backdropHooked)
                {
                    _backdropHooked = true;
                    SourceInitialized += (s, e) => ApplyBackdrop();
                }
                return;
            }
            int hr = Native.SetSystemBackdrop(h, _backdrop);
            if (hr != 0) App.Log($"系统背衬未生效（HRESULT 0x{hr:X8}），窗口照常可用");
        }

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
            // _navigating 只用来压掉 SelectedIndex 赋值同步触发的 OnNavChanged（防双份 Render）；
            // 真正的渲染必须在这里自己做。Task 3 起的写法把赋值和渲染一起压掉了 ——
            // `Vigil.exe --panel appearance` 导航条亮在「外观」、Host 里却还躺着构造时
            // Render(0) 塞进去的概览页（真窗口截图实证）。默认页恰好是 0 号，
            // 所以冷打开与旧冒烟从没暴露过它。
            _navigating = true;
            Nav.SelectedIndex = idx;
            _navigating = false;
            Render(idx);
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
