using System;
using System.Collections.Generic;
using System.IO;
using System.Text.RegularExpressions;

namespace Vigil
{
    /// <summary>
    /// 第三方许可清单的解析器：读仓库根那份 `THIRD-PARTY-NOTICES.md`，
    /// 把带 `- **用途**：` 的 `## N.` / `### N.M` 小节收成条目。
    ///
    /// 为什么解析而不是抄一遍：spec 增补 V5 §2 的原话是「清单从 THIRD-PARTY-NOTICES.md
    /// 解析出来，而不是在 C# 里再手抄一份（手抄必然漂移）」。冒烟里另有一条**独立**的
    /// Python 解析器（smoke_test.notices_entries），两边对不上就红 —— 抄一份到 C#
    /// 就等于把「关于页显示的清单」和「实际随产物分发的声明」变成两个事实源。
    ///
    /// 2 / 4 那两个大节是容器（只有说明文字，没有字段行），按"没有用途就不算条目"排除。
    /// </summary>
    internal sealed class Credit
    {
        public string Id { get; set; }
        public string Name { get; set; }
        public string License { get; set; }
        public string Url { get; set; }
        public string Purpose { get; set; }

        /// <summary>
        /// 许可证的短名：只取括号前的那一小截，并去掉 markdown 的 `**`。
        /// notices 里那一格写的是整句依据（「MIT（nuspec &lt;license .../&gt;，且 require...」），
        /// 直接铺到界面上就是一张全是字的卡，读不出重点。
        /// </summary>
        public string ShortLicense
        {
            get
            {
                string s = (License ?? "").Replace("**", "").Trim();
                int cut = s.IndexOfAny(new[] { '（', '(', '｜', '|', ',' });
                if (cut > 0) s = s.Substring(0, cut).Trim();
                return s;
            }
        }

        /// <summary>
        /// MIT 之外的都要在页面上单独标出来（V5 §3）。
        ///
        /// 判序很要紧：Segoe 那一格原文是「微软专有字体许可（**非 MIT**）」，
        /// 先按"含 MIT 就算 MIT"判会被括号里的"非 MIT"骗过去 —— 必须先认「非 MIT / 专有」。
        /// </summary>
        public bool NonMit
        {
            get
            {
                string s = (License ?? "").Replace("**", "");
                if (s.Length == 0) return false;
                if (s.Contains("非 MIT") || s.Contains("非MIT") || s.Contains("专有")) return true;
                return !s.Contains("MIT");
            }
        }
    }

    internal static class Credits
    {
        static readonly Regex Heading =
            new Regex(@"^#{2,3}\s+(\d+(?:\.\d+)?)\.?\s*(.+?)\s*$", RegexOptions.Compiled);
        static readonly Regex Field =
            new Regex(@"^-\s+\*\*(.+?)\*\*\s*[：:]\s*(.*)$", RegexOptions.Compiled);
        static readonly Regex HttpUrl = new Regex(@"https?://\S+", RegexOptions.Compiled);

        /// <summary>清单文件与 exe 同目录（csproj 里 CopyToOutputDirectory 带出来的）。</summary>
        public static string NoticesPath =>
            Path.Combine(AppContext.BaseDirectory, "THIRD-PARTY-NOTICES.md");

        public static string LicensePath =>
            Path.Combine(AppContext.BaseDirectory, "LICENSE");

        public static List<Credit> Parse()
        {
            var list = new List<Credit>();
            string[] lines;
            try
            {
                lines = File.ReadAllLines(NoticesPath);
            }
            catch (Exception)
            {
                return list;
            }
            Credit cur = null;
            for (int i = 0; i <= lines.Length; i++)
            {
                // 末尾补一次 flush：循环里只在遇到下一个标题时才收尾上一条。
                string line = i < lines.Length ? lines[i] : "";
                var head = Heading.Match(line);
                if (head.Success)
                {
                    if (cur != null && !string.IsNullOrEmpty(cur.Purpose)) list.Add(cur);
                    string title = head.Groups[2].Value;
                    cur = new Credit
                    {
                        Id = head.Groups[1].Value,
                        // 「WPF-UI 4.2.0 — MIT」这类标题把许可证放在破折号后面，条目名只取前半。
                        Name = Regex.Split(title, @"\s+[—-]\s+")[0].Trim(),
                        License = "",
                        Url = "",
                        Purpose = "",
                    };
                    continue;
                }
                if (cur == null) continue;
                var field = Field.Match(line);
                if (!field.Success) continue;
                string key = field.Groups[1].Value;
                string val = field.Groups[2].Value.Trim();
                if (key == "用途") cur.Purpose = val;
                else if (key == "许可证" && string.IsNullOrEmpty(cur.License)) cur.License = val;
                else if (key == "上游")
                {
                    var m = HttpUrl.Match(val);
                    if (m.Success) cur.Url = m.Value.TrimEnd('｜', '|', ',', '，', '。');
                }
            }
            if (cur != null && !string.IsNullOrEmpty(cur.Purpose)) list.Add(cur);
            return list;
        }

        /// <summary>MIT 全文，关于页直接渲染同一份，不另抄一遍（spec §8）。</summary>
        public static string LicenseText()
        {
            try { return File.ReadAllText(LicensePath).Trim(); }
            catch (Exception) { return ""; }
        }
    }
}
