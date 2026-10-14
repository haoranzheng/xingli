# 星黎 MO2 小助手 v1.8.1-user

## MO2 2.4.4 MOD 启用状态热修复

- 修复“模组管理中心所有 MOD 都显示为禁用”。
- 根因：v1.8.0 错误地从 `mobase.IModList.STATE_ACTIVE` 读取 Python 枚举；MO2 Python 绑定实际通过 `mobase.ModState.ACTIVE`（旧绑定可能为 `active`）暴露状态位。
- 新增兼容状态判定：优先 Python ModState 枚举，兼容大小写差异，最后使用 MO2 官方位值作为兜底。
- 同步修复 LOD 自动化的 MOD 当前启用状态判定，保证“运行前启用/禁用”只操作真正需要变化的 MOD。
- 修复自动分辨率 MOD 的旧式等值判断与不存在的 `IModList.setState()` 调用，统一使用 `setActive()`。
- 不新增 `pluginList()`、`onPluginStateChanged()` 或 `onModStateChanged()` 回调。
