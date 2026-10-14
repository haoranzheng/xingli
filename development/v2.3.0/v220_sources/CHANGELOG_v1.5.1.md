# 星黎 MO2 小助手 v1.5.1-user

## Grass Cache hotfix

- 修复 v1.5.0 能进入 Skyrim 但 NGIO/GrassControl 未实际进入草缓存流程的问题。
- 草缓存启动方式回归 GrassPrecacher v3.2.0 的 MO2 2.4.4 已知可工作语义：
  `organizer.startApplication(sksePath, ["-forcesteamloader"])`。
- 启动前使用 `Path.touch()` 创建游戏根目录 `PrecacheGrass.txt`。
- 启动前强制确认标记文件真实存在；不存在则禁止启动普通游戏。
- 日志输出标记文件绝对路径，便于排查 NGIO/GrassControl 是否读取到触发文件。
- 保留自动崩溃重启、输出验证和后续 xLODGen/TexGen/DynDOLOD 流水线。
