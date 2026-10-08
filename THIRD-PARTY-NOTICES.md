# 第三方声明与开源许可（Third-Party Notices）

Vigil（Wildcreator）本身以 **MIT 许可证** 发布，全文见仓库根目录的 [`LICENSE`](LICENSE)。

本文件列出项目中用到的第三方组件、素材与参考项目，每条给出**用途 + 上游地址 + 许可证 + 版权行**；
**凡随产物分发的条目**（第 1、2、3 节，第 4 节里自包含载荷带走的 .NET 运行时，以及第 4 节
按版本走的引擎、第 5 节编进 `vigil-engine.exe` 的 zstd 解压子集）都按上游要求附上许可证原文（不是只给链接）。

同一份源码出**两个版本**（`tools/flavors.py` 那张表）：**Lilium 1.1.0** 的引擎是 C++ 的
`app\vigil-engine.exe`，**Vachellia farnesiana 1.0.0** 的引擎是随包的 CPython。所以
"哪条随附义务成立"取决于**你手上那份载荷里有什么**：zstd 那一条跟着前者，PSF 那一条跟着后者。
分发物里的原文落在 `licenses/` 目录，由 `tools/collect_licenses.py` 从 NuGet 缓存、
`engine/third_party/zstd/`、以及**解压完的 `app\runtime\python\LICENSE.txt`** 收集，
`tools/verify_package.py` 会按识别出的版本断言该有的那一份存在 —— 少一份就不出包。

核实依据（2026-10-03 实测核对，未凭记忆改写）：本机 NuGet 包缓存
`%USERPROFILE%\.nuget\packages\wpf-ui\4.2.0\` 内的 `LICENSE.md`、`ThirdPartyNotices.txt`、
`wpf-ui.nuspec` 三个文件；AF-Media-Bar 只通过 GitHub API 核实到**许可证类型为 MIT**，
其上游 `LICENSE` 文件本机不可得，未逐字比对（见第 3 节的明确口径）。

---

## 1. WPF-UI 4.2.0 — MIT

- **用途**：控制台面板窗口的 Fluent 外壳与表单控件（按规划在后续任务以 `PackageReference` 引入；
  截至本文件写成时，`bar/Vigil.csproj` 尚未引用该包）。
- **上游**：https://github.com/lepoco/wpfui ｜ NuGet 包 `WPF-UI` 4.2.0，`authors = lepo.co`
- **许可证**：MIT（nuspec `<license type="expression">MIT</license>`，且 `requireLicenseAcceptance = true`）
- **版权行**（nuspec `<copyright>`）：`Copyright (C) 2021-2025 Leszek Pomianowski and WPF UI Contributors`
  包内 `LICENSE.md` 的同一表述为 `Copyright (c) 2021-2025 Leszek Pomianowski and WPF UI Contributors. https://lepo.co/`
- **附带说明**：`WPF-UI` 4.2.0 依赖同仓库、同作者、同许可证的子包 `WPF-UI.Abstractions` 4.2.0
  （MIT，`Copyright (C) 2021-2025 Leszek Pomianowski and WPF UI Contributors`），许可证原文与下面一份相同，不重复列出。

许可证原文（包内 `LICENSE.md` 全文）：

```
MIT License

Copyright (c) 2021-2025 Leszek Pomianowski and WPF UI Contributors. https://lepo.co/

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 2. WPF-UI 内含、需继续向下传递的 5 项

依据包内 `ThirdPartyNotices.txt`。该文件开头写明 **“Do Not Translate or Localize”**，
因此下列原文一律按英文原样附出、不翻译；这些组件随 WPF-UI 一起进入产物，
其声明义务由本项目继续向下游传递。

标点同样以来源为准：上游 `wpf-ui` 包内 `ThirdPartyNotices.txt` 的 §2.4（microsoft-ui-xaml）那一段，
末尾本来就缺一个句号（该行以 `SOFTWARE` 收尾，第 116 行原文如此；正文已与上游逐字核对，
行尾按本仓库既有的纯 LF 约定），本文照原样保留、未作补正。第三方许可证声明的职责是逐字忠于来源，
不得为排版完整而改动来源，请勿替它把那个句号补上。

### 2.1 sbaeumlisberger/VirtualizingWrapPanel 2.0.6 — MIT

- **用途**：WPF-UI 虚拟化换行面板的实现来源，本项目经由 WPF-UI 间接使用，不直接引用其源码。
- **上游**：https://github.com/sbaeumlisberger/VirtualizingWrapPanel
- **版权行**：`Copyright (c) 2019 S. Bäumlisberger`

```
MIT License

Copyright (c) 2019 S. Bäumlisberger

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### 2.2 microsoft/fluentui-system-icons 1.1.242 — MIT

