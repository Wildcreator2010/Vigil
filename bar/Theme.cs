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

        // 2026-10-04 换成品牌色（用户自己的配色积累「落日猎人」，色号按像素采样）：
        // 浅底用暖纸白 + 夜影黑字，深底用夜巡蓝 + 米白字；次级文字走松木灰/黄昏云。
        // 状态栏嵌在任务栏里，仍然跟系统深浅走 —— 只是把冷灰换成同一套里的暖灰。
        public static uint Fill => Light ? 0xE6FBF8F3u : 0xCC2E3844u;
        public static uint Stroke => Light ? 0x40B0A788u : 0x2A92A5A0u;
        public static uint TextPrimary => Light ? 0xF22B2A23u : 0xF2EDE9E1u;
        public static uint TextSecondary => Light ? 0xB35D4B3Fu : 0xB3B0A788u;
        public static uint TextDim => Light ? 0x808A5631u : 0x8092A5A0u;
        public static uint Track => Light ? 0x1F2B2A23u : 0x2A92A5A0u;
        /// <summary>警示色：晚霞红（原 #DC2626 随状态色板一起换掉）。</summary>
        public static uint Alert => 0xFFC65B51u;
        /// <summary>强调色：暮光橙。进度槽已填满的那一截用它。</summary>
        public static uint Accent => 0xFFD87D44u;
    }
}
