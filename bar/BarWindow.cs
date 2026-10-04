using System;
using System.Collections.Generic;
using System.Globalization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Shapes;

namespace Vigil
{
    /// <summary>
    /// 停靠进任务栏的状态栏窗口。必须 AllowsTransparency，WPF 才会加上 WS_EX_LAYERED；
    /// 没有分层组合表面，像素不会出现在任务栏之上。
    /// </summary>
    internal sealed class BarWindow : Window
    {
        private readonly Border _shell = new Border();
        private readonly Rectangle _accent = new Rectangle { Width = 3, RadiusX = 2, RadiusY = 2 };
        private readonly Grid _indicator = new Grid { Width = 30, Height = 30, VerticalAlignment = VerticalAlignment.Center };
        private readonly UniformGrid _barsHost = new UniformGrid { Rows = 1, Columns = 4, Width = 18, Height = 16 };
        private readonly Ellipse _glow = new Ellipse { Width = 30, Height = 30, Opacity = 0 };
        private readonly Ellipse _dot = new Ellipse { Width = 20, Height = 20 };
        private readonly TextBlock _glyph = new TextBlock
        {
            FontFamily = new FontFamily("Segoe UI"),
            FontWeight = FontWeights.Bold,
            FontSize = 10.5,
            HorizontalAlignment = HorizontalAlignment.Center,
            VerticalAlignment = VerticalAlignment.Center,
        };
        private readonly List<Rectangle> _bars = new List<Rectangle>();
        private readonly Border _track = new Border
        {
            Width = 46,
            Height = 3,
            CornerRadius = new CornerRadius(2),
            VerticalAlignment = VerticalAlignment.Center,
            Margin = new Thickness(10, 0, 0, 0),
            HorizontalAlignment = HorizontalAlignment.Left,
        };
        private readonly Border _fill = new Border { Height = 3, CornerRadius = new CornerRadius(2), HorizontalAlignment = HorizontalAlignment.Left };
        private readonly TextBlock _state = new TextBlock
        {
            FontFamily = new FontFamily("Microsoft YaHei UI"),
            FontWeight = FontWeights.SemiBold,
            FontSize = 12.5,
            VerticalAlignment = VerticalAlignment.Center,
        };
        private readonly TextBlock _meta = new TextBlock
        {
            FontFamily = new FontFamily("Microsoft YaHei UI"),
            FontSize = 12,
            VerticalAlignment = VerticalAlignment.Center,
            Margin = new Thickness(8, 0, 0, 0),
            TextTrimming = TextTrimming.CharacterEllipsis,
        };
        private readonly TextBlock _alert = new TextBlock
        {
            FontFamily = new FontFamily("Microsoft YaHei UI"),
            FontSize = 12,
            FontWeight = FontWeights.SemiBold,
            VerticalAlignment = VerticalAlignment.Center,
            Margin = new Thickness(10, 0, 0, 0),
            TextTrimming = TextTrimming.CharacterEllipsis,
            Visibility = Visibility.Collapsed,
        };
        private readonly Border _divider = new Border { Width = 1, Margin = new Thickness(10, 7, 0, 7) };
        private readonly TextBlock _balance = new TextBlock
        {
            FontFamily = new FontFamily("Microsoft YaHei UI"),
            FontWeight = FontWeights.SemiBold,
            FontSize = 12,
            VerticalAlignment = VerticalAlignment.Center,
            Margin = new Thickness(9, 0, 2, 0),
        };

        private Grid _progressHost;
        private StackPanel _head;
        private Snapshot _last;
        private bool _hover;
        private bool _animating;
        private DateTime _transientUntil = DateTime.MinValue;
        private string _transient;

        public event Action LeftClicked;
        public event Action<System.Windows.Point> RightClicked;
        public event Action<int> Wheel;
        public event Action HoverChanged;

        public bool Expanded => _hover;
        public string TooltipText { get; private set; } = "Vigil 状态栏";

