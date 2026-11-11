# 星黎 MO2 小助手 v1.7.1-user

## Grass Cache 非阻塞热修复

- 移除 Python `QThread` 中对 `IOrganizer.waitForApplication()` 的调用。
- Grass Cache 改为主线程 `QTimer` + Windows 非阻塞进程检测，不锁死 MO2 UI。
- 启动 SKSE 前记录既有 Skyrim PID，启动后跟踪新出现的 `SkyrimSE.exe` / `SkyrimVR.exe`。
- 不把短生命周期的 SKSE loader 退出误判为 Skyrim 已结束。
- Skyrim 真正退出/闪退后检查 `PrecacheGrass.txt`：存在则 8 秒后自动续跑；消失则进入完成校验。
- 保留 v1.7.0 Grass Cache checkpoint 格式，可继续已有部分 `.cgid/.gid`。
- xLODGen / TexGen / DynDOLOD 也改为非阻塞句柄轮询，避免相同 UI 卡死问题。
- 插件关闭时只释放监控句柄，不结束外部程序；Grass 任务写入 `interrupted` 断点。
