# v1.5.0：草缓存生成已内置

从 v1.5.0 起，Grass Cache 阶段不再需要配置任何外部 EXE。星黎直接内置 GrassPrecacher 的核心流程：

1. 自动检查 NGIO-NG / GrassControl 是否在当前 MO2 VFS 中可见；
2. 自动创建游戏目录下的 `PrecacheGrass.txt`；
3. 自动通过 MO2 启动 SKSE；
4. Skyrim 退出/崩溃后检查 `PrecacheGrass.txt`；
5. 文件仍存在则 8 秒后自动重启；
6. 文件消失后检查 MO2 Overwrite 中是否已经生成 `.cgid/.gid`；
7. 自动同步到 `Grass Cache Output` MOD，恢复临时 MOD 状态，然后继续 xLODGen。

Grass Precaching 允许游戏在生成过程中多次退出，这是该生成流程的正常工作方式。星黎设置了 240 次重启和 8 小时安全上限，同时提供“停止草缓存自动循环”按钮。

生成前会清理 `Overwrite/Grass` 中旧 `.cgid/.gid`。如果你的草缓存不写入 MO2 Overwrite，而是被其它外部工具重定向到自定义目录，则这一版内置模式不适用，需要先调整 NGIO/GrassControl 的输出行为回到 MO2 VFS/Overwrite。

---

# 星黎 LOD 自动化流水线 v1.3.0

目标环境：Mod Organizer 2.4.4 / Python 3.8 / PyQt5 / Skyrim Special Edition。

## 它自动处理什么

流水线顺序：

1. Grass Cache 草地缓存
2. xLODGen 地形 LOD
3. TexGen 纹理、树木/草地 billboard
4. DynDOLOD 对象、树木、草地远景

星黎工具负责：

- 检查当前环境相对上次成功生成是否发生变化。
- 自动选择“建议重新生成”的阶段。
- 在每个阶段启动前自动启用/禁用指定 MO2 左侧 MOD。
- 工具退出后自动检查退出码、日志成功标记、输出目录。
- 第一次失败时，可切换到另一套“调试 MOD”启停规则并自动重试一次。
- 任一阶段失败后立即停止，不继续生成后续产物。
- 可把外部生成目录自动同步到 MO2 的指定输出 MOD。
- 成功后保存当前环境基线，下次只推荐必要阶段。

## 第一次设置

进入：星黎 MO2 小助手 -> 功能模式“高级玩家” -> “LOD自动化（高级）” -> “流水线设置”。

每个阶段至少设置“工具 EXE”。如果希望自动接管生成结果，还要设置“生成输出源目录”和“同步到 MO2 MOD”。

### Grass Cache

- 工具 EXE：填写你当前实际用于生成草地缓存的启动程序。常见情况是通过 SKSE/Grass Cache 方案启动游戏完成缓存，而不是单独的 LOD EXE。
- 输出源目录：可以填写实际生成 `*.CGID` 的 Grass 文件夹，也可以填写包含 Grass 子目录的 Overwrite 路径。
- 输出 MOD：建议 `Grass Cache Output`。
- 正常启用/禁用 MOD：填写你现在每次手动切换的草地缓存辅助 MOD。
- 失败重试启用/禁用 MOD：填写只有报错时你才手动开启/关闭的调试 MOD。

### xLODGen

- 工具 EXE：你当前使用的 SSE xLODGen / SSELODGen x64 程序。
- 参数：通常包含 `-sse`；如果你已经通过其它方式固定游戏模式，可按你的现有配置填写。
- 输出源目录：必须是专门的外部 xLODGen 输出目录。
- 输出 MOD：建议 `xLODGen Output`。
- 如果你使用 `SSE Terrain Tamriel` / xLODGen Resource 一类“只在地形生成时使用”的资源 MOD，可放进本阶段“正常：运行前启用 MOD”，流水线结束会恢复原状态。

### TexGen

- 工具 EXE：`TexGenx64.exe`。
- 参数：通常 `-sse`，可以继续追加你现有参数。
- 输出源目录：专门的外部 TexGen 输出目录。
- 输出 MOD：建议 `TexGen Output`。
- 默认成功标记：`TexGen completed successfully`。

### DynDOLOD

