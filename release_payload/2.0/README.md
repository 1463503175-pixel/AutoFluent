# Release 2.0 — 静态拆包核验

原始 EXE 未执行、未修改。SHA-256: d3b374cf79413c248d7d1ff0178cda4ef8b2d0fd8e273cd08b6e41b3f589aed1

状态：worker_source_synced_go_not_recovered

识别归档：zip@3522368, zip@3522368/zip@0
可恢复项目文件：56

Worker 主目录同步：已同步

尚未证明完整 Go 及测试源码与此 EXE 一致；未恢复部分保留为历史基线。

完整文件与缺失项见 [inventory.json](inventory.json)，原样恢复内容见 [files/](files/)（如有）。

没有内嵌的 Go 源文件或仅有编译字节码时，需要原始构建源码才能完整同步。
