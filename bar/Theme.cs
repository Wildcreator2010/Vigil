using System;
using Microsoft.Win32;

namespace DshBar
{
    /// <summary>
    /// 状态栏嵌在任务栏里，配色必须跟着系统主题走，否则深色任务栏上顶出一块白卡特别突兀。
    /// </summary>
    internal static class Theme
    {
        public static bool Light { get; private set; }

        public static void Refresh()
        {
            try
            {
                using var key = Registry.CurrentUser.OpenSubKey(
                    @"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");
                object v = key?.GetValue("SystemUsesLightTheme");
                Light = v is int i && i != 0;
            }
            catch
            {
                Light = false;
            }
        }

        public static uint Fill => Light ? 0xE6FFFFFFu : 0xCC1D1F24u;
        public static uint Stroke => Light ? 0x33000000u : 0x2AFFFFFFu;
        public static uint TextPrimary => Light ? 0xF21A1D23u : 0xF2F5F7FAu;
        public static uint TextSecondary => Light ? 0xB35A6270u : 0xB3AAB2BFu;
        public static uint TextDim => Light ? 0x806B7480u : 0x80828C99u;
        public static uint Track => Light ? 0x1F000000u : 0x2AFFFFFFu;
        public static uint Alert => 0xFFDC2626u;
    }
}
