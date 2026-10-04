using System;
using System.Windows;
using WpfControls = System.Windows.Controls;
using UiControls = Wpf.Ui.Controls;

namespace Vigil
{
    /// <summary>
    /// 通知页：系统通知总开关 + 「需要操作」的重复提醒间隔。
    ///
    /// 三项契约都照 Task 5 立下的形状来：
    /// ① 颜色只走主题键（Ui.Ref）—— 页实例被 PanelWindow 缓存，写死 Brush 切主题不重解析；
    /// ② 页面自带 ScrollViewer + 铺满的主题底色 Border —— 外壳（PanelWindow.xaml）已经不再
    ///    把 Host 包进 ScrollViewer，页面拿到的是有限高度，超了就得自己滚；
    /// ③ 先赋初值、后订阅事件 —— --panel-shot 也会构造页，构造期写盘会把用户的
    ///    settings.json 顺手规范化重写一遍（AppearancePage.cs 同款处理）。
    ///
    /// notify / repeatSec 都是**热生效**（spec §6）：写的就是常驻进程在用那个 Settings 单例，
    /// App.Notify 每帧现读，所以这里不调 RestartEngine —— 只有 interval 才需要重启引擎。
    /// </summary>
    internal sealed class NotifyPage : WpfControls.ContentControl, IPanelPage
    {
        public NotifyPage()
        {
            var cfg = App.Config;

            var notify = new UiControls.ToggleSwitch { OnContent = "开", OffContent = "关" };
            notify.IsChecked = cfg.Notify;
            notify.Checked += (s, e) => Save(true, null);
            notify.Unchecked += (s, e) => Save(false, null);

            // 0 表示不重复（App.Notify 的重复分支要求 RepeatSec > 0），所以下限是 0 不是 1。
            // SpinButtonPlacementMode=Inline：默认 Hidden 时只能靠键盘输入，
            // 鼠标点不了加减档（也正因为默认不显示，冒烟要用 UIA 步进就得先打开它）。
            var repeat = new UiControls.NumberBox
            {
                Width = 150,
                Minimum = 0,
                Maximum = 3600,
                SmallChange = 1,
                SpinButtonPlacementMode = UiControls.NumberBoxSpinButtonPlacementMode.Inline,
            };
            repeat.Value = cfg.RepeatSec;
            // ValueChanged 只在「回车 / 点加减档 / 失焦」时触发（Wpf.Ui.xml 的事件说明），
            // 不是每敲一个数字就写一次盘。清空成 null 按 0（不重复）落，而不是偷偷回到 90。
            repeat.ValueChanged += (s, e) => Save(null, (int)(e.NewValue ?? 0));

            var scroll = new WpfControls.ScrollViewer
            {
                HorizontalScrollBarVisibility = WpfControls.ScrollBarVisibility.Disabled,
                VerticalScrollBarVisibility = WpfControls.ScrollBarVisibility.Auto,
            };
            scroll.Content = Ui.Column(
                Ui.Heading("通知"),
                Ui.Group("系统通知",
                    Ui.Row("托盘气泡提醒",
                        "「需要操作」与「出错了」时弹系统通知；回答完成只在从活动态转入时提醒一次。",
                        notify),
                    Ui.Row("重复提醒间隔（秒）",
                        "「需要操作」一直没人处理时，按这个间隔再提醒一次；0 表示不重复。",
                        repeat)));
            // 底色必须是会真的绘制 Background 的元素（裸 ContentControl 的模板只有
            // ContentPresenter，挂 Background 等于没挂）—— 见 AppearancePage.cs 的同款注释。
            HorizontalContentAlignment = HorizontalAlignment.Stretch;
            VerticalContentAlignment = VerticalAlignment.Stretch;
            var backdrop = new WpfControls.Border { Child = scroll };
            Ui.Ref(backdrop, WpfControls.Border.BackgroundProperty, Ui.PageKey);
            Content = backdrop;
        }

        /// <summary>写内存单例并落盘；两者都热生效，不涉及引擎重启。</summary>
        static void Save(bool? notify, int? repeatSec)
        {
            if (notify.HasValue) App.Config.Notify = notify.Value;
            if (repeatSec.HasValue) App.Config.RepeatSec = Math.Max(0, repeatSec.Value);
            App.SaveSettings();
        }

        public void Refresh(Snapshot snap) { }
    }
}
