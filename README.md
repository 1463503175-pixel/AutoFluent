<div align="center">

<img src="docs/assets/autofluent-hero.svg" width="100%" alt="AutoFluent — ALD flow analysis workstation" />

<br />

### Fluent saved solutions. Clearer flow insight.

面向 ALD 反应腔的 Fluent 保存解分析工作台。  
从原生面通量到晶圆片间、挡板、守恒与方案对比，让数据直接服务下一轮结构优化。

<p>
  <a href="AutoFluent_V2.10.5_Windows_x64.zip"><strong>Download for Windows</strong></a>
  &nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="docs/GETTING_STARTED.md">Quick start</a>
  &nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="cmd/autofluent/">Source code</a>
</p>

<sub>Windows x64 &nbsp; / &nbsp; ANSYS Fluent H5 &nbsp; / &nbsp; Go + Python &nbsp; / &nbsp; V2.10.5</sub>

</div>

---

## From simulation to insight

AutoFluent 从已有的 `.cas.h5` 与 `.dat.h5` 文件读取 Fluent 原生数据，将**文件核对、几何识别、质量流量积分、结果验证和报告生成**整合为一套桌面工作流。

离线分析直接使用保存解，无需连接 Workbench，也不会重新初始化或迭代计算。对可以确认的结果独立输出；遇到复杂几何时，支持通过模型配置指定搜索范围和截面分区，避免用未经确认的数量替代真实测量。

<div align="center">

<img src="docs/assets/autofluent-pipeline.svg" width="100%" alt="Select, identify, measure, verify and compare workflow" />

</div>

## Built for flow analysis

<table>
<tr>
<td width="50%" valign="top">
<h3>01 / Native H5 analysis</h3>
<p>直接分析 Fluent Case/Data 保存解，核验文件对应关系、面索引与原生 <code>SV_FLUX</code> 数据。分别统计入口、出口的正向、反向及净质量流量。</p>
</td>
<td width="50%" valign="top">
<h3>02 / Wafer &amp; baffle mapping</h3>
<p>从晶圆壁面片、厚度和重复阵列识别通道；支持普通双排与交错双排的配置辅助分析。挡板孔与截面积分独立校验。</p>
</td>
</tr>
<tr>
<td valign="top">
<h3>03 / Physical verification</h3>
<p>核对质量守恒、片间流量分配、通道 CV、旁路及可用压降。以不同模块的校验状态区分完整结果和部分结果。</p>
</td>
<td valign="top">
<h3>04 / Reports &amp; comparison</h3>
<p>输出结构化 JSON、CSV 与离线 HTML 报告；保留逐通道位置、流量证据及异常诊断。方案对比区分可比指标与不同统计口径。</p>
</td>
</tr>
</table>

## Get started

**01. Download** — 下载 [Windows x64 完整程序包](AutoFluent_V2.10.5_Windows_x64.zip) 并全部解压。  
**02. Open** — 运行 `AutoFluent-目录浏览版.exe`，在「保存解分析」中选择 `.cas.h5` 或 `.dat.h5`。  
**03. Analyze** — 确认文件配对及几何预检，执行离线分析，查看统计结果与 HTML 报告。

[完整使用指南](docs/GETTING_STARTED.md) · [查看配置示例](templates/) · [查看分析报告示例](examples/)

## Two complementary modes

| Offline analysis | Connected workflow |
|---|---|
| **默认模式**。复用已有保存解，进行原生通量积分、晶圆与挡板分析、口径核验及报告导出。 | **按需使用**。连接已有 Workbench / Fluent 会话，进行受控的工况预览、发布与求解任务管理。 |
| 不依赖在线求解器，不修改保存解。 | 设置修改与迭代仅在明确确认后执行。 |

## Explore

| | |
|---|---|
| **Download** | [V2.10.5 Windows x64](AutoFluent_V2.10.5_Windows_x64.zip) |
| **Documentation** | [Quick start](docs/GETTING_STARTED.md) · [Validation &amp; changelog](docs/V2.10.5_专项修改与验证.md) |
| **Source** | [Go desktop](cmd/autofluent/) · [Python analysis worker](worker/) |
| **Examples** | [Configuration templates](templates/) · [Report samples](examples/) |
| **Related project** | [AutoMesh — mesh quality workstation](https://github.com/1463503175-pixel/AutoMesh) |

<details>
<summary><strong>Architecture</strong></summary>

```text
AutoFluent / Windows native UI (Go)
      ├── Offline analysis
      │     ├── Case + Data pairing / geometry precheck
      │     ├── Native H5 fields + SV_FLUX
      │     ├── Ports / wafer channels / baffles / pressure
      │     └── JSON + CSV + standalone HTML
      └── Connected tasks
            └── Existing Workbench / Fluent session
```

当前仓库公开的是 V2.10.5 版本；真实 FFF 大模型的完整晶圆、挡板与内存测试仍以 [专项验收记录](docs/V2.10.5_专项修改与验证.md) 为准。

</details>
