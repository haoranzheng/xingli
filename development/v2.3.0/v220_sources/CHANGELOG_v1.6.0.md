# 星黎 MO2 小助手 v1.6.0-user

- 新增 GrassControl.ini VFS 检测与 MO2 Overwrite 安全覆盖。
- 草缓存启动前强制检查/修复 Use-grass-cache=true、Only-load-from-cache=true。
- 新增中配远景草地预设：Mode 1、iMinGrassSize 60、MaxGrassTypes 15、DynDOLOD GrassDensity 50%。
- 性能预设：iMinGrassSize 70、DynDOLOD GrassDensity 33%。
- Community Shaders 自动写入 ComplexGrassBillboard=5、ComplexGrassBacklightMask=100；不强制 ForceComplexGrass。
- 新增“检查 GrassControl.ini”按钮，并检测 Skip/Only 世界空间列表交集。
- GrassControl 覆盖和 LOD 工具配置纳入统一备份/恢复。
- 保留单一 createPlugin()，不注册 IPluginList 状态回调。
