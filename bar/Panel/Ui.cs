using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Media;
using WpfControls = System.Windows.Controls;

namespace Vigil
{
    /// <summary>
    /// 控制台的分组建块：主题卡片 + 分隔线，行是「标题/说明 + 右侧控件」。
    /// 对齐 AF-Media-Bar 的 SettingsGroup / SettingsRow 词汇，但全部自绘，
    /// 不用 WPF-UI 的 CardControl（它是 ButtonBase，整卡可点的语义不对）。
    ///
    /// 颜色一律 SetResourceReference 走 WPF-UI 主题字典的键（c35cfbf 的主题刷裁决）：
    /// 简报 Step 1 给的是 `Ui.Ink / InkDim / CardBg / CardLine / PageBg` 五个写死的
    /// frozen SolidColorBrush —— 那在深浅切换下不会跟着变（切主题时 DynamicResource 重解析，
    /// 静态字段做不到），外壳 PanelWindow.xaml 的注释也点名「写死色 → 深色外壳 + 纯白内容区」。
    /// 所以这里把画刷常量换成**资源键常量**，页面建好后的任何主题切换都当场生效。
    /// 键名以 Wpf.Ui 4.2.0 实际提供的为准（TaskMaterialFillColorSecondary 这类
    /// 简报/计划里点名的键在包里根本不存在，已用 strings 数过 DLL）。
    /// </summary>
    internal static class Ui
    {
        public const string InkKey = "TextFillColorPrimaryBrush";
        public const string InkDimKey = "TextFillColorSecondaryBrush";
        public const string CardKey = "CardBackgroundFillColorDefaultBrush";
        public const string LineKey = "CardStrokeColorDefaultBrush";
        public const string PageKey = "ApplicationBackgroundBrush";

        /// <summary>把某个元素的某根依赖属性接到主题键上；后续页面填实时统一用这个入口。
        /// 参数类型必须是 FrameworkElement：SetResourceReference 定义在它身上，
        /// 裸 DependencyObject 没有这个方法（CS1061，本机踩过）。</summary>
        internal static void Ref(FrameworkElement target, DependencyProperty dp, string key)
        {
            target.SetResourceReference(dp, key);
        }

        /// <summary>Ui.Row 的右侧只收一个元素，需要横排（输入框 + 两颗按钮、一组单选）时包这一层。</summary>
        public static FrameworkElement Row2(params FrameworkElement[] items)
        {
            var sp = new WpfControls.StackPanel { Orientation = WpfControls.Orientation.Horizontal };
            foreach (var i in items) sp.Children.Add(i);
            return sp;
        }

        public static FrameworkElement Row(string title, string desc, FrameworkElement field)
        {
            var grid = new WpfControls.Grid { Margin = new Thickness(14, 11, 14, 11) };
            grid.ColumnDefinitions.Add(new WpfControls.ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new WpfControls.ColumnDefinition { Width = GridLength.Auto });

            var text = new WpfControls.StackPanel { VerticalAlignment = VerticalAlignment.Center };
            var titleText = new WpfControls.TextBlock
            {
                Text = title,
                FontSize = 13.5,
                FontWeight = FontWeights.SemiBold,
            };
            Ref(titleText, WpfControls.TextBlock.ForegroundProperty, InkKey);
            text.Children.Add(titleText);
            if (!string.IsNullOrEmpty(desc))
            {
                var descText = new WpfControls.TextBlock
                {
                    Text = desc,
                    FontSize = 12,
                    Margin = new Thickness(0, 3, 0, 0),
                    TextWrapping = TextWrapping.Wrap,
                };
                Ref(descText, WpfControls.TextBlock.ForegroundProperty, InkDimKey);
                text.Children.Add(descText);
            }
            grid.Children.Add(text);

            if (field != null)
            {
                field.VerticalAlignment = VerticalAlignment.Center;
                field.Margin = new Thickness(16, 0, 0, 0);
                WpfControls.Grid.SetColumn(field, 1);
                grid.Children.Add(field);
            }
            return grid;
        }

