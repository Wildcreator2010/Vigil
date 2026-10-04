using System;
using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Vigil
{
    internal sealed class Settings
    {
        [JsonPropertyName("interval")] public int Interval { get; set; } = 2;
        [JsonPropertyName("notify")] public bool Notify { get; set; } = true;
        [JsonPropertyName("repeatSec")] public int RepeatSec { get; set; } = 90;
        [JsonPropertyName("theme")] public string Theme { get; set; } = "light";
        [JsonPropertyName("showBar")] public bool ShowBar { get; set; } = true;
        /// <summary>
        /// 面板外壳材质：mica（云母）/ acrylic（毛玻璃）/ glass（自绘液态玻璃）。
        /// 默认 acrylic —— 用户要的就是"能看见玻璃"的那一档。
        /// WPF-UI 4.2 的系统材质只有 Mica / Acrylic / Tabbed，没有 Liquid Glass，
        /// 所以 glass 是**自绘近似**：Mica 打底 + 半透明层叠 + 内高光描边 + 大圆角。
        /// </summary>
        [JsonPropertyName("backdrop")] public string Backdrop { get; set; } = "acrylic";

        /// <summary>三档材质的合法取值与界面顺序。外观页与 ApplyBackdrop 共用这一份，别各写一遍。</summary>
        public static readonly string[] BackdropKinds = { "mica", "acrylic", "glass" };

        public static string SafeBackdrop(string raw)
        {
            raw = (raw ?? "").Trim().ToLowerInvariant();
            foreach (var k in BackdropKinds) if (k == raw) return k;
            return "acrylic";
        }

        public static Settings Load(string path)
        {
            try
            {
                if (File.Exists(path))
                {
                    var s = JsonSerializer.Deserialize<Settings>(File.ReadAllText(path));
                    if (s != null)
                    {
                        s.Interval = Math.Max(1, s.Interval);
                        if (s.Theme != "light" && s.Theme != "dark" && s.Theme != "system")
                            s.Theme = "light";
                        s.Backdrop = SafeBackdrop(s.Backdrop);
                        return s;
                    }
                }
            }
            catch
            {
            }
            return new Settings();
        }

        public void Save(string path)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                File.WriteAllText(path, JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true }));
            }
            catch
            {
            }
        }
    }
}
