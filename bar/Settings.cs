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