        /// <summary>
        /// 长取值专用的堆叠行：标题 + 说明在上，取值独占一整行、左对齐。
        ///
        /// 为什么不用 <see cref="Row"/>：Row 的右列是 Auto，给它限宽（阈值那格原来是
        /// MaxWidth 320 + 右对齐换行）就把左侧说明挤掉一半宽，724 DIP 的页面里两坨文字
        /// 互相咬住。取值本身是一长串「当前判定：… | 会话 … | 静默 … | 本次扫描 …」，
        /// 天生就该整行铺开，不该塞在按钮旁边那一格里。
        /// </summary>
        public static FrameworkElement StackedRow(string title, string desc, FrameworkElement value)
        {
            var sp = new WpfControls.StackPanel { Margin = new Thickness(14, 11, 14, 11) };
            var titleText = new WpfControls.TextBlock
            {
                Text = title,
                FontSize = 13.5,
                FontWeight = FontWeights.SemiBold,
            };
            Ref(titleText, WpfControls.TextBlock.ForegroundProperty, InkKey);
            sp.Children.Add(titleText);
            if (!string.IsNullOrEmpty(desc))
            {
                var descText = new WpfControls.TextBlock
                {
                    Text = desc,
                    FontSize = 12,
                    Margin = new Thickness(0, 3, 0, 0),
                    TextWrapping = TextWrapping.Wrap,
                };
                Ref(descText, WpfControls.TextBlock.ForegroundProperty, InkDimKey);
                sp.Children.Add(descText);
            }
            if (value != null)
            {
                value.Margin = new Thickness(0, 7, 0, 0);
                sp.Children.Add(value);
            }
            return sp;
        }

        /// <summary>
        /// 所有由 Group 造出来的卡片，按弱引用存着。材质是三档可切的，而页面实例被面板
        /// 缓存复用（切页不重建），所以换档时必须能回头重刷每一张卡 —— 不然会出现
        /// 「外壳已经是玻璃、卡片还是实心」这种半新半旧的拼缝。
        /// </summary>
        static readonly List<WeakReference<WpfControls.Border>> Cards =
            new List<WeakReference<WpfControls.Border>>();

        /// <summary>当前是不是自绘玻璃档。由 App.ApplyTheme 在换档时写。</summary>
        public static bool Glass = false;

        // 玻璃档的卡片必须**半透明**才能透出背后的 Mica；但 alpha 不能太低 ——
        // 面板的像素门禁拿「真窗口取样框」比「离屏参照」，桌面一旦大量渗进来，
        // 比样就会随壁纸时红时绿。0xB8≈72% 是「看得出通透、又不致于漂」的那一档，
        // 换壁纸重合度的验收就钉在这里（见 smoke 的 check_backdrop_material）。
        static readonly Brush GlassTintLight = Frozen(Color.FromArgb(0xB8, 0xFF, 0xFF, 0xFF));
        static readonly Brush GlassTintDark = Frozen(Color.FromArgb(0xB8, 0x20, 0x20, 0x20));
        static readonly Brush GlassEdgeLight = Frozen(Color.FromArgb(0x80, 0xFF, 0xFF, 0xFF));
        static readonly Brush GlassEdgeDark = Frozen(Color.FromArgb(0x40, 0xFF, 0xFF, 0xFF));

        static bool DarkNow()
            => Wpf.Ui.Appearance.ApplicationThemeManager.GetAppTheme()
               == Wpf.Ui.Appearance.ApplicationTheme.Dark;

