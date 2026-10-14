# 星黎 MOD 组与状态事务（v1.9.0）

## MOD 组

数据默认保存在 MO2 pluginDataPath 下：

`xingli_assistant/mod_groups.json`

组使用 MO2 **内部 MOD 名称**。组可直接包含 MOD，也可包含其它组；保存时会阻止循环依赖。删除一个组不会自动修改其它组，引用会保留并显示为“缺失组”，便于作者发现整合包版本漂移。

LOD 流水线可分别配置：
- `EnableGroups`
- `DisableGroups`
- `RetryEnableGroups`
- `RetryDisableGroups`

直接 MOD 规则与 MOD 组会合并、去重；冲突时禁用优先。

## 状态事务

事务仅用于可逆的 MOD 启用/禁用状态，不承诺回滚物理卸载。执行前会保存 `pending_state_transaction.json`，成功后生成最近一次事务记录，可通过“撤销上次状态操作”恢复。

如果 MO2 或插件在状态操作过程中异常退出，下次打开模组管理中心会检测 pending journal，并询问是否恢复到事务开始前状态。

事务目录：

`xingli_assistant/transactions/`

## MO2 2.4.4 兼容约束

- 单 MOD 必须走 `setActive(QString, bool)` 标量重载。
- 真正多 MOD 操作才使用列表重载。
- 不使用 `pluginList()` / `onPluginStateChanged()` / `onModStateChanged()`。
- MO2 API 只在 GUI 线程调用。
