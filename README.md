<div align="center">

<br>

<img src="assets/AutoFluent_icon_512.png" width="96" alt="AutoFluent">

<h1>AutoFluent</h1>

<h3>From simulation to engineering intelligence.</h3>

<p><strong>让每一次仿真，都成为下一次设计的依据。</strong></p>

<p>专为批量式 ALD 反应腔打造的 CFD 分析与结构优化工作站。<br>
从原生求解数据出发，将复杂流场转化为清晰、可验证的工程决策。</p>

<a href="https://github.com/1463503175-pixel/AutoFluent/releases/download/2.1/AutoFluent.exe"><img alt="Download for Windows" src="https://img.shields.io/badge/Download%20for%20Windows-0B776D?style=for-the-badge&logo=windows&logoColor=white"></a>
<a href="docs/GETTING_STARTED.md"><img alt="Quick start" src="https://img.shields.io/badge/Quick%20start-24292F?style=for-the-badge"></a>

<br>

<a href="https://github.com/1463503175-pixel/AutoFluent/releases/tag/2.1">Release 2.1</a> &nbsp;·&nbsp; <a href="worker/">分析引擎</a> &nbsp;·&nbsp; <a href="templates/report_v11/">报告模板</a> &nbsp;·&nbsp; <a href="https://github.com/1463503175-pixel/AutoFluent/issues">问题反馈</a>

<sub>Windows x64 &nbsp; / &nbsp; ANSYS Fluent HDF5 &nbsp; / &nbsp; Local-first workflow</sub>

<br><br>

</div>

<img src="docs/media/brand.svg" width="100%" alt="Beyond the residuals. Understand the flow.">

## Beyond the residuals.

**仿真结束，不意味着分析完成。**

传统 CFD 后处理依赖大量重复的截面构造、通量提取、数据整理和人工对照。AutoFluent 将这些工作整合到同一个分析环境：直接读取 Fluent 保存解，追踪气体如何经过反应腔，定位偏流与供气差异，并为下一轮结构优化提供定量依据。

不止是看云图，而是理解气体去了哪里、哪里分得少，以及改变结构之后究竟发生了什么。

## What’s new in 2.1

- **独立物种库存统计**：将气相物种库存计算提前，即使晶圆几何未成功识别，也可在相关字段有效时保留已取得的库存数据。
- **ALD 阶段分析适配**：有物种分析数据时即可进入阶段报告逻辑，不再要求入口同时含四种指定物种；阶段快照携带库存统计。
- **完整报告资源同步**：从当前发布程序恢复 HTML、CSS、JavaScript 及可取得的分析资源。

## What you can do

<table>
<tr>
<td width="50%" valign="top">
<h3>01 &nbsp; Flow intelligence</h3>
<b>看清流量的真实去向</b>
<p>直接读取原生质量通量，分析片间、中央及外围旁路，建立入口到出口的流量收支。</p>
</td>
<td width="50%" valign="top">
<h3>02 &nbsp; Spatial diagnostics</h3>
<b>把异常定位到具体结构</b>
<p>独立解析晶圆层、排与通道，追踪最弱位置、均匀性 CV、挡板孔位和集气腔压力。</p>
</td>
</tr>
<tr>
<td valign="top">
<h3>03 &nbsp; Reaction insights</h3>
<b>从流动延伸到反应过程</b>
<p>在对应求解字段可用时，分析物种浓度、暴露相关量和表面反应位点，观察反应区域的空间差异。</p>
</td>
<td valign="top">
<h3>04 &nbsp; Design iteration</h3>
<b>让方案变化有数据依据</b>
<p>核对工况、统计截面及分区口径，比较不同方案的流量分配、均匀性和压力损失。</p>
</td>
</tr>
</table>

## One workflow. From data to insight.

|  |  |
|:--|:--|
| **01 — Import** | 选择 Fluent 保存的 Case/Data，自动检查配对与原生字段。 |
| **02 — Analyze** | 识别几何与统计区域，计算片间、旁路、挡板、压力等可取得的指标。 |
| **03 — Verify** | 独立检查分区闭合、质量守恒、缺失字段和比较条件。 |
| **04 — Optimize** | 生成 V11 分析报告，定位问题并建立下一轮方案的对照依据。 |

### Two ways to work

**Offline analysis** — 日常分析无需启动 Fluent。直接使用本地保存解，不改变原文件。

**Connected workflow** — 需要统一工况或继续求解时，连接已有 Workbench / Fluent 会话，经过确认后执行受控操作。

## Get started

下载最新的一体化 Windows 程序，放在本地可写目录，双击运行。在保存解分析中选择一份 `.cas.h5` 或 `.dat.h5`，确认文件配对及几何预检后开始分析。

**[Download AutoFluent 2.1](https://github.com/1463503175-pixel/AutoFluent/releases/download/2.1/AutoFluent.exe)** &nbsp; · &nbsp; [完整使用指南](docs/GETTING_STARTED.md) &nbsp; · &nbsp; [几何配置](templates/geometry_config_example_staggered.json)

## Explore the project

[Analysis engine](worker/) &nbsp; · &nbsp; [Report V11](templates/report_v11/) &nbsp; · &nbsp; [Windows application](app/) &nbsp; · &nbsp; [Documentation](docs/)

<details>
<summary><strong>Release integrity &amp; scientific scope</strong></summary>

当前仓库以 2026-11-22 上传的 Release 2.1 为准。可恢复的 Python 分析模块、模板及图标来自发布程序，展示文档独立维护。本次已从集成程序原样恢复 60 个项目文件，其中 50 个 Python 分析模块。

- [SHA-256 校验清单](SHA256SUMS.txt)
- [原始文件恢复记录](docs/release_inventory.json)

集成 EXE 未包含可恢复的完整桌面前端源码及构建定义。当前静态核验不代表所有 Windows / Fluent 真机功能已验收。流场指标不能直接证明成膜均匀性；保存快照不能代替完整反应剂量或收敛历史。未取得的数据与不满足比较条件的结果应独立标注。

</details>

---

<div align="center">

<sub>AutoFluent &nbsp;·&nbsp; Built for ALD research and engineering exploration.</sub>

</div>
