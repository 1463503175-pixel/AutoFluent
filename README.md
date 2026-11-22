<div align="center">

<img src="assets/AutoFluent_icon_512.png" width="100" alt="AutoFluent icon" />

# AutoFluent

**ALD simulation analysis workstation**

面向 ALD 反应腔的 Fluent 原生数据分析与结构优化工作台。

[**Download Windows EXE**](https://github.com/1463503175-pixel/AutoFluent/releases/download/2.0/AutoFluent.exe) · [快速开始](docs/GETTING_STARTED.md) · [分析引擎](worker/) · [报告模板](templates/report_v11/)

**Release 2.0 · 2026-11-22 · Windows x64**

</div>

---

## 从保存解到结构优化

AutoFluent 读取 Fluent 的 Case/Data（.cas.h5 / .dat.h5），保留原生通量与物理分区的可追溯性，通过统一分析及报告辅助判断结构优化方向。离线分析无需连接 Workbench。

| 分析模块 | 内容 |
|---|---|
| 原生 H5 分析 | 文件配对、网格与原生面通量、入口出口、质量守恒 |
| 晶圆与挡板 | 普通及交错排几何、逐通道流量、旁路、上下挡板、均匀性 CV |
| 流场诊断 | 空间异常定位、可用压力统计与压降、方案比较 |
| 物种与表面反应 | 保存解的物种浓度、暴露及表面位点统计；快照不等于完整反应剂量 |
| V11 报告 | 结构化统计、独立 HTML 报告及可直接查看的模板 |
| 在线任务 | 已有 Fluent / Workbench 会话的受控工况与求解操作 |

## 快速开始

1. 下载 [Release 2.0 集成版](https://github.com/1463503175-pixel/AutoFluent/releases/download/2.0/AutoFluent.exe)，放入本地可写目录后运行。
2. 在保存解分析入口选择 Case/Data，核对配对及几何分区。
3. 查看有效物理指标、诊断与报告。缺失或无法核验的数据不可作为正式比较结论。

## Repository

| 目录 | 内容 |
|---|---|
| [app/](app/) | 本次上传的集成 EXE，原始字节保持不变 |
| [worker/](worker/) | 50 个从本次 EXE 原样提取的 Python 模块 |
| [templates/](templates/) | 3 份配置与报告模板 |
| [assets/](assets/) | 3 份图标及资源 |
| [docs/](docs/) | 当前发布使用说明及源码核验清单 |

## Release integrity

本仓库当前分支仅包含 Release 2.0 原始 EXE、从该 EXE 恢复的 56 个项目文件及本次新增的使用和核验说明。

[SHA-256](SHA256SUMS.txt) · [完整恢复清单](docs/release_inventory.json)

集成 EXE 未包含可恢复的完整 Go 前端源码及构建定义；当前源码可以查阅和维护分析引擎，但不能保证从零构建出与该 EXE 完全一致的桌面程序。静态拆包核验不代表 Windows 或 Fluent 实机功能验收。
