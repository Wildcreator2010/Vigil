using System.Windows;
using WpfControls = System.Windows.Controls;
using UiControls = Wpf.Ui.Controls;

namespace Vigil
{
    /// <summary>
    /// 外观页：主题单选（浅/深/跟随系统）+ 任务栏状态条开关。
    /// 每处改动都立刻 App.ApplyTheme()/ApplyBarVisibility() 生效并 SaveSettings() 落盘，
    /// 页实例在 PanelWindow 里是缓存的 —— 所以颜色全走 Ui 的主题键引用（DynamicResource），
    /// 切主题不重建页面也能当场换装。
    ///
    /// 页面**自带 ScrollViewer**（Global Constraints ①：外壳已不再代劳整页滚动，
    /// Host 拿到的是有限高度）：内容超出视口时页内可滚，不再被裁。
    /// ScrollViewer 的底色挂 ApplicationBackgroundBrush —— 真实窗口里它和外壳同键同色，
    /// 看不出多铺了一层；离屏 --panel-shot 时它保证画面有**可采样的主题底色**
    /// （占位页整页透明，深浅两张无从分辨，Task 3 复核 N3 的缺口就在这）。
    /// </summary>
    internal sealed class AppearancePage : WpfControls.ContentControl, IPanelPage
    {
        public AppearancePage()
        {
            var cfg = App.Config;

            var light = Radio("浅色", "light", cfg.Theme);
            var dark = Radio("深色", "dark", cfg.Theme);
            var sys = Radio("跟随系统", "system", cfg.Theme);
            // Checked 在 IsChecked 初值赋完之后再挂：否则构造时点亮默认项会当场
            // SaveSettings()/ApplyTheme()，离屏出图顺带把用户的 settings.json 规范化重写一遍。
            foreach (var rb in new[] { light, dark, sys })
                rb.Checked += (s, e) =>
                {
                    App.Config.Theme = (string)((WpfControls.RadioButton)s).Tag;
                    App.ApplyTheme();
                    App.SaveSettings();
                };

            var showBar = new UiControls.ToggleSwitch
            {
                IsChecked = cfg.ShowBar,
                OnContent = "显示",
                OffContent = "隐藏",
            };
            showBar.Checked += (s, e) => SetBar(true);
            showBar.Unchecked += (s, e) => SetBar(false);

            // 三档界面材质：云母 / 毛玻璃 / 自绘液态玻璃。点了当场生效，不用重开面板。
            var mica = Radio("云母 Mica", "mica", cfg.Backdrop);
            var acrylic = Radio("毛玻璃 Acrylic", "acrylic", cfg.Backdrop);
            var glass = Radio("液态玻璃", "glass", cfg.Backdrop);
            foreach (var rb in new[] { mica, acrylic, glass })
                rb.Checked += (s, e) =>
                {
                    App.Config.Backdrop = Settings.SafeBackdrop((string)((WpfControls.RadioButton)s).Tag);
                    App.ApplyTheme();
                    App.SaveSettings();
                };

            var scroll = new WpfControls.ScrollViewer
            {
                HorizontalScrollBarVisibility = WpfControls.ScrollBarVisibility.Disabled,
                VerticalScrollBarVisibility = WpfControls.ScrollBarVisibility.Auto,
            };
            scroll.Content = Ui.Column(
                Ui.Heading("外观"),
                Ui.Group("主题",
                    Ui.Row("配色",
                        "面板默认浅色（白底黑字）；任务栏状态条始终跟随系统深浅色，不受这里影响。",
                        Ui.Row2(light, dark, sys))),
                Ui.Group("显示模式",
                    Ui.Row("界面材质",
                        "云母最实、只染一点桌面色；毛玻璃真的会把身后模糊掉；液态玻璃是半透明卡片叠大圆角" +
                        "加一圈内高光，看着最透。换档当场生效。",
                        Ui.Row2(mica, acrylic, glass)),
                    Ui.Row("任务栏状态条", "隐藏后仍可通过托盘图标打开本面板。", showBar)));
            // 主题底色用 Border 铺满整页：裸 ContentControl 的默认模板里没有画 Background 的
            // chrome（只有 ContentPresenter），把 Background 挂在它身上等于没挂——离屏实测
            // 四角仍全透明；Border 是确定会绘制 Background 的元素。真实窗口里它和外壳同键
            // 同色看不出多铺一层；离屏 --panel-shot 时四角采到的就是主题底色，
            // 深浅两张从这里才分得开（N3 补口门禁的取样点）。
            HorizontalContentAlignment = HorizontalAlignment.Stretch;
            VerticalContentAlignment = VerticalAlignment.Stretch;
            var backdrop = new WpfControls.Border { Child = scroll };
            Ui.Ref(backdrop, WpfControls.Border.BackgroundProperty, Ui.PageKey);
            Content = backdrop;
        }

        static WpfControls.RadioButton Radio(string text, string value, string current)
        {
            // 必须用 WPF-UI 的 Fluent RadioButton：原生那个的选中点直接取**系统主题色**，
            // 不读 WPF-UI 的强调色资源，所以覆盖 AccentFillColor* 对它无效
            // （第一版就是踩在这上面，选中圈一直是系统紫）。
            var rb = new WpfControls.RadioButton
            {
                Content = text,
                Tag = value,
                GroupName = "theme",
                Margin = new Thickness(0, 0, 14, 0),
                IsChecked = current == value,
            };
            // 原生 RadioButton 不在 WPF-UI 重style范围内，文字前景自己接主题键，
            // 否则深色下默认黑字贴在深色底上看不见。
            Ui.Ref(rb, WpfControls.RadioButton.ForegroundProperty, Ui.InkKey);
            return rb;
        }

        void SetBar(bool on)
        {
            App.Config.ShowBar = on;
            App.ApplyBarVisibility();
            App.SaveSettings();
        }

        public void Refresh(Snapshot snap) { }
    }
}