- **用途**：WPF-UI 内置系统图标（字形与图标资源）的来源。
- **上游**：https://github.com/microsoft/fluentui-system-icons
- **版权行**：`Copyright (c) 2020 Microsoft Corporation`

```
MIT License

Copyright (c) 2020 Microsoft Corporation

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### 2.3 dotnet/wpf 8.0 — MIT

- **用途**：WPF-UI 的部分 WPF 控件与主题代码取材自 .NET 的 WPF 仓库。
- **上游**：https://github.com/dotnet/wpf
- **版权行**（以包内原文为准）：`Copyright (c) .NET Foundation and Contributors`
  （`.NET Foundation` 是微软侧的 .NET 项目基金会；设计文档里简记为 “© Microsoft Corporation”，
  此处以上游 `ThirdPartyNotices.txt` 的实际版权行为准。）

```
The MIT License (MIT)

Copyright (c) .NET Foundation and Contributors

All rights reserved.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### 2.4 microsoft/microsoft-ui-xaml 3.0 — MIT

- **用途**：WPF-UI 的 WinUI 风格控件与主题资源参照自 WinUI（Microsoft UI Library）。
- **上游**：https://github.com/microsoft/microsoft-ui-xaml
- **版权行**：`Copyright (c) Microsoft Corporation. All rights reserved.`

```
MIT License

Copyright (c) Microsoft Corporation. All rights reserved.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE
```

### 2.5 microsoft/segoe-fluent-icons-font 3.0 — 微软专有字体许可（**非 MIT**）

- **用途**：任务栏状态条与控制台面板上使用的 `Segoe Fluent Icons` 图标字形。
- **上游**：https://learn.microsoft.com/en-us/windows/apps/design/style/segoe-fluent-icons-font
- **许可证**：**这不是开源许可证**，是微软针对该字体/字形的专有许可条款，与上面几项 MIT 有本质区别。
- **本项目的做法（重要）**：
  - 只**引用系统已安装的 Segoe Fluent Icons 字体**（Windows 11 自带），
    **不随安装包分发任何字体文件**，也不把字形子集嵌进产物。
  - 对该字体的一切使用都受微软自身的许可条款约束：仅可用于设计、开发、测试运行在微软平台上的程序；
    **不得向任何第三方分发或再许可该字体（全部或部分）**。
  - 后果：在未预装该字体的系统上图标可能显示为缺字方框。这属于许可限制，
    **不能靠“把字体一起打包”绕过**——那样做恰恰违反上述条款。

许可证原文（包内 `ThirdPartyNotices.txt` 对应段落，一字未改）：

```
You may use the Segoe and icon fonts, or glyphs included in this file (“Software”) solely to design, develop and test your programs that run on a Microsoft Platform, a Microsoft Platform includes but is not limited to any hardware or software product or service branded by trademark, trade dress, copyright or some other recognized means, as a product or service of Microsoft. This license does not grant you the right to distribute or sublicense all or part of the Software to any third party.  By using the Software, you agree to these terms. If you do not agree to these terms, do not use the Software.
```

---

## 3. AF-Media-Bar — MIT（仅作设计参照，未复制源代码）

- **用途**：**任务栏停靠方式**（把窗口挂为 `Shell_TrayWnd` 子窗口）与**控制台分组/行布局**的
  **设计参照**。本项目**未复制其任何源代码**：`bar/Native.cs` 的 `Dock()` / `Place()` / `FreeRange()`
  等全部为自行实现，只是交互与停靠思路上参考了它。
- **上游**：https://github.com/Fervent-Tempo/AF-Media-Bar
- **许可证**：MIT —— 2026-10-03 经 GitHub API 核实，其仓库许可证**类型为 MIT**。
  因本项目不含其代码，此处仅作归属声明。
- **版权行**：`Copyright (c) 2026 AmorFate`（同一次 API 核实得到的版权行）

许可证原文（**按 SPDX `MIT` 标准文本附出**。上游仓库的 `LICENSE` 文件在本机不可得，
GitHub 网页与 raw 均不可达，因此**未与上游逐字比对**；下面这段是 SPDX MIT 的标准正文 +
核实到的版权行，不是对上游文件内容的转录）：

