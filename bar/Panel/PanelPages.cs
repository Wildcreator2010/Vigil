using System;
using Win = System.Windows;

namespace Vigil
{
    /// <summary>
    /// 控制台页签的唯一登记表：key、导航标题、以及 key 到页面实例的工厂。
    /// PanelWindow 与 --panel-shot 共用这一份。此前分发知识散在三处
    /// （PanelWindow.Keys / PanelWindow.Build / App.RenderShot 的 switch），
    /// 后两个还逐字重复：加一页要改三个地方，漏一处就出现「窗口里能打开、
    /// shot 渲染不出来」这种分裂，未知 key 还会被 default 静默当 overview。
    /// </summary>
    internal static class PanelPages
    {
        internal static readonly string[] Keys = { "overview", "notify", "appearance", "runtime", "balance", "about" };
        internal static readonly string[] Labels = { "概览", "通知", "外观", "运行", "余额", "关于" };

        /// <summary>导航列表用的下标：未知 key 归到概览，别因为一个手抖的参数打不开控制台。</summary>
        internal static int IndexOf(string key)
        {
            int idx = Array.IndexOf(Keys, key);
            return idx < 0 ? 0 : idx;
        }

        /// <summary>按 key 造一页。未知 key 返回 false，由调用方决定是报错（--panel-shot）还是回退（导航）。</summary>
        internal static bool TryCreate(string key, out Win.FrameworkElement page)
        {
            switch (key)
            {
                case "overview": page = new OverviewPage(); return true;
                case "notify": page = new NotifyPage(); return true;
                case "appearance": page = new AppearancePage(); return true;
                case "runtime": page = new RuntimePage(); return true;
                case "balance": page = new BalancePage(); return true;
                case "about": page = new AboutPage(); return true;
                default: page = null; return false;
            }
        }
    }
}
