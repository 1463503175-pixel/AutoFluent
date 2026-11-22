# AutoFluent 2.1 快速开始

## 安装和运行

下载 [AutoFluent.exe](https://github.com/1463503175-pixel/AutoFluent/releases/download/2.1/AutoFluent.exe)，保存到本地可写目录并双击运行。本次发布为一体化 Windows x64 程序。

## 保存解分析

选择 Fluent 保存的 .cas.h5 或 .dat.h5，核对配对文件和几何预检，再执行分析。离线分析不需要连接 Workbench。检查每项结果的实际核验状态。

## 结果阅读

- 片间与旁路：按实际层、排及通道位置查看流量、占比与 CV。
- 挡板与压力：根据可取得的孔、端口和测压数据判断异常位置。
- 物种与表面反应：保存快照不能直接作为完整剂量或饱和成膜证据。
- 方案比较：先核对工况、截面与分区的可比性。
- 在线设置与求解：仅在明确确认后执行。

## 文件来源

[分析引擎](../worker/) · [配置和报告模板](../templates/) · [恢复清单](release_inventory.json) · [SHA-256](../SHA256SUMS.txt)

本仓库只有当前 Release 2.1 的集成程序及可恢复项目源码。EXE 不包含完整可恢复的 Go 前端源码与构建定义。静态核验不代表真实 H5 或 Windows 实机功能已经验收。
