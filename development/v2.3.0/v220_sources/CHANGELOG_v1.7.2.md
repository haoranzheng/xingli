# 星黎 MO2 小助手 v1.7.2-user

## Grass Cache 启动前卡死修复

- 修复点击“运行推荐阶段”后，在 Skyrim 尚未启动前因同步删除大量 `Overwrite/Grass/*.cgid` / `*.gid` 导致 MO2 UI 长时间假死的问题。
- 旧草缓存清理改为纯文件系统 QThread 后台任务；后台线程不调用任何 `mobase/IOrganizer` API。
- 草缓存环境指纹复用“重新分析”已经生成的快照，不再在启动热路径二次扫描整套 MOD。
- Skyrim 每次退出后的草缓存文件数量/大小统计改为后台任务，避免闪退后统计数万小文件时再次卡 UI。
- “放弃草缓存断点”的大规模文件清理也改为后台执行。
- 增加 Grass Cache 启动分段日志：阶段 MOD 规则 → 后台清理 → PrecacheGrass 标记/启动 SKSE。
- 保留 v1.7.1 的非阻塞 Skyrim 进程检测和持久化断点格式。

## 线程边界

- MO2 API 始终只在 UI 主线程调用。
- 后台线程只处理普通文件系统扫描/删除/统计，不访问 MO2 VFS/ModList。
- v1.7.0/v1.7.1 旧断点采用轻量兼容校验后自动升级为 `cached_snapshot_v2`，不需要为了继续断点重新扫描整套 MOD。
- MOD 状态切换只对实际需要改变状态的 MOD 调用 `setActive`，减少无意义的 MO2 VFS 重建。