        /// <summary>按当前材质重画一张卡：圆角 12 ↔ 18，实心主题刷 ↔ 半透明 + 内高光。</summary>
        static void StyleCard(WpfControls.Border card)
        {
            if (card == null) return;
            // 12 是 spec 增补 V2 圆角那一行给卡片的值（按钮/输入框那 8 走
            // App.ApplyBrandPalette 里的 ControlCornerRadius 键）；玻璃档故意更大，
            // 那一档要的是"看着最透"，18 是它自己定的。
            card.CornerRadius = new CornerRadius(Glass ? 18 : 12);
            if (Glass)
            {
                bool dark = DarkNow();
                card.Background = dark ? GlassTintDark : GlassTintLight;
                card.BorderBrush = dark ? GlassEdgeDark : GlassEdgeLight;
            }
            else
            {
                Ref(card, WpfControls.Border.BackgroundProperty, CardKey);
                Ref(card, WpfControls.Border.BorderBrushProperty, LineKey);
            }
        }

        /// <summary>换材质/换主题后调用，把活着的卡片全部重刷一遍。</summary>
        public static void RefreshMaterial()
        {
            for (int i = Cards.Count - 1; i >= 0; i--)
            {
                if (!Cards[i].TryGetTarget(out var b) || b == null) { Cards.RemoveAt(i); continue; }
                StyleCard(b);
            }
        }

        public static FrameworkElement Group(string title, params FrameworkElement[] rows)
        {
            var body = new WpfControls.StackPanel();
            for (int i = 0; i < rows.Length; i++)
            {
                if (i > 0)
                {
                    var divider = new WpfControls.Border { Height = 1, Margin = new Thickness(14, 0, 14, 0) };
                    Ref(divider, WpfControls.Border.BackgroundProperty, LineKey);
                    body.Children.Add(divider);
                }
                body.Children.Add(rows[i]);
            }
            var card = new WpfControls.Border
            {
                BorderThickness = new Thickness(1),
                Child = body,
            };
            // 圆角与底色一律由 StyleCard 决定（材质三档要能当场重刷），这里不写死
            Cards.Add(new WeakReference<WpfControls.Border>(card));
            StyleCard(card);

            var outer = new WpfControls.StackPanel { Margin = new Thickness(0, 0, 0, 18) };
            if (!string.IsNullOrEmpty(title))
            {
                var head = new WpfControls.TextBlock
                {
                    Text = title,
                    FontSize = 13,
                    FontWeight = FontWeights.SemiBold,
                    Margin = new Thickness(2, 0, 0, 7),
                };
                Ref(head, WpfControls.TextBlock.ForegroundProperty, InkKey);
                outer.Children.Add(head);
            }
            outer.Children.Add(card);
            return outer;
        }

        public static WpfControls.TextBlock Heading(string text)
        {
            var block = new WpfControls.TextBlock
            {
                Text = text,
                FontSize = 20,
                FontWeight = FontWeights.SemiBold,
                Margin = new Thickness(2, 0, 0, 14),
            };
            Ref(block, WpfControls.TextBlock.ForegroundProperty, InkKey);
            return block;
        }

        public static WpfControls.StackPanel Column(params FrameworkElement[] children)
        {
            var sp = new WpfControls.StackPanel();
            foreach (var c in children) sp.Children.Add(c);
            return sp;
        }

        /// <summary>
        /// 状态码 → 中文标签 + 配色。**标签和色值必须与引擎 dsh_state.py 的 STATES 逐项一致**
        /// （冒烟 check_overview_page 有一条门禁在两边做逐字比对），这里是前端侧的同一份表。
        /// 顺序也照引擎的优先级排（需要操作 → 未运行 → 未知），方便对着读。
        /// 2026-10-04 随引擎一起换成「落日猎人 + 完美风暴」采样色，紫色相整套废除。
        /// </summary>
        static readonly Dictionary<string, (string Label, string Hex)> StateMap =
            new Dictionary<string, (string, string)>
        {
            { "needs_action", ("需要操作", "#D87D44") },
            { "error", ("出错了", "#C65B51") },
            { "stalled", ("疑似卡住", "#C3A559") },
            { "thinking", ("正在思考", "#92A5A0") },
            { "tool_running", ("正在执行工具", "#54644A") },
            { "answering", ("正在回答", "#7F8E6B") },
            { "done", ("回答完成", "#BFD268") },
            { "aborted", ("已中断", "#5A6363") },
            { "idle", ("待命", "#D2D2C8") },
            { "offline", ("未运行", "#2E3844") },
            { "unknown", ("未知", "#8E918A") },
        };