```
MIT License

Copyright (c) 2026 AmorFate

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 4. 运行时（自包含分发，随产物走）

`package.cmd` 出的是**零前置**产物：`dotnet publish --self-contained` 会把 .NET 10 运行时的
二进制一起打进 `app\`。**这件事构成分发他人的作品**，所以本节不是"只写版本下限"——
MIT 要求版权声明随副本保留，原文已收进分发物的 `licenses\`（见文件开头的口径说明）。

两个版本对 `app\runtime\python\` 的答案不一样：**Lilium（cpp）不带**，它的引擎
`app\vigil-engine.exe` 自带 zstd 解压能力（第 5 节）；**Vachellia（python）带**，
那份 embeddable 解释器就是这一版的引擎，见 §4.3。两个版本的载荷都留着 `app\dsh_state.py`：
在 Lilium 里它是 40KB 的**回退级脚本**（`bar/Engine.cs`：C++ 引擎不在时才用，用的时候要求
目标机自己装有 Python 3.14，我们不分发解释器）；在 Vachellia 里它就是**引擎本体**。

### 4.1 .NET 10 运行时（自包含载荷内）

- **用途**：`Vigil.exe`（WPF + WinForms）的运行环境；`--self-contained` 后不再要求目标机预装。
- **上游**：https://github.com/dotnet/dotnet
- **许可证**：**MIT**，版权行 `Copyright (c) .NET Foundation and Contributors`。
- **原文随附**：`licenses\dotnet-runtime-MIT.txt`（取自
  `microsoft.netcore.app.runtime.win-x64`）、`licenses\dotnet-windowsdesktop-MIT.txt`
  （取自 `microsoft.windowsdesktop.app.runtime.win-x64`）。两者都由 `tools/collect_licenses.py`
  从本机 NuGet 缓存原样复制，不手抄、不改写。
- 注意 §2.3 那份 `dotnet/wpf 8.0` 的 MIT 是 **WPF-UI 的传递依赖声明**，与这里的运行时分发
  是两回事，两者都要保留，别合并。

### 4.2 Python 3.14 标准库（Lilium 版的引擎回退级，**该版不随附解释器**）

- **用途**：`dsh_state.py` 是引擎的**回退级**与对照基准 —— `bar/Engine.cs` 只在
  `vigil-engine.exe` 缺失时（开发检出没跑 `engineuild.cmd`）才用它；而
  `engine/compare_*.py` 那六条对照把它当判据的另一半，永远留着。
- **只使用标准库**，没有任何第三方 Python 包。
- **版本下限**：**Python 3.14**。低于 3.14 时标准库里没有 `compression.zstd`，
  这份脚本读不了 `session.v4.jsonl.zstd`。正常安装碰不到这一级：随包的
  `vigil-engine.exe` 自带解压能力。`Vigil.exe --engine-probe` 报 `kind=` 说清了
  当前用的是哪一级。
- **PSF 那一条随附义务在这一版不成立**：Lilium 的载荷里没有解释器，`dsh_state.py` 用的
  那台 Python 由用户自己提供，不构成"分发他人的作品"。**但 Vachellia（python 版）分发它**，
  所以那条义务在 §4.3 —— 两版共用这一份声明文件，各自成立的部分不同，别读串行。

### 4.3 CPython 3.14.7 embeddable 版 — Python Software Foundation License（**只随 Vachellia 版分发**）

Vachellia（`package.cmd python`）的载荷 `app\runtime\python\` 随附 Python 官方的 Windows
embeddable 包（`python-3.14.7-embed-amd64.zip`，SHA-256
`d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15`），含 `python.exe`、
`python314.dll`、`python314.zip`（标准库）以及 `_zstd.pyd`、`_ssl.pyd` 等扩展模块。
`package.cmd` 下载后**先校 SHA-256 再解压**：未经核对的下载物等于把别人的解释器发给用户。

- **许可证**：**Python Software Foundation License**（BSD 风格，另含若干历史条款）。
- **原文随附两处**：`app\runtime\python\LICENSE.txt`（embeddable 包内自带，未改动）与
  `licenses\python-PSF.txt`（同一份的副本，方便只拿 `licenses\` 目录做审计）。
  全文有 35KB、含大量与本项目无关的历史条款，这里只引当前版本的授权与条件两条：

> **PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2**
>
> 1. This LICENSE AGREEMENT is between the Python Software Foundation
> ("PSF"), and the Individual or Organization ("Licensee") accessing and
> otherwise using this software ("Python") in source or binary form and
> its associated documentation.
>
> 2. Subject to the terms and conditions of this License Agreement, PSF hereby
> grants Licensee a nonexclusive, royalty-free, world-wide license to reproduce,
> analyze, test, perform and/or display publicly, prepare derivative works,
> distribute, and otherwise use Python alone or in any derivative version,
> provided, however, that PSF's License Agreement and PSF's notice of copyright,
> i.e., "Copyright (c) 2001 Python Software Foundation; All Rights Reserved"
> are retained in Python alone or in any derivative version prepared by Licensee.

- 上面两条逐字取自随附的 `LICENSE.txt` 第 73–87 行（不是凭记忆写的）；该文件里那句
  `Copyright (c) 2001 Python Software Foundation` 就是它自己的原文，年份看着少了一位也照原样保留。
- **上游**：https://www.python.org/download/releases/ （官方 FTP 构建）
- **Lilium 版的载荷里没有这一项**，`licenses\python-PSF.txt` 也不会有；`tools/collect_licenses.py`
  是看解压完的载荷决定收哪一份，不是看打包参数。

---

## 5. Meta Platforms zstd 1.5.6 — BSD 3-Clause（**非 MIT**，双许可里取 BSD 这一支）

- **用途**：C++ 状态引擎（`engine/vigil_engine.cpp`）解压 dsh 的会话文件
  `session.v4.jsonl.zstd`。只 vendor **解压子集**（`lib/common` + `lib/decompress`，
  约 1MB 源码），压缩侧一行未取。
- **上游**：https://github.com/facebook/zstd ｜ 取 v1.5.6，源码落在 `engine/third_party/zstd/`
- **许可证**：**BSD 3-Clause**（`Copyright (c) Meta Platforms, Inc. and affiliates. All rights reserved.`）。
  zstd 本身是 **BSD-3-Clause 与 GPL-2.0 双许可**，本项目按 BSD 那一支使用与分发。
  这一项不是 MIT，与上面几项 MIT 有本质区别。
- **为什么必须自带**：本机（Win11 10.0.26300）实测 Windows 自带压缩 API 解不了 zstd ——
  `RtlDecompressBufferEx2` 未导出，`RtlGetCompressionWorkSpaceSize` 对候选格式全拒
  （`C000000D` / `C000025F`）。判据是 `engine/probe_zstd.cpp` 跑出来的，不是读文档推的。
- **当前分发状态**：**随 Lilium（cpp）版分发** —— `package.cmd cpp` 先跑 `engine/build.cmd`，
  把编好的 `vigil-engine.exe` 放进 `app\`，解压子集就编在那颗 exe 里。原文由
  `tools/collect_licenses.py` 从 `engine/third_party/zstd/LICENSE` 原样复制进
  `licenses\zstd-BSD.txt`，`tools/verify_package.py` 断言它在 —— 少这份原文就不出包。
  Vachellia（python 版）不含这颗 exe，那一条义务跟着走；它解 zstd 用的是 CPython 自带的
  `_zstd.pyd`（libzstd 由 CPython 那份 `LICENSE.txt` 一并发下来，见 §4.3）。

许可证原文（`engine/third_party/zstd/LICENSE` 全文）：

```
BSD License

