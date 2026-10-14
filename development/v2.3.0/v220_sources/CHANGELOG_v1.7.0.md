# 星黎 MO2 小助手 v1.7.0-user

- Grass Cache 新增持久化断点文件 `grass_checkpoint.json`。
- Skyrim 崩溃/退出且 `PrecacheGrass.txt` 仍存在时继续自动重启，不删除已生成 `.cgid/.gid`。
- MO2 关闭、插件窗口关闭或 Windows 重启后，可通过【继续草缓存断点】恢复同一任务。
- 断点保存环境指纹：启用 MOD、插件/loadorder、草地/地形/模型目录签名、GrassControl.ini、Skyrim.ini/SkyrimPrefs.ini 和流水线草地策略。
- 环境指纹不一致时自动作废旧断点并从头生成，避免混用失效草缓存。
- 【停止草缓存】改为【暂停草缓存（保留断点）】，不会删除 `PrecacheGrass.txt` 或部分缓存。
- 新增【放弃草缓存断点】：仅在明确放弃时删除标记与部分缓存并恢复临时 MOD 状态。
- 错误时进入 `paused_error`，保留断点；修复后可继续。
- 连续 4 次 Skyrim 运行无新增缓存会自动断点暂停，避免无限重启。
- 断点记录累计启动次数、缓存文件数量、输出大小和剩余 LOD 阶段。
- 保留 MO2 2.4.4 / Python 3.8 / PyQt5 兼容目标；无 IPluginList 状态回调。