        /// <summary>状态码的中文标签；不认识的码一律「未知」，不把引擎的英文码甩到界面上。</summary>
        public static string LabelOf(string state)
        {
            return StateMap.TryGetValue(state ?? "", out var v) ? v.Label : "未知";
        }

        /// <summary>状态码对应的色号（引擎报什么色画什么色），未知码落到「未知」那一格。</summary>
        public static string HexOf(string state)
        {
            return StateMap.TryGetValue(state ?? "", out var v) ? v.Hex : StateMap["unknown"].Hex;
        }

        static readonly Dictionary<string, Brush> Tints = new Dictionary<string, Brush>();

        /// <summary>
        /// 状态徽章的那层底色：状态色压到 15% 不透明度。
        /// 徽章上的文字**不**用状态色 —— 「待命 #D2D2C8」「回答完成 #BFD268」这类浅色
        /// 压在白卡上根本读不了，所以文字吃主题墨色，颜色只由那枚小圆点承担。
        /// </summary>
        public static Brush TintOf(string state)
        {
            string hex = HexOf(state);
            lock (Tints)
            {
                if (Tints.TryGetValue(hex, out var hit)) return hit;
                Color c;
                try { c = (Color)ColorConverter.ConvertFromString(hex); }
                catch { c = (Color)ColorConverter.ConvertFromString("#8E918A"); }
                var tint = Frozen(Color.FromArgb(0x26, c.R, c.G, c.B));
                Tints[hex] = tint;
                return tint;
            }
        }

        static readonly Brush Fallback = Frozen(Color.FromRgb(0x5D, 0x4B, 0x3F));

        /// <summary>
        /// 次强调：复古金 #D6A44B —— spec 增补 V2 表里那一格此前**谁都没用**。
        /// 现在给「非 MIT」这类要提醒、但不是出问题的标记用：红色按 V4 是保留给
        /// 「出错了」那个状态码的，拿它做合规提醒等于把状态语义借走。
        ///
        /// 固定色不是主题色，所以和状态色一样直接给画刷、不走 <see cref="Ref"/>；
        /// 集中在这里给，是为了让页面上不再出现对象初始化器里的 SolidColorBrush 字面量
        /// （plan 的 Global Constraints 那条）。
        /// </summary>
        public static readonly Brush SubAccent = Frozen(Color.FromRgb(0xD6, 0xA4, 0x4B));
        public static readonly Brush SubAccentTint = Frozen(Color.FromArgb(0x33, 0xD6, 0xA4, 0x4B));

        static readonly Dictionary<string, Brush> Solid = new Dictionary<string, Brush>();

        /// <summary>状态色是**数据驱动**的（引擎报什么色画什么色），不是主题色，
        /// 所以这里返回具体 Brush 而不走 Ui.Ref；解析失败退回次级文字色。
        /// 按色号缓存：概览页每 2 秒把最多 60 行重投影一遍，不缓存就是每轮新建 60 根画刷。</summary>
        public static Brush BrushOf(string hex)
        {
            lock (Solid)
            {
                if (Solid.TryGetValue(hex ?? "", out var hit)) return hit;
                Brush b;
                try { b = Frozen((Color)ColorConverter.ConvertFromString(hex)); }
                catch { b = Fallback; }
                Solid[hex ?? ""] = b;
                return b;
            }
        }

        static Brush Frozen(Color c)
        {
            var b = new SolidColorBrush(c);
            b.Freeze();
            return b;
        }
    }
}
