# dsh-status 控制台面板 + 开源合规 —— 设计文档

日期：2026-10-03
状态：待评审
范围：为 dsh-status 增加一个 AF-Media-Bar 风格的桌面控制台窗口，并补齐开源许可证与第三方声明

## 1. 背景与目标

dsh-status 现在只有一个嵌在 Windows 任务栏里的状态条，唯一的操作入口是托盘右键菜单，
所有配置都要靠改 `%LOCALAPPDATA%\dsh-status\settings.json` 或用菜单里零散的项目。
它没有一个"产品"形态的界面：看不到全部会话，看不到用量，改设置也没有反馈。

目标：

1. 新增一个控制台窗口（启动面板），把实时状态、全部会话、余额和所有设置收进一个地方；
   任务栏状态条降级为一种"显示模式"，不再是唯一界面。
2. 界面语言与布局参照 [AF-Media-Bar](https://github.com/Fervent-Tempo/AF-Media-Bar) 的
   `SettingsWindow` + `Views/Pages/*` + `SettingsGroup`/`SettingsRow` 那套词汇。
3. 把项目自身和全部第三方（含传递依赖）的许可证写清楚。

非目标见 §10。

## 2. 已确认的决策

| 决策 | 选择 | 后果 |
| --- | --- | --- |
| Fluent 观感来源 | 引入 `WPF-UI` 4.2.0 | 放弃"零 NuGet 依赖 + 离线编译"，README 相应改写 |
| 面板定位 | 控制台（实时状态 + 会话列表 + 设置） | 需要扩展引擎输出契约 |
| 本项目许可证 | MIT，`© 2026 Wildcreator` | 署名待定稿，改一处即可 |

可行性已实测，不是推断：`dotnet restore` 拉取 WPF-UI 4.2.0 成功（5.92s，nuget.org 可达）；
引用后 `dotnet build -c Release` 成功且 0 警告；`Wpf.Ui.dll` 导出 186 个公开类型，
`FluentWindow` / `NavigationView` / `CardControl` / `CardExpander` / `ToggleSwitch` /
`NumberBox` / `InfoBar` / `Badge` / `TitleBar` 均存在。
注意 4.x 已移除 3.x 的 `SettingsCard`，分组行一律用 `CardControl` 搭。

**反射实测补充（写代码前必须知道的三条，都会咬人）**：

- `Wpf.Ui.Controls.ToggleSwitch` 继承自 `System.Windows.Controls.Primitives.ToggleButton`，
  状态属性是 **`IsChecked`（`bool?`）**，不是 `IsOn`。
- `Wpf.Ui.Controls` 自带 `TextBlock` 与 `Button`，与 `System.Windows.Controls` 同名。
  同一文件里两个 `using` 并存会直接 CS0104 二义性错误——**code-behind 一律用
  `using WpfControls = System.Windows.Controls;` 起别名**，只从 `Wpf.Ui.Controls` 显式
  import 需要的类型（`FluentWindow`、`ToggleSwitch`、`NumberBox`、`FontIcon`、`Badge`、`InfoBar`）。
- 枚举实际取值：`ApplicationTheme { Unknown, Dark, Light, HighContrast }`、
  `WindowBackdropType { None, Auto, Mica, Acrylic, Tabbed }`。主题切换用
  `ApplicationThemeManager.Apply(ApplicationTheme, WindowBackdropType, bool updateAccent)`，
  跟随系统用 `ApplicationThemeManager.ApplySystemTheme()`。

**控件取舍修正**：AF-Media-Bar 的 `SettingsGroup`/`SettingsRow` 本来就是自绘控件而非库件，
本项目照此办理——分组卡片与行用自绘 `Border`/`Grid`，左侧导航用原生 `ListBox` +
`ContentControl`，**不使用** `Wpf.Ui.Controls.NavigationView`（它的页面注册/导航服务对
6 个固定页面是多余的复杂度）与 `CardControl`（实测是 `ButtonBase` 子类，整卡可点击的
语义和只读分组不符）。WPF-UI 只负责窗口外壳（`FluentWindow` + Mica 背衬 + 主题）
和表单控件（`ToggleSwitch`/`NumberBox`/`Button`/`InfoBar`/`Badge`/`FontIcon`）。

## 3. 架构：进程与窗口拓扑

面板是 **Vigil.exe 同进程内的第二扇窗口**，不新增可执行文件。理由：`StateClient`
（常驻 python 子进程）、`Settings`（内存单例）、托盘、余额 Key 存取全都在这个进程里，
同进程即零 IPC、零状态同步问题。

```
Vigil.exe
├── BarWindow        现有任务栏状态条（纯 C# 分层窗口，本次不动其视觉树）
├── PanelWindow      新增 FluentWindow + NavigationView
│   └── Pages/       概览 · 通知 · 外观 · 运行 · 余额 · 关于
├── StateClient      唯一数据源：python dsh_state.py --watch
└── Settings         唯一配置源，面板与状态条共用同一实例
```

打开面板的三个入口：托盘菜单「打开面板」、状态栏右键菜单（复用同一个 ContextMenuStrip）、
`Vigil.exe --panel [page]`。

**二次实例行为变更**：现在再启动一个 Vigil.exe 会弹「状态栏已经在运行了。」后退出。
改为：写 `%LOCALAPPDATA%\dsh-status\panel.request`（内容是页签名，空为默认页），然后静默退出；
已在跑的实例由 watchdog（每 2 秒 tick）发现该文件 mtime 变化，删除它并打开/聚焦面板。
不引入具名管道或 WCF——一个文件加一次 mtime 比较就够，且和现有的 `refresh.token` 是同一套路。

面板关闭只是 `Hide()`，不销毁，避免反复重建视觉树；进程退出仍只由托盘「退出」触发。

## 4. 引擎契约扩展（唯一的后端改动）

`snapshot()` 目前只输出 `recent`（`ACTIVE_WINDOW`=300 秒内、最多 6 条）和 `waiting`（最多 8 条），
撑不起会话表。而 `scan()` 本来就已经把全部会话算完并排好序，只是没吐出来——所以这是纯增量。

在快照里新增 `sessions` 数组，按 mtime 倒序、上限 `SESSIONS_IN_SNAPSHOT = 60`：

```jsonc
"sessions": [{
  "key": "T47-abc123",            // 会话目录名，用于和 session 字段对齐
  "project": "FurBoard",
  "title": "专利申请合规性核验",     // 截断到 80 字符
  "state": "needs_action",
  "turn": 47, "step": 10,          // 可为 null
  "age_sec": 12.4,
  "last_event": "approval/asked",
  "last_tool": "pwsh",
  "end_reason": "blocked",
  "records": 1284,
  "todo": { "total": 6, "done": 5, "in_progress": 0, "pending": 1 },   // 可为 null
  "usage_total": { "input": 120000, "output": 9000, "total": 129000 },
  "pending": { "kind": "approval", "tool": "pwsh", "text": "要联网下载",
               "options": ["允许一次", "总是允许"] }  // text 截到 120 字符，可为 null
}]
```

约束：

- `recent` 与 `waiting` 字段保持原样不动（滚轮循环 `Cycle()` 在用）。
- 所有字符串字段在 Python 侧就截断。**实测校正**：本机 29 个真实会话下单帧 17KB（我原先估的 7.5KB 偏低——
  截断按字符算而 UTF-8 中文占 3 字节/字），60 行满额最坏约 110KB，即每 2 秒约 55KB/s。
  这个量对 `System.Text.Json` 与管道都可忽略，**不设字节预算**。
- 白名单只收面板真消费的字段：`path` 与 `cwd` 不在其中（没有任何页面用到，且 `project` 已由 `cwd` 派生）；
  `pending.options` 保留，因为 §5 概览页要求就地显示待处理问题的选项。
- `--watch` 的异常帧（引擎抛错时那条 `ok:false`）**不带** `sessions`；C# 侧 `Snapshot.Sessions`
  须按 null 处理，消费方要能容忍空表。
- `C#` 侧 `Snapshot` 增加 `List<SessionRow> Sessions`，旧字段不受影响；`System.Text.Json`
  对多余字段本来就宽容，所以这是向后兼容的。
- 引擎异常路径（`--watch` 的 try/except）不变。

## 5. 面板页面规格

左侧导航是原生 `ListBox`（6 项固定，样式对齐 Fluent 的导航条），右侧一个 `ContentControl`
宿主页面。内容区一律「分组卡片 + 行」结构，每行是 **标题 + 说明文字 + 右侧控件**，
分组与行由 `bar/Panel/Ui.cs` 里的 `Ui.Group()` / `Ui.Row()` 工厂自绘（对应 AF-Media-Bar 的
`SettingsGroup` / `SettingsRow` 词汇，但那两个在参考项目里也是自绘件，不是库件）。

| 页面 | 内容 | 右侧控件 |
| --- | --- | --- |
| 概览 | 顶部大字当前状态 + 状态色条 + 静默时长 + 余额；中部**全部会话表**（项目 / 状态徽章 / 轮次 T·S / 静默 / 任务进度 / 最近工具 / tokens）；底部待处理列表（正文 + 选项） | `SettingsGroup` + 原生 `DataGrid` + `Badge` |
| 通知 | 提示总开关、重复提醒间隔秒数 | `ToggleSwitch`、`NumberBox` |
| 外观 | 主题（浅色 / 深色 / 跟随系统，**默认浅色**）、任务栏状态条显示开关 | 原生 `RadioButton` 分组、`ToggleSwitch` |
| 运行 | 轮询间隔、开机自启、重启检测进程、打开日志目录、打开数据目录、当前阈值只读展示 | `NumberBox`、`ToggleSwitch`、`Button` |
| 余额 | Key 录入与清除（走 DPAPI）、当前来源（env / dpapi / file / none）、环境变量覆盖提示、立即刷新、余额明细 | 原生 `PasswordBox`、`Button`、`InfoBar` |
| 关于 | 版本号（读程序集 `Version`）、MIT 全文、第三方与致谢清单 | `SettingsGroup` + 只读文本 |

`KeyDialog.cs` 删除，能力并入「余额」页——同一功能不留两处入口。
托盘菜单「设置余额 Key…」改为直接打开面板并定位到余额页。

**每页可独立打开**：`--panel <page>` 支持 `overview|notify|appearance|runtime|balance|about`，
沿用项目已有的 `--demo <state>` 惯例，为的是让 §9 的视觉门禁可自动化。

**另加 `--panel-shot <page> <out.png>`**：在进程内用 `RenderTargetBitmap` 把该页渲染成 PNG 后退出。
必要性见 §12——实测 `PrintWindow` 对 `FluentWindow` 会返回内容全白、标题栏错位的废图，
不能用来验面板；而 `RenderTargetBitmap` 不经过屏幕合成，不受遮挡影响，也不需要窗口真的可见。

**再加 `--panel-scroll <page> <滚前.png> <滚后.png>`**（2026-10-06 补）：把该页按真窗口给它的
有限高度（同 `--panel-shot` 的 724×552）排一遍，量它内部 `ScrollViewer` 的
`extent / viewport / offset / ScrollableWidth`，`ScrollToEnd` 前后各出一张定帧，然后打印一行
`SCROLL <page> <w> <h> <extent> <viewport> <off0> <off1> <scrollableWidth>`。
为什么滚动这件事要应用自己滚给冒烟看：本机实测三条合成输入通路全都送不达面板窗口
（定向 `PostMessage WM_MOUSEWHEEL`、抬到最上层后的 `SendInput` 真滚轮、连侧栏导航项的左键点击
都不换页），而窗口激活/失焦会让 Mica 背衬整帧换色——1200×800 的 800 行里 800 行逐行对不上，
可表格顶沿、表头墨迹带、分隔线条数一个都没变。于是「画面变了多少行」在真窗口里不再是
「滚没滚」的证据；改由应用交出滚动前后的两张定帧，冒烟逐行比「表头冻住、表体换内容」。

## 6. 设置模型与生效矩阵

`Settings.cs` 现有 `interval` / `notify` / `repeatSec`，新增 `theme`（默认 `"light"`）与
`showBar`（默认 `true`）。

| 字段 | 生效方式 |
| --- | --- |
| `notify` / `repeatSec` / `theme` / `showBar` | 热生效（保存即作用于当前内存实例） |
| `interval` | 保存时调用已有的 `RestartClient()` |
| 余额 Key | 走已有的 `--save-balance-key` 子进程 + `refresh.token` 强刷 |
| 开机自启 | 走已有的 `ToggleAutostart()`（`HKCU\...\Run`） |

面板与状态条共用同一个 `Settings` 实例，退出时 `Cleanup()` 统一落盘，不存在双写覆盖。
`Settings.Load` 已有的"损坏即回退默认值"行为保持不变。

`showBar=false` 时：调用 `Native.Undock()` 并隐藏 `BarWindow`，watchdog 跳过 `PlaceNow`；
恢复时重新 `DockNow`。托盘图标不受影响（否则没有入口回到面板）。

## 7. 主题

按既有偏好**面板默认浅色（白底黑字）**，可在外观页切到深色或跟随系统。
任务栏状态条继续走 `Theme.cs` 的系统深浅色探测，两者互不影响——状态条贴在任务栏上必须跟任务栏一致，
面板是独立窗口按用户偏好。WPF-UI 的主题通过 `ApplicationThemeManager.Apply` 运行时切换。

## 8. 开源合规落地

新增 `LICENSE`：MIT，`Copyright (c) 2026 Wildcreator`。

新增 `THIRD-PARTY-NOTICES.md`，逐条附许可证原文（不是只给链接），条目与依据：

1. **WPF-UI 4.2.0** — MIT，`Copyright (C) 2021-2025 Leszek Pomianowski and WPF UI Contributors`，
   https://github.com/lepoco/wpfui （依据本地包内 `LICENSE.md` 与 nuspec 的 authors/license 字段）
2. WPF-UI 自身内含、需要继续向下传递的 5 项（依据包内 `ThirdPartyNotices.txt`）：
   - `sbaeumlisberger/VirtualizingWrapPanel` 2.0.6 — MIT，© 2019 S. Bäumlisberger
   - `microsoft/fluentui-system-icons` 1.1.242 — MIT，© 2020 Microsoft Corporation
   - `dotnet/wpf` 8.0 — MIT，© Microsoft
   - `microsoft/microsoft-ui-xaml` 3.0 — MIT，© Microsoft
   - `microsoft/segoe-fluent-icons-font` 3.0 — **微软专有字体许可，非 MIT**，单独标注来源与限制
3. **AF-Media-Bar** — MIT，© 2026 AmorFate，https://github.com/Fervent-Tempo/AF-Media-Bar ；
   声明为"任务栏停靠与设置界面布局的设计参照，未复制其源代码"
4. .NET 10 Windows Desktop Runtime、Python 3.14 标准库 —— 运行时依赖，不随附其代码，注明版本要求

`Vigil.csproj` 补元数据并对齐 AF-Media-Bar 的做法（`Version` 作为版本号唯一来源，
运行期从程序集读）：`PackageLicenseExpression=MIT`、`Authors`、`Copyright`、
`RepositoryUrl`、`Description`、`Product`。

README 改动：新增「开源协议」与「致谢」两节；**删除「编译不需要联网」**这句已不成立的话，
构建章节补上首次 `dotnet restore` 需要联网；文件清单表加 `LICENSE` / `THIRD-PARTY-NOTICES.md` /
面板相关文件；「关于」页在应用内渲染同一份清单，不只是躺在仓库里。

## 9. 测试与验收

扩展 `smoke_test.py`（当前 64 项全绿，必须保持全绿）：

- **契约**：`sessions` 存在、条数 ≤ 60、每行字段齐全、`state` 全部落在 `STATES` 内、
  `recent`/`waiting` 结构未变（回归保护）
- **面板**：`--panel-shot <page>` 逐页渲染成 PNG，断言图像尺寸 ≥ 720×480、颜色种类 > 200
  （防空白页/纯色），且「概览」页在引擎有 25 个会话时颜色种类显著高于「关于」页
  ——证明会话表真画出了东西。窗口本身的存在性与尺寸另用 `--panel <page>` + Win32 枚举校验
- **状态栏**：继续用 `PrintWindow`，但必须按窗口类型选 flag：分层窗口（BarWindow）用
  `PW_RENDERFULLCONTENT`，普通窗口用 `0`；抓回颜色种类 ≤1 视为失败并自动换另一种 flag
- **设置往返**：改 → 存 → 重新加载 → 比对；`theme`/`showBar` 新字段持久化
- **二次实例转交**：已有实例时再启动 `--panel balance`，断言原进程收到请求、面板被打开、
  第二个进程退出码 0
- **合规**：`LICENSE` 与 `THIRD-PARTY-NOTICES.md` 存在，且包含 WPF-UI / lepoco / AmorFate /
  AF-Media-Bar / Bäumlisberger / Segoe 这些必要条目
- **编译**：继续 0 错误 0 警告硬门禁
- Python 侧新增断言进 `test_dsh_state.py`（`sessions` 截断与上限）

人工验收：浅色主题下面板 6 页逐页目视；`showBar` 开关来回切；余额 Key 录入后概览页余额刷新。

## 10. 非目标（明确不做）

- 不在面板里编辑 `ACTIVE_WINDOW` 等阈值（需要引擎反向读配置，新增耦合），只读展示
- 不做会话历史图表 / token 趋势
- 不做在面板里直接回答待处理问题（反向控制 DeepSeek Harness）
- 不引入 MicaWPF（WPF-UI 自带 Mica 支持）
- 不做安装程序、自动更新、多语言资源
- 不照搬 AF-Media-Bar 的 10 页结构凑页面数

## 11. 文件清单

新增：`LICENSE`、`THIRD-PARTY-NOTICES.md`、
`bar/Panel/PanelWindow.xaml(.cs)`（唯一用 XAML 的文件，spike 已验证可与纯代码 `Main` 共存）、
`bar/Panel/Pages/{Overview,Notify,Appearance,Runtime,Balance,About}Page.cs`（**纯 C# 构建**，
与仓库现有 `BarWindow.cs` 的全代码风格一致，不再引入第二套 XAML 页面机制）、
`bar/Panel/Ui.cs`（`Group()` / `Row()` 分组与行工厂）、
`docs/superpowers/specs/2026-10-03-dsh-status-panel-design.md`（本文档）

面板全部收在 `bar/Panel/` 下，避免把 18 个新文件平铺进现在只有 9 个 `.cs` 的 `bar/`；
`UseWPF=true` 时 SDK 按 `**/*.xaml` 递归收集，子目录不影响构建。

修改：`bar/Vigil.csproj`（WPF-UI 引用 + 元数据）、`bar/App.cs`（面板入口、`--panel`、
panel.request 轮询、showBar 处理）、`bar/Settings.cs`（两个新字段）、
`bar/StateClient.cs`（`Snapshot.Sessions` 与 `SessionRow`）、
`dsh_state.py`（`sessions` 输出）、`test_dsh_state.py`、`smoke_test.py`、`README.md`

删除：`bar/KeyDialog.cs`（并入余额页）

实现顺序（每步都可独立编译验证、互不阻塞）：
① §8 合规文件 + csproj 元数据 + README 校正 → ② §4 引擎 `sessions` 契约 + Python 断言 →
③ csproj 引 WPF-UI + `PanelWindow` 空壳能开能关 → ④ 设置模型两新字段 + 生效矩阵 →
⑤ 6 个页面逐个填实（概览最后做，它依赖 ②）→ ⑥ 删 `KeyDialog`、接面板入口 →
⑦ §9 冒烟扩展 + 全量回归。

## 12. 风险

| 风险 | 应对 |
| --- | --- |
| WPF-UI 的 `FluentWindow` 与现有 `AllowsTransparency` 分层窗口共存可能互相干扰 | 面板是普通顶层窗口，不参与停靠；`--panel` 与 `--demo` 一样先只验证渲染，不碰 `Native.Dock` |
| 引入 XAML 后构建方式变化（需要 `Page` 项） | `UseWPF=true` 时 SDK 自动包含 `**/*.xaml`，探针工程已验证可编译；仍列入 §9 的编译门禁 |
| Segoe Fluent Icons 字体许可不是 MIT，可能被误当作可自由再分发 | 在 `THIRD-PARTY-NOTICES.md` 里显式标注，且只依赖系统已装字体、不随包分发字体文件 |
| `sessions` 变大拖慢每 2 秒的解析 | 60 条上限 + Python 侧截断；`smoke_test` 的 live 冒烟已断言单轮扫描 < 120ms |
| 二次实例改成静默转交后，用户误以为没启动成功 | 转交时原实例把面板带到前台并聚焦，视觉反馈比弹框更明确 |
| **`PrintWindow` 抓 `FluentWindow` 会得到废图**（spike 实测：`PW_RENDERFULLCONTENT` 返回内容全白、只有 1 种颜色；flag=0 返回 194 种颜色但仍是标题栏倒扣在窗口底部的错乱图），屏幕抓取又会被其它窗口遮挡 | 面板视觉门禁全部改走进程内 `RenderTargetBitmap`（`--panel-shot`），不依赖窗口合成与屏幕可见性；`PrintWindow` 只留给分层的状态栏 |

## 13. 未决

- 署名 `Wildcreator` 是否为期望的版权人写法（GitHub id 是 `Wildcreator2010`）
- 本目录不是 git 仓库，是否要 `git init` 以便按流程提交 spec 与后续改动

---

# 增补（2026-10-04）：品牌视觉 —— 落日猎人配色、毛玻璃外壳、关于页与 Logo

用户口径：面板要「毛玻璃 + 圆角」的搭配；配色参考 `Desktop/配色积累`；**不得再出现紫色**。
原文里「疑似卡住」用的 `#7C3AED` 由本增补替换。

## V1 色板来源与取色方法

参考图共 5 套（`配色积累 (1).png` 夜间飞行 / `(1).jpg` 完美风暴 / `(2).jpg` 落日猎人 /
`(3).jpg` 水晶海豚 / `(4).jpg` 超级海鸥）。两条纪律：

1. **色号一律从图里采样像素得到，不采信图上印的 hex。** 实测发现「落日猎人」的
   Forest Green 印成了 `#1F2A36`，与「夜间飞行」的 Wet Asphalt 重号，真实色块是
   `#3E6143` —— 照着印的抄会抄错一个色。
2. 含紫色的两套（夜间飞行 `#8E7CFF`、水晶海豚 `#A26DAA`）整套不用。

选定：**界面主色用「落日猎人」，状态色以「完美风暴」为主、两枚暖色取自「落日猎人」**。
理由：落日猎人的橙/红/棕/金扎堆，11 个状态分不开；完美风暴是低饱和冷灰绿 + 一枚荧光
信号色，天然像状态码，且与帐篷+树的 Logo 同属户外调性。

## V2 界面色（面板与状态条）

| 用途 | 浅主题 | 深主题 | 备注 |
| --- | --- | --- | --- |
| 强调色（导航选中条、链接、关于页 Logo 呼应） | `#D87D44` 暮光橙 | `#EA8C70` 落日暮光 | 深色下提亮一档，避免橙压在黑底上发闷 |
| 次强调 | `#D6A44B` 复古金 | `#D6A44B` | |
| 主文字 | `#2B2A23` 夜影黑 | `#EDE9E1` | |
| 次级文字 | `#5D4B3F` 松木灰 | `#B0A788` 黄昏云 | |
| 卡片底（**不透明**，见 V3） | `#FBF8F3` 暖纸白 | `#38434A` | 暖纸白是自定的第 10 色：参考图无白底，取落日猎人的暖调底 |
| 卡片描边 | `#B0A788` 黄昏云 | `#5A6363` 哨站灰 | |
| 圆角 | 卡片 12、按钮/输入框 8 | 同 | 从 8 提到 12，与毛玻璃搭配 |

## V3 毛玻璃的范围（这条是工程约束，不是审美选择）

窗口外壳用 WPF-UI 的 **Acrylic** backdrop，左侧导航栏半透明，**内容卡片一律不透明**。

原因：面板的像素门禁（`check_panel_direct_page`）判的是「真窗口页面取样框的配色分布 =
该页离屏参照」。离屏 `RenderTargetBitmap` 拿不到系统合成的 backdrop，而真窗口一旦让桌面
渗进取样框，比样的两侧就不是同一块内容 —— 门禁会随桌面壁纸时红时绿。卡片不透明之后，
取样框里全是确定色，毛玻璃只出现在窗口边缘与侧栏（不在取样框内）。

## V4 状态色（11 档，替换引擎 STATES 与前端映射两处）

| 状态码 | 中文 | 新色 | 来源 | 旧色 |
| --- | --- | --- | --- | --- |
| needs_action | 需要操作 | `#D87D44` | 落日猎人 暮光橙 | #DC2626 |
| error | 出错了 | `#C65B51` | 落日猎人 晚霞红 | #BE123C |
| stalled | 疑似卡住 | `#C3A559` | 完美风暴 旧港锈黄 | **#7C3AED（紫，废除）** |
| thinking | 正在思考 | `#92A5A0` | 完美风暴 边境雾蓝 | #D97706 |
| tool_running | 正在执行工具 | `#54644A` | 完美风暴 林冠绿 | #0D9488 |
| answering | 正在回答 | `#7F8E6B` | 完美风暴 高架苔绿 | #2563EB |
| done | 回答完成 | `#BFD268` | 完美风暴 荧光信号绿 | #16A34A |
| aborted | 已中断 | `#5A6363` | 完美风暴 哨站灰 | #64748B |
| idle | 待命 | `#D2D2C8` | 完美风暴 封锁线灰 | #6B7280 |
| offline | 未运行 | `#2E3844` | 完美风暴 夜巡蓝 | #9CA3AF |
| unknown | 未知 | `#8E918A` | 完美风暴 混凝土灰 | #9CA3AF |

语义排布：「需要操作」用橙、「出错」用红（红=坏了，橙=该你了），比原来两枚红更分得开。
两侧必须逐项一致 —— Task 8 的门禁「前端状态映射的中文标签与配色逐项等于引擎 STATES」
正是钉这一条的，改一边必红。

## V5 关于页（照 AF-Media-Bar 的结构，不是照它的文案）

参考 `Fervent-Tempo/AF-Media-Bar` 的 `ViewModels/Pages/AboutViewModel.cs` 与
`Classes/Models/Credits/*`。它那一页由四块组成，本项目的对应物：

1. **身份区**：Logo 大图 + 项目名 + 版本 + 作者 **Wildcreator** + 仓库链接。
2. **开源许可清单**：条目 = 名称 / 版本 / SPDX / 项目地址 / 用途说明。
   **必须与 `THIRD-PARTY-NOTICES.md` 和 `Vigil.csproj` 的真实依赖逐项一致** ——
   AF-Media-Bar 自己的注释就写着「否则这一节就是装饰」。落地方式：清单从
   `THIRD-PARTY-NOTICES.md` 解析出来，而不是在 C# 里再手抄一份（手抄必然漂移）。
3. **运行时与素材声明**：.NET 运行时、Segoe Fluent Icons 这类**非 MIT** 项单独标出。
4. **数据与诊断区**：数据目录、日志位置（复用运行页已有的 `RevealInExplorer` 通路）。

不做赞赏码/赞助者/贡献者头像联网拉取：本项目单机自用，联网清单会引入新的隐私面。

## V6 Logo

`Desktop/WildCommunity.png`（2684×2684 白底手绘帐篷+树）→ 缩放并抠出透明底，产出
`bar/assets/logo.png`（256）与托盘用的多尺寸 `bar/assets/dsh.ico`（16/32/48）。
托盘图标改用 Logo；关于页顶部放 256 大图。抠底用「近白像素转 alpha」的阈值法，
因为原图是白底单色线稿，没有半透明过渡区需要保留。

---

# 增补（2026-10-07）：实现期间对设计的偏离与验收口径修正

用户口径：面板「暗角太大、没按给的色板写」。这一轮把 V1–V6 真正落到像素上，
过程中对原设计有九处偏离，全部记回这里（照 30ce386 那次的口径：改了的要写回来，
别让文档停在没实现的状态）。

1. **外壳与内容必须同一套主题**（V3 的工程缺口）。`ApplicationThemeManager.Apply`
   只换 WPF 资源字典，管不到具体窗口的非客户区：DWM 的标题栏明暗与背衬染色一直停在
   **系统主题**上。本机系统深色 + 面板浅色 = 白内容糊一圈黑框，就是用户骂的"暗角"。
   补法是 `WindowBackgroundManager.UpdateBackground(窗口, 主题, 材质)`，
   且构造期没有 HWND、那一步是空转，要由 `SourceInitialized` 再补一次。
2. **V2 色板此前一个像素都没落地**。面板颜色一律吃 WPF-UI 的主题键，而 WPF-UI 深色调到
   的是 Windows 默认 `#202020`/`#2B2B2B`。新增一层品牌色板覆盖（Color 与 Brush 两个键一起给，
   追加在合并字典最后抢优先级，并同步挂到窗口级）。
3. **V2 的「卡片底 浅 = 暖纸白」给卡片，不给页面**。表上只定了卡片那一格；页面底照同一支
   色卡深一档自定为 `#F2EBE0`，否则卡片浮不起来。
4. **V2 圆角落地**：非玻璃档卡片 12（原来是 14），按钮/输入框 8（走 `ControlCornerRadius` 键）。
5. **V2 深档强调色 `#EA8C70` 此前不存在**：两套主题都写死 `#D87D44`。改成主色随主题重建，
   `Default/Secondary/Tertiary` 由主色按系数算出，不再手抄四个数。
6. **V4「不得再出现紫色」的判据从源码升级为像素**。扫 hex 字面量挡不住控件从**系统强调色**
   派生的画刷 —— ToggleSwitch 与 HyperlinkButton 都是这么漏的（改前那张关于页里有
   1114 个紫色像素而源码门禁全绿）。现在六页 × 深浅两档逐像素扫 245°~300° 色相。
7. **V5 清单的条目判定**：原来是"有 `- **用途**：` 行才算一条"，于是 4.3 CPython embeddable
   整条消失（那一节写的是版本下限/许可证/原文随附）。改成"有任一字段行即算条目"。
   漏掉的恰好是随产物分发的那一项。
8. **V6「关于页顶部放 256 大图」按素材尺寸理解**，渲染 128 DIP：本机 125% 缩放下 256 DIP
   要把 256px 的图放大到 320 物理像素，反而糊。
9. **§5 概览「待处理列表（正文 + 选项）」的选项不在 `waiting` 里**。§4 保留
   `sessions[].pending.options` 的理由正是这一格，而 `waiting` 条目只有 project/text/age/key。
   C# 侧补接 `Brief.Key`（引擎本来就发），按 key 对回选项显示。


10. **V3「内容卡片一律不透明」只对云母 / 毛玻璃两档成立，「液态玻璃」档不成立**（这一条是
    裁决，不是遗漏）。V3 给的理由是像素门禁：离屏 `RenderTargetBitmap` 拿不到 DWM 合成的
    backdrop，卡片一旦半透，桌面就会渗进取样框，比样随壁纸时红时绿。后来加的三档材质里
    「液态玻璃」整档卖点就是透，所以那一档的卡片保留半透（alpha `0xB8` ≈ 72%），
    云母 / 毛玻璃两档仍走不透明的主题刷。72% 是量出来的下限：再低，换壁纸就能把
    `check_backdrop_material` 换红。V3 的原始约束在两档里照旧生效。

**§9 验收口径两处修正**（都是"参照物被这一轮改动改变"，不是放松判据）：

- 「概览页色数显著高于关于页」作废：它靠关于页是 7 色透明占位页撑出区分度，而关于页补齐后
  成了最丰富的一张，这条会永远红。参照换成 spec §9 本来就写的绝对下限（颜色种类 > 200）。
- 「窗口收到最小尺寸时运行页可滚」换成两分支：**可滚，或末行按钮仍在视口内**。
  阈值行改成整行堆叠后运行页变矮、最小尺寸放得下了 —— 放得下不是缺陷，
  「放不下又滚不动、内容被裁在视口外」才是这条要挡的事。
  「页面自带 ScrollViewer」这条外壳契约改钉在天生就长的关于页上（清单 + MIT 全文必然超视口）。