For Zstandard software

Copyright (c) Meta Platforms, Inc. and affiliates. All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

 * Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

 * Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

 * Neither the name Facebook, nor Meta, nor the names of its contributors may
   be used to endorse or promote products derived from this software without
   specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

---

## 6. 汇总

| 组件 | 许可证 | 在本项目中的角色 | 是否随产物分发 |
| --- | --- | --- | --- |
| dsh-status 自身 | MIT（见 `LICENSE`） | — | — |
| WPF-UI 4.2.0（+ WPF-UI.Abstractions 4.2.0） | MIT | 控制台面板的 Fluent 外壳与表单控件 | 是（后续任务引入包后随产物） |
| sbaeumlisberger/VirtualizingWrapPanel 2.0.6 | MIT | WPF-UI 的传递依赖 | 是（经 WPF-UI） |
| microsoft/fluentui-system-icons 1.1.242 | MIT | WPF-UI 的传递依赖（图标） | 是（经 WPF-UI） |
| dotnet/wpf 8.0 | MIT | WPF-UI 的传递依赖 | 是（经 WPF-UI） |
| microsoft/microsoft-ui-xaml 3.0 | MIT | WPF-UI 的传递依赖 | 是（经 WPF-UI） |
| microsoft/segoe-fluent-icons-font 3.0 | **微软专有字体许可（非 MIT）** | 图标字形 | **否**，只引用系统已装字体 |
| AF-Media-Bar | MIT | 停靠方式与面板布局的**设计参照** | 否，未复制其源码 |
| .NET 10 运行时（coreclr / WindowsDesktop） | MIT | 自包含载荷的运行环境 | **是**，`app\` 内二进制 + `licenses\dotnet-*.txt` 原文 |
| Python 3.14 标准库 | PSF License | Lilium 版引擎**回退级** `dsh_state.py` 只用标准库 | **否**（Lilium 不带解释器，见 §4.2）；**是**（Vachellia 的引擎就是它，见下一行） |
| CPython 3.14.7 embeddable | PSF License | **Vachellia 版**的状态引擎解释器 | **是**（仅 Vachellia 版），`app\runtime\python\` + `licenses\python-PSF.txt`，见 §4.3 |
| Meta Platforms zstd 1.5.6（解压子集） | **BSD 3-Clause（非 MIT）** | 编在 `app\vigil-engine.exe` 里解会话文件 | **是**（仅 Lilium 版），`licenses\zstd-BSD.txt` |

如对以上声明有疑问，请开 issue 联系 Wildcreator。