- 工具 EXE：`DynDOLODx64.exe`。
- 参数：通常 `-sse`。
- 输出源目录：专门的外部 DynDOLOD 输出目录。
- 输出 MOD：建议 `DynDOLOD Output`。
- 默认成功标记：`DynDOLOD completed successfully`。

## 两种变化检测策略

### 智能重建（推荐）

- MOD 启用集合 / plugins.txt / loadorder.txt 变化：完整重建。
- 草地模型/配置相关目录变化：Grass Cache + TexGen + DynDOLOD。
- 地形相关资源变化：xLODGen + TexGen + DynDOLOD。
- 普通材质或远景模型变化：TexGen + DynDOLOD。
- 单纯草地贴图变化不会因为“贴图”这个原因强制重建 Grass Cache。

### 检测到变化就完整重建

只要检测到相关环境变化，固定跑四个阶段。适合发布整合包前做最终生成。

## 为什么不是完全无人值守点击 TexGen / DynDOLOD

TexGen 和 DynDOLOD 本身是有状态的 GUI 生成器，选项、世界空间、错误提示和预设可能随版本及整合变化。v1.3.0 不使用脆弱的鼠标坐标/窗口模拟点击。

因此第一次请先在 TexGen/DynDOLOD 内保存好你的常用预设。之后点击“运行推荐阶段”时，星黎流水线会启动工具并等待；你在工具内确认生成并正常退出后，它会自动验证结果、同步输出、切换 MOD 并启动下一阶段。

这比直接模拟鼠标点击更稳定，也能在出现真实错误时停下来，而不是把错误状态继续传递到下一步。

## 重要安全规则

- 不注册 `pluginList().onPluginStateChanged()`，避免 MO2 2.4.4 的 Python/Boost.Python PluginState 回调兼容问题。
- 只使用左侧 `modList().setActive()` 自动启停辅助 MOD。
- 外部程序通过当前 MO2 profile 的 VFS 启动，不显式指定另一个 profile。
- 新手玩家模式无法看到或调用此功能。
- 自动输出覆盖会清空“同步到 MO2 MOD”的旧目录内容，因此不要把人工文件混放进 Grass Cache/xLODGen/TexGen/DynDOLOD 输出 MOD。
- 智能检测使用快速环境指纹，不会逐字节 Hash 数百万个材质文件。发布正式版本前可以使用“强制完整重建”。

## v1.4.0：中配·着色器兼容画质护栏

流水线顶部新增【生成前自动应用星黎画质护栏】。默认预设用于整合包发行，不追求 DynDOLOD High/极限 3D LOD。

### 星黎中配·着色器兼容（默认）

xLODGen 自动写入：

- LOD4/8/16/32 mesh Quality：4 / 7 / 8 / 10。
- Terrain diffuse/normal：512 / 512 / 512 / 512。
- Format：BC7 Quick。
- Protect Borders：开启。
- Bake normal maps：开启。
- Mipmap：只给 LOD4 diffuse 开启；其它 terrain diffuse 和全部 normal 不开。
- Gamma：1.00；Brightness/Contrast：0。
- Default texture size：16。

DynDOLOD 自动写入：

- MaxTextureSize=8192。
- GrassDensity=50。
- 自动检测到 Community Shaders 时：ComplexGrassBillboard=5、ComplexGrassBacklightMask=100。

### 性能优先

- xLODGen mesh Quality：5 / 8 / 8 / 10。
- Terrain texture：512 / 256 / 256 / 256。
- Default texture size：4。
- DynDOLOD GrassDensity=33。
- 其余保持相同的着色器兼容规则。

### 为什么不强制 xLODGen Gamma=0.6

着色器/PBR 环境的地形 LOD颜色需要看实际材质、noise.dds、天气和 shader 配置。星黎默认使用中性 Gamma 1.00，避免整合包在不同机器上普遍偏暗。只有经过作者实机对比确认需要压暗时，才应做项目级 Gamma 微调。

### DynDOLOD Medium 规则

星黎自动修改的是可稳定审计的 INI 标量设置。DynDOLOD 的 Low/Medium/High 按钮会按照**当前插件 load order**动态重建 mesh/reference rules，因此 v1.4.0 不把某个版本生成的静态 Medium 规则硬写进其它 DynDOLOD 版本。整合包作者应以官方 Medium 作为基础规则；星黎负责在每次生成前重新施加 8192 texture/atlas cap、草地密度和 shader-specific INI 护栏。

