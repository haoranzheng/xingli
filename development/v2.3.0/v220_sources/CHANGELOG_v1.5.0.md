# 星黎 MO2 小助手 v1.5.0-user

## 内置 Grass Precacher

- 将 GrassPrecacher v3.2.0 的核心生成循环直接集成到星黎 LOD 流水线。
- 草缓存阶段不再需要配置 EXE、参数、工作目录、日志目录或输出源目录。
- 自动检测 `SKSE/Plugins/*NGIO-NG.dll`，并兼容旧版 `NetScriptFramework/Plugins/*GrassControl.dll`。
- 自动定位当前游戏目录与 SKSE 启动器（SkyrimSE / EnderalSE 使用 `skse64_loader.exe`，SkyrimVR 使用 `sksevr_loader.exe`）。
- 自动创建 `PrecacheGrass.txt`，通过 MO2 VFS 启动 SKSE，并在游戏退出/崩溃后自动检查标记文件。
- 标记仍存在时自动等待 8 秒并重启；标记消失后判定 Grass Precaching 结束。
- 增加 240 次重启 / 8 小时安全上限，避免异常状态无限循环。
- 增加“停止草缓存自动循环”按钮：删除 `PrecacheGrass.txt` 后停止后续自动重启。
- 生成前自动清理 `MO2 Overwrite/Grass` 中旧 `.cgid/.gid`，避免旧缓存混入新结果。
- 完成后自动从 MO2 Overwrite 收集 `.cgid/.gid`，同步到配置的 Grass Cache Output MOD，并刷新/启用该 MOD。
- 草缓存阶段仍保留正常/失败调试 MOD 启停规则，但不再暴露生成器路径配置。

## 兼容性

- 目标：MO2 2.4.4 / Python 3.8 / PyQt5。
- 不注册 `pluginList()` / `onPluginStateChanged`。
- 整个插件仍只有主控制器一个 `createPlugin()` 入口。

## 来源说明

Grass Precaching 循环参考用户提供的 GrassPrecacher v3.2.0，原作者标记为 AL、leostevano、GaRRuSS。集成时保留其核心完成判定：`PrecacheGrass.txt` 存在则继续重启，不存在则认为预缓存结束；星黎额外增加输出验证、流水线衔接和安全上限。
