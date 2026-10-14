# 星黎 MO2 小助手 v1.4.0-user

## LOD 中配·着色器兼容护栏

- 在 xLODGen / DynDOLOD 启动前自动备份并写入可回滚的生成设置。
- 默认预设：`星黎中配·着色器兼容`，面向整合包大量中等配置用户，不使用极限 High 参数。
- 可选 `性能优先` 预设，进一步降低远处地形纹理与草地 LOD 密度。
- 自动检测 Community Shaders；也可手动指定 Community Shaders / ENB / 原版。
- Community Shaders 下自动写入 `ComplexGrassBillboard=5`、`ComplexGrassBacklightMask=100`。
- xLODGen 默认保持 Gamma 1.00；不把 0.6 当成所有着色器/天气/地表组合的固定值。
- xLODGen 使用 BC7 Quick、Bake normal maps，仅 LOD4 diffuse 开 mipmap。
- DynDOLOD 中配护栏使用 `GrassDensity=50`、`MaxTextureSize=8192`；性能优先使用 `GrassDensity=33`。
- 提供【恢复生成工具原设置】按钮。
- 不模拟鼠标点击 DynDOLOD 的 Low/Medium/High 按钮；动态 mesh/reference rules 继续由当前 DynDOLOD 版本按实际 load order 生成，避免静态规则过期。

## 兼容性

- 目标：MO2 2.4.4 / Python 3.8 / PyQt5。
- 不注册 `pluginList().onPluginStateChanged()`。
- 新手模式仍完全禁止模组压缩和 LOD 自动化。
