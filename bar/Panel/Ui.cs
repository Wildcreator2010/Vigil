using System.Windows;
using WpfControls = System.Windows.Controls;

namespace DshBar
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
                CornerRadius = new CornerRadius(8),
                Child = body,
            };
            Ref(card, WpfControls.Border.BackgroundProperty, CardKey);
            Ref(card, WpfControls.Border.BorderBrushProperty, LineKey);

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
    }
}