        public BarWindow()
        {
            WindowStyle = WindowStyle.None;
            AllowsTransparency = true;
            ShowInTaskbar = false;
            ShowActivated = false;
            ResizeMode = ResizeMode.NoResize;
            Focusable = false;
            Topmost = false;
            Background = Brushes.Transparent;
            SizeToContent = SizeToContent.Manual;
            Width = 420;
            Height = 40;
            Title = "Vigil 状态栏";

            for (int i = 0; i < 4; i++)
            {
                var bar = new Rectangle
                {
                    Width = 3,
                    Height = 6,
                    Margin = new Thickness(1.5, 0, 1.5, 0),
                    RadiusX = 1.5,
                    RadiusY = 1.5,
                    VerticalAlignment = VerticalAlignment.Center,
                };
                _bars.Add(bar);
                _barsHost.Children.Add(bar);
            }

            var dotHost = new Grid { Width = 20, Height = 20 };
            dotHost.Children.Add(_dot);
            dotHost.Children.Add(_glyph);
            _indicator.Children.Add(_glow);
            _indicator.Children.Add(dotHost);
            _indicator.Children.Add(_barsHost);

            var head = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
            _head = head;
            var progress = new Grid { Width = 46, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(10, 0, 0, 0) };
            progress.Children.Add(_track);
            progress.Children.Add(_fill);
            _progressHost = progress;
            head.Children.Add(_state);
            head.Children.Add(_meta);
            head.Children.Add(progress);

            var textZone = new Grid { ClipToBounds = true };
            textZone.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            textZone.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            Grid.SetColumn(head, 0);
            Grid.SetColumn(_alert, 1);
            textZone.Children.Add(head);
            textZone.Children.Add(_alert);

            // 必须用 Grid 而不是横向 StackPanel：StackPanel 会给子元素无限宽度，
            // 正文永远拿不到受限宽度，省略号也就不会出现
            var outer = new Grid();
            outer.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(3) });
            outer.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            outer.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(6) });
            outer.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            outer.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            outer.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            Grid.SetColumn(_accent, 0);
            Grid.SetColumn(_indicator, 1);
            Grid.SetColumn(textZone, 3);
            Grid.SetColumn(_divider, 4);
            Grid.SetColumn(_balance, 5);
            outer.Children.Add(_accent);
            outer.Children.Add(_indicator);
            outer.Children.Add(textZone);
            outer.Children.Add(_divider);
            outer.Children.Add(_balance);

            _shell.CornerRadius = new CornerRadius(9);
            _shell.Padding = new Thickness(9, 0, 9, 0);
            _shell.Child = outer;
            Content = _shell;

            MouseLeftButtonUp += (s, e) => LeftClicked?.Invoke();
            MouseRightButtonUp += (s, e) =>
            {
                RightClicked?.Invoke(PointToScreen(e.GetPosition(this)));
                e.Handled = true;
            };
            MouseWheel += (s, e) => Wheel?.Invoke(Math.Sign(e.Delta));
            // 挂在 explorer 任务栏下的非活动窗口收不到 MouseEnter/Leave，悬停靠 App 轮询光标

            Theme.Refresh();
            ApplyPalette();
        }

        public void Apply(Snapshot snap)
        {
            bool wasLight = Theme.Light;
            Theme.Refresh();
            if (wasLight != Theme.Light) ApplyPalette();
            _last = snap;
            ApplyVisual(snap);
        }

        /// <summary>不含可伸缩正文时的自然宽度（DIP），供宽度补间定目标。</summary>
        public double ContentWidth()
        {
            _head.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
            _balance.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
            double alert = _alert.Visibility == Visibility.Visible ? 24 : 0;
            return 9 + 3 + 30 + 6 + _head.DesiredSize.Width + alert + 10 + 1 + 9 + _balance.DesiredSize.Width + 9;
        }

        /// <summary>由 App 的光标轮询驱动：只有真的变了才重绘并通知补间。</summary>
        public void SetHover(bool on)
        {
            if (_hover == on) return;
            _hover = on;
            if (!on) _transientUntil = DateTime.MinValue;
            ApplyVisual(_last);
            HoverChanged?.Invoke();
        }

        public void ShowTransient(string text)
        {
            _transient = text;
            _transientUntil = DateTime.Now.AddSeconds(4);
            ApplyVisual(_last);
        }

        private static Color C(uint argb) =>
            Color.FromArgb((byte)(argb >> 24), (byte)(argb >> 16), (byte)(argb >> 8), (byte)argb);

        private void ApplyPalette()
        {
            _shell.Background = new SolidColorBrush(C(Theme.Fill));
            _shell.BorderBrush = new SolidColorBrush(C(Theme.Stroke));
            _shell.BorderThickness = new Thickness(1);
            _state.Foreground = new SolidColorBrush(C(Theme.TextPrimary));
            _meta.Foreground = new SolidColorBrush(C(Theme.TextSecondary));
            _balance.Foreground = new SolidColorBrush(C(Theme.TextSecondary));
            _divider.Background = new SolidColorBrush(C(Theme.Track));
        }

        private static Color ParseColor(string hex)
        {
            try
            {
                return (Color)ColorConverter.ConvertFromString(hex);
            }
            catch
            {
                return Color.FromRgb(107, 114, 128);
            }
        }

        private static Color InvertFor(Color bg) =>
            0.299 * bg.R + 0.587 * bg.G + 0.114 * bg.B > 150
                ? Color.FromRgb(20, 22, 26)
                : Color.FromRgb(255, 255, 255);

        private void ApplyVisual(Snapshot snap)
        {
            if (snap == null) return;
            var accent = ParseColor(snap.Color);
            var accentBrush = new SolidColorBrush(accent);
            bool active = snap.State == "thinking" || snap.State == "answering" || snap.State == "tool_running";
            bool alert = snap.State == "needs_action" || snap.State == "error";

            _accent.Fill = accentBrush;
            _accent.VerticalAlignment = VerticalAlignment.Stretch;
            _accent.Margin = new Thickness(0, 3, 0, 3);
            _accent.HorizontalAlignment = HorizontalAlignment.Left;

            _dot.Fill = accentBrush;
            _glyph.Text = string.IsNullOrEmpty(snap.Glyph) ? "?" : snap.Glyph;
            _glyph.Foreground = new SolidColorBrush(InvertFor(accent));
            _dot.Visibility = active ? Visibility.Collapsed : Visibility.Visible;
            _glyph.Visibility = active ? Visibility.Collapsed : Visibility.Visible;
            _barsHost.Visibility = active ? Visibility.Visible : Visibility.Collapsed;
            foreach (var b in _bars) b.Fill = accentBrush;
            if (active) StartBars();
            else StopBars();

            _glow.Fill = accentBrush;
            if (alert) PulseGlow();
            else
            {
                _glow.BeginAnimation(UIElement.OpacityProperty, null);
                _glow.Opacity = 0;
            }

            var s = snap.Session;
            bool transientActive = _transientUntil > DateTime.Now && !string.IsNullOrEmpty(_transient);
            if (transientActive)
            {
                _state.Text = "查看";
                _meta.Text = _transient;
                _alert.Visibility = Visibility.Collapsed;
            }
            else
            {
                _state.Text = snap.Label ?? "未知";
                _meta.Text = BuildMeta(snap, s);
                string alertText = BuildAlert(snap, s, alert);
                if (alertText != null)
                {
                    _alert.Text = alertText;
                    _alert.Foreground = alert
                        ? new SolidColorBrush(Color.FromRgb(248, 113, 113))
                        : new SolidColorBrush(C(Theme.TextDim));
                    _alert.Visibility = Visibility.Visible;
                }
                else
                {
                    _alert.Visibility = Visibility.Collapsed;
                }
            }
            BuildProgress(s, accentBrush);

            _balance.Text = string.IsNullOrEmpty(snap.StripRight) ? "--" : snap.StripRight;
            TooltipText = string.IsNullOrEmpty(snap.Tooltip) ? "Vigil 状态栏" : snap.Tooltip;
        }

        private string BuildMeta(Snapshot snap, Session s)
        {
            if (s == null || string.IsNullOrEmpty(s.Project)) return "";
            var culture = CultureInfo.InvariantCulture;
            string text = s.Project;
            if (s.Turn.HasValue)
            {
                text += s.Step.HasValue
                    ? string.Format(culture, " · T{0}/S{1}", s.Turn.Value, s.Step.Value)
                    : string.Format(culture, " · T{0}", s.Turn.Value);
            }
            if (_hover && !string.IsNullOrEmpty(s.LastTool) && (snap.State == "tool_running" || snap.State == "thinking"))
            {
                text += " · " + s.LastTool;
            }
            return text;
        }

        private string BuildAlert(Snapshot snap, Session s, bool alert)
        {
            if (s == null) return null;
            if (s.Pending != null && !string.IsNullOrWhiteSpace(s.Pending.Text))
            {
                string text = s.Pending.Text.Trim();
                if (!_hover && text.Length > 30) text = text.Substring(0, 29) + "…";
                return text;
            }
            if (alert && !string.IsNullOrWhiteSpace(s.Error))
            {
                string text = s.Error.Trim();
                if (text.Length > 48) text = text.Substring(0, 47) + "…";
                return text;
            }
            return null;
        }

        private void BuildProgress(Session s, Brush accentBrush)
        {
            if (s?.Todo == null || s.Todo.Total == 0)
            {
                _progressHost.Visibility = Visibility.Collapsed;
                return;
            }
            _progressHost.Visibility = Visibility.Visible;
            _track.Background = new SolidColorBrush(C(Theme.Track));
            double ratio = Math.Clamp((double)s.Todo.Done / s.Todo.Total, 0, 1);
            _fill.Width = Math.Max(2, 46 * ratio);
            _fill.Background = accentBrush;
        }

        private void StartBars()
        {
            if (_animating) return;
            _animating = true;
            for (int i = 0; i < _bars.Count; i++)
            {
                var anim = new DoubleAnimation
                {
                    From = 4,
                    To = 15,
                    Duration = TimeSpan.FromMilliseconds(420 + i * 90),
                    AutoReverse = true,
                    RepeatBehavior = RepeatBehavior.Forever,
                    EasingFunction = new SineEase { EasingMode = EasingMode.EaseInOut },
                    BeginTime = TimeSpan.FromMilliseconds(i * 110),
                };
                _bars[i].BeginAnimation(Rectangle.HeightProperty, anim);
            }
        }

        private void StopBars()
        {
            if (!_animating) return;
            _animating = false;
            foreach (var bar in _bars)
            {
                bar.BeginAnimation(Rectangle.HeightProperty, null);
                bar.Height = 6;
            }
        }

        private void PulseGlow()
        {
            if (_glow.Opacity > 0 && _glow.Opacity < 0.43) return;
            var anim = new DoubleAnimation
            {
                From = 0.10,
                To = 0.42,
                Duration = TimeSpan.FromMilliseconds(900),
                AutoReverse = true,
                RepeatBehavior = RepeatBehavior.Forever,
                EasingFunction = new SineEase { EasingMode = EasingMode.EaseInOut },
            };
            _glow.BeginAnimation(UIElement.OpacityProperty, anim);
        }
    }
}
