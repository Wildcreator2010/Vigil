using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace DshBar
{
    /// <summary>
    /// 录入 DeepSeek 余额 Key，交给 dsh_state.py 用 DPAPI 加密保存（不写明文）。
    /// </summary>
    internal static class KeyDialog
    {
        public static void Ask()
        {
            var box = new PasswordBox { Margin = new Thickness(0, 8, 0, 12) };
            var ok = new Button
            {
                Content = "保存（当前用户加密）",
                Width = 168,
                Height = 28,
                IsDefault = true,
                Margin = new Thickness(0, 0, 8, 0),
            };
            var cancel = new Button { Content = "取消", Width = 80, Height = 28, IsCancel = true };
            var buttons = new StackPanel
            {
                Orientation = Orientation.Horizontal,
                HorizontalAlignment = HorizontalAlignment.Right,
            };
            buttons.Children.Add(ok);
            buttons.Children.Add(cancel);

            var body = new StackPanel { Margin = new Thickness(14) };
            body.Children.Add(new TextBlock
            {
                Text = "平台控制台 → API Keys → 可查询余额的 Key。留空并保存 = 清除已存 Key。",
                TextWrapping = TextWrapping.Wrap,
                FontSize = 12,
                Foreground = new System.Windows.Media.SolidColorBrush(System.Windows.Media.Color.FromRgb(55, 65, 81)),
            });
            body.Children.Add(box);
            body.Children.Add(buttons);

            var win = new Window
            {
                Title = "DeepSeek 余额 Key",
                Content = body,
                Width = 440,
                SizeToContent = SizeToContent.Height,
                WindowStartupLocation = WindowStartupLocation.CenterScreen,
                ResizeMode = ResizeMode.NoResize,
                ShowInTaskbar = true,
                Topmost = true,
                Background = System.Windows.Media.Brushes.White,
                FontFamily = new FontFamily("Microsoft YaHei UI"),
            };
            string key = null;
            bool confirmed = false;
            ok.Click += (s, e) =>
            {
                key = box.Password.Trim();
                confirmed = true;
                win.DialogResult = true;
            };
            win.ShowDialog();
            if (!confirmed) return;

            try
            {
                string engine = EnginePath();
                string python = PythonPath();
                if (python == null || engine == null)
                {
                    MessageBox.Show("找不到 python 或 dsh_state.py", "dsh 状态栏");
                    return;
                }
                string args = key.Length > 0 ? "--save-balance-key" : "--clear-balance-key";
                var psi = new ProcessStartInfo
                {
                    FileName = python,
                    Arguments = $"-X utf8 \"{engine}\" {args}",
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardInput = key.Length > 0,
                    RedirectStandardOutput = true,
                    WorkingDirectory = Path.GetDirectoryName(engine),
                    StandardOutputEncoding = new UTF8Encoding(false),
                };
                using var proc = Process.Start(psi);
                if (key.Length > 0)
                {
                    proc.StandardInput.Write(key);
                    proc.StandardInput.Close();
                }
                proc.WaitForExit(20000);
                File.WriteAllText(RefreshTokenPath(), DateTime.Now.ToString("o"));
            }
            catch (Exception ex)
            {
                MessageBox.Show("保存失败：" + ex.Message, "dsh 状态栏");
            }
        }

        private static string RefreshTokenPath()
        {
            string dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "dsh-status");
            Directory.CreateDirectory(dir);
            return Path.Combine(dir, "refresh.token");
        }

        private static string EnginePath()
        {
            string local = Path.Combine(AppContext.BaseDirectory, "dsh_state.py");
            if (File.Exists(local)) return local;
            string up = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "dsh_state.py"));
            return File.Exists(up) ? up : null;
        }

        private static string PythonPath()
        {
            foreach (var dir in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
            {
                if (dir.Length == 0) continue;
                try
                {
                    string candidate = Path.Combine(dir.Trim(), "python.exe");
                    if (File.Exists(candidate)) return candidate;
                }
                catch
                {
                }
            }
            return null;
        }
    }
}
