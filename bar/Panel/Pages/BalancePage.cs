using System.Windows;
using WpfControls = System.Windows.Controls;
using UiControls = Wpf.Ui.Controls;

namespace DshBar
{
    /// <summary>
    /// 余额页：余额 Key 的录入 / 清除，和余额本身的状态与手动刷新。
    ///
    /// 这一页顶掉的是原来的独立弹窗 `KeyDialog`（Task 7）：同一功能不留两处入口，
    /// 而且弹窗那份把「留空并保存 = 清除」做成一个动作里的两个分支，
    /// 拆成两颗按钮以后「清除已存 Key」是明确的一次点击，不靠用户留空这个隐性约定。
    ///
    /// 安全口径（本页唯一不能让步的地方）：
    /// ① Key 只进不出。PasswordBox 的值只交给 App.SaveBalanceKey（→ 引擎 stdin → DPAPI），
    ///    页面上没有任何一处把它写回控件、标题、提示或日志；点完「保存」立刻 Clear()。
    ///    面板也不会显示「已保存的 Key」——它显示的是**来源**（dpapi / env:… / file:… / none），
    ///    够用户判断生效的是哪一份凭据，不够任何人拿走那一份凭据。
    /// ② 加解密全在引擎侧（dsh_state.py 的 save_balance_key），C# 这边不落任何 Key 文件。
    ///
    /// 结构契约与外观页/通知页/运行页同源（Task 5、6 立下的三条）：
    /// 颜色只走主题键（Ui.Ref）、页面自带 ScrollViewer + 铺满的主题底色 Border
    /// （外壳已不代劳整页滚动）、构造期零副作用（--panel-shot 也会建页，
    /// 点按钮才会起子进程 —— 出图路径上不可能触发保存）。
    /// </summary>
    internal sealed class BalancePage : WpfControls.ContentControl, IPanelPage
    {
        /// <summary>录入框：WPF 的 PasswordBox 不回显内容，UIA 也读不到它的值
        /// （PasswordBoxAutomationPeer 刻意不通过 Value/Text 模式暴露密码）。</summary>
        readonly WpfControls.PasswordBox _key = new WpfControls.PasswordBox { Width = 300 };

        /// <summary>余额那一行：跟着最近一帧快照刷新，只有文本、不含任何 Key。</summary>
        readonly WpfControls.TextBlock _state = new WpfControls.TextBlock
        {
            FontSize = 12,
            Margin = new Thickness(14, 11, 14, 11),
            TextWrapping = TextWrapping.Wrap,
            Text = "还没有余额信息，等引擎的第一帧。",
        };

        public BalancePage()
        {
            Ui.Ref(_state, WpfControls.TextBlock.ForegroundProperty, Ui.InkDimKey);

            var save = PushButton("保存（当前用户加密）", () =>
            {
                string k = _key.Password == null ? "" : _key.Password.Trim();
                if (k.Length == 0) return;   // 空输入什么都不做：清除有专门的按钮
                App.SaveBalanceKey(k);
                _key.Clear();
            });

            var clear = PushButton("清除已存 Key", () =>
            {
                App.ClearBalanceKey();
                _key.Clear();
                _state.Text = "已请求清除已保存的 Key。";
            });

            var refresh = PushButton("立即刷新余额", () => App.RequestBalanceRefresh());

            var scroll = new WpfControls.ScrollViewer
            {
                HorizontalScrollBarVisibility = WpfControls.ScrollBarVisibility.Disabled,
                VerticalScrollBarVisibility = WpfControls.ScrollBarVisibility.Auto,
            };
            scroll.Content = Ui.Column(
                Ui.Heading("余额"),
                Ui.Group("Key",
                    Ui.Row("录入余额 Key",
                        "平台控制台 → API Keys → 可查询余额的 Key。用当前 Windows 账户的 DPAPI " +
                        "加密保存在数据目录，不是明文；保存后输入框立刻清空，面板不会再显示它。",
                        Ui.Row2(_key, save, clear)),
                    Ui.Row("生效优先级",
                        "环境变量 DEEPSEEK_BALANCE_KEY > DEEPSEEK_API_KEY > 已保存的加密 Key > 明文文件。" +
                        "环境变量存在时会盖住已保存的 Key。", null),
                    _state),
                Ui.Group("查询",
                    Ui.Row("刷新", "默认 5 分钟查一次，这里可以立刻重查。", refresh)));
            HorizontalContentAlignment = HorizontalAlignment.Stretch;
            VerticalContentAlignment = VerticalAlignment.Stretch;
            // 底色必须是会真的绘制 Background 的元素（裸 ContentControl 的模板只有
            // ContentPresenter）—— 见 AppearancePage.cs / NotifyPage.cs 的同款注释。
            var backdrop = new WpfControls.Border { Child = scroll };
            Ui.Ref(backdrop, WpfControls.Border.BackgroundProperty, Ui.PageKey);
            Content = backdrop;
        }

        /// <summary>一个动作按钮：文本 + 点击。Wpf.Ui 的 Button 自带主题样式，前景不用再接键。</summary>
        static FrameworkElement PushButton(string text, System.Action onClick)
        {
            var b = new UiControls.Button { Content = text, Padding = new Thickness(12, 5, 12, 5), Margin = new Thickness(8, 0, 0, 0) };
            b.Click += (s, e) => onClick();
            return b;
        }

        /// <summary>
        /// 只有 balance 这一格空着（--no-balance 的帧、或面板在第一帧之前打开）才不动；
        /// available=false 是合法状态，要如实写出错误与来源。
        /// </summary>
        public void Refresh(Snapshot snap)
        {
            var b = snap?.Balance;
            if (b == null) return;
            string src = string.IsNullOrEmpty(b.Source) ? "未知" : b.Source;
            _state.Text = b.Available
                ? $"当前余额 {b.Currency} {b.Total}（来源 {src}）"
                : $"余额不可用：{b.Error}（来源 {src}）";
        }
    }
}