所有被修改的工具设置在写入前自动备份；需要撤销时点击【恢复生成工具原设置】。


## v1.6.0 远景草地整合包预设

Grass Cache 阶段现在会通过 MO2 VFS 查找实际生效的 `SKSE/Plugins/GrassControl.ini`，
并复制为 `Overwrite/SKSE/Plugins/GrassControl.ini` 后再修改，因此不会直接改写下载的 NGIO MOD。

默认“星黎中配·着色器兼容”：

- `Use-grass-cache=true`
- `Only-load-from-cache=true`
- `DynDOLOD-Grass-Mode=1`
- `Super-dense-grass=false`
- `Extend-grass-distance=false`
- `Extend-grass-count=true`
- `Ensure-max-grass-types-setting=15`
- `Overwrite-min-grass-size=60`
- DynDOLOD `GrassDensity=50`

“性能优先”将 `Overwrite-min-grass-size` 提高为 70，并将 DynDOLOD `GrassDensity` 降低到 33%。

注意：`Overwrite-min-grass-size` 和 `Ensure-max-grass-types-setting` 会改变草缓存中的草量/位置，
因此更换这些数值后必须重新生成 Grass Cache 和 Grass LOD。距离参数本身通常不要求重建草缓存。

对于 Community Shaders，自动设置 `ComplexGrassBillboard=5`、`ComplexGrassBacklightMask=100`。
TexGen HD Grass billboard 的社区/官方当前建议为
Direct 0 / Ambient 100 / Smoothness 0；GUI 数值仍由 TexGen 自己的 preset 保存，插件不做鼠标坐标模拟。


## v1.7.0 草缓存持久化断点续跑

Grass Cache 阶段现在保存 `grass_checkpoint.json`。只要 `PrecacheGrass.txt` 和环境指纹仍有效，Skyrim 崩溃、MO2 退出或 Windows 重启后都可以继续已有 `.cgid/.gid`。

- 【暂停草缓存（保留断点）】：停止自动重启，但保留触发标记、缓存和临时 MOD 状态。
- 【继续草缓存断点】：验证环境指纹一致后继续。
- 【放弃草缓存断点】：恢复临时 MOD 状态，删除触发标记和部分缓存。
- 连续 4 次没有新增缓存时自动进入错误断点，避免无限正常进入游戏。
- 如果环境、GrassControl.ini、加载顺序或关键生成配置改变，旧断点会自动失效并从头生成。


## v1.7.1 非阻塞进程监控

草缓存不再调用 `waitForApplication()`。星黎通过 Windows 进程句柄与进程快照定时检测实际 Skyrim 会话，MO2 界面在刷草缓存期间保持可操作。SKSE loader 可以先退出；只有实际 `SkyrimSE.exe`/`SkyrimVR.exe` 退出后，才检查 `PrecacheGrass.txt` 并决定完成或 8 秒后继续。


## v1.7.2 启动前非阻塞文件处理

Grass Cache 启动前对旧 `.cgid/.gid` 的清理，以及 Skyrim 退出后的缓存统计，均改为纯文件系统后台任务。MO2 API 不会在线程中调用。环境指纹复用已完成的环境分析结果，避免在点击“运行推荐阶段”后再次同步扫描大型整合包。

## v1.8.0：模组管理中心联动

LOD 设置页的阶段 MOD 规则不再需要纯手工输入。点击“从模组管理器选择…”即可读取当前 MO2 左侧 MOD 列表并批量选择启用/禁用规则。保存后使用 MOD 内部名称，以保证 `IModList.setActive()` 能准确定位。


## v1.9.0：MOD 组联动

LOD 四阶段现在支持直接引用“星黎模组管理中心 → MOD 组”定义：`EnableGroups`、`DisableGroups`、`RetryEnableGroups`、`RetryDisableGroups`。组可嵌套，运行时递归展开并去重；同一 MOD 同时出现在启用和禁用侧时以禁用为准。删除/缺失组会在运行日志中报警，设置页也会阻止保存不存在的组名。
