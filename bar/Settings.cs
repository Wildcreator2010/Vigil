using System;
using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace DshBar
{
    internal sealed class Settings
    {
        [JsonPropertyName("interval")] public int Interval { get; set; } = 2;
        [JsonPropertyName("notify")] public bool Notify { get; set; } = true;
        [JsonPropertyName("repeatSec")] public int RepeatSec { get; set; } = 90;
        [JsonPropertyName("theme")] public string Theme { get; set; } = "light";
        [JsonPropertyName("showBar")] public bool ShowBar { get; set; } = true;

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
