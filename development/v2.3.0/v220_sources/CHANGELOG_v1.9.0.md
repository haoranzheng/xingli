# 星黎 MO2 小助手 v1.9.0-user

## MOD 组
- 新增持久化 MOD 组系统 `mod_groups.py`。
- 组支持说明、直接 MOD 成员、嵌套子组。
- 保存时阻止循环引用；删除组不会静默修改其它组，引用会显示为缺失。
- 模组管理中心新增“MOD 组”页，可创建、编辑、删除、事务启用/禁用整组。
- LOD Grass/xLODGen/TexGen/DynDOLOD 的正常/失败重试启停规则均可直接引用 MOD 组。

## 状态事务 / 回滚
- 新增 `mod_transaction.py`。
- 启用/禁用前生成实际变更预览。
- 执行前持久化 pending journal；Python/MO2 会话异常后，下次打开模组管理中心可恢复事务前状态。
- 执行失败时自动尝试回滚启用/禁用状态。
- 保存最近一次成功状态事务，支持“撤销上次状态操作”。
- 事务仅覆盖可逆的启用/禁用；物理卸载仍明确标记为不可自动恢复。

## MO2 2.4.4 兼容
- 延续统一 `ModStateService`。
- 单 MOD 仍强制使用 `setActive(QString, bool)` 标量路径。
- 不使用 `pluginList()`、`onPluginStateChanged()`、`onModStateChanged()` 或 `waitForApplication()`。
