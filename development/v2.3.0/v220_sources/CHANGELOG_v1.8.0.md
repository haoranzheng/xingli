# 星黎 MO2 小助手 v1.8.0-user

## 新增：星黎模组管理中心（高级玩家）

- 实时读取当前 MO2 Profile 的 MOD 列表。
- 搜索并筛选：全部 / 已启用 / 已禁用 / Essential。
- 批量启用、批量禁用，优先使用 `IModList.setActive(QStringList, bool)`，减少重复 VFS 重算。
- 批量卸载：单次危险操作确认，自动排除 Essential/受保护 MOD，然后通过 `IModList.removeMod()` 逐项卸载。
- 批量安装：可多选 `.7z/.zip/.rar` 或扫描目录，将归档队列逐项交给 MO2 原生 `IOrganizer.installMod()`。
- FOMOD/安装选项仍由 MO2 原生安装界面负责，不绕过安装器。

## LOD 自动化联动

Grass Cache / xLODGen / TexGen / DynDOLOD 每个阶段的以下四个规则：

- 正常：运行前启用 MOD
- 正常：运行前禁用 MOD
- 失败重试：启用 MOD
- 失败重试：禁用 MOD

现在都可以点击“从模组管理器选择…”直接从 MO2 实时列表批量勾选。
仍保留手动分号输入兼容性。

## MO2 2.4.4 安全边界

- 不使用 `pluginList()`。
- 不注册 `onPluginStateChanged` / `onModStateChanged` map/QFlags 回调。
- 所有 IOrganizer / IModList 写操作仍在 GUI 主线程执行。
- 不打包 `.pyc` / `__pycache__`。
