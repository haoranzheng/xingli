# v2.2.0 — Clean First-Run + PGPatcher / TruePBR Support

- 首次启动状态迁移到用户本机 LocalAppData，并按物理整合实例隔离；作者机器状态不会再被打包传播。
- 同一整合目录移动后保持同一实例；删除并重新解压到同一路径会被识别为新的首次安装。
- 首次欢迎只在实际显示完成后写入完成状态；损坏状态会安全回退为重新引导。
- 新增【PBR / PGPatcher】适配：自动发现 PGPatcher.exe，并通过当前 MO2 VFS 启动。
- 启动前检查 ModOrganizer.ini、旧 PGPatcher 输出和 DynDOLOD 输出；旧输出已启用时可由小助手安全禁用。
- 显示推荐 MO2 Instance / PGPatcher_Output 路径，并提示 TruePBR + Community Shaders 与 BodySlide → PGPatcher → TexGen → DynDOLOD 顺序。
- 对 Skyrim 1.5.97–1.6.659 明确提示 PGPatcher 官方要求 Backported Extended ESL Support (BEES)。
- 用户助手自检与内置教程新增 PGPatcher / TruePBR 检查和说明。
