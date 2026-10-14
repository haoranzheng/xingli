# coding=utf-8
"""Tutorial content for Xingli Assistant.

Product-specific instructions are built into the plugin so they stay usable even if
external videos age or links become unavailable. External tutorials are kept only as
optional references.
"""

TUTORIAL_CATEGORIES = {
    "新手必读": [
        {
            "name": "第一次使用：我只想进入游戏",
            "content": (
                "日常游玩不需要先学习 MO2。\n\n"
                "1. 关闭正在打开的 MO2。\n"
                "2. 双击桌面的【星黎整合启动器.bat】。\n"
                "3. 启动器会通过当前整合的 MO2 配置启动 SKSE。\n\n"
                "不要为了进入游戏去修改 MO2 左侧 MOD 顺序、右侧插件顺序或可执行程序。"
            ),
        },
        {
            "name": "小助手在哪里？怎么打开？",
            "content": (
                "你有两种入口：\n\n"
                "推荐新手：双击桌面的【星黎帮助与教程.bat】。它会打开 MO2，并自动弹出教程中心。\n\n"
                "在 MO2 内：打开工具/插件入口，选择【星黎MO2小助手】。\n\n"
                "星黎小助手用于教程、更新、自检、整合包检查和维护；"
                "日常只玩游戏仍然使用【星黎整合启动器.bat】。"
            ),
        },
        {
            "name": "助手更新和整合包更新有什么区别？",
            "content": (
                "【助手更新】只更新星黎 MO2 小助手本身。下载后会校验文件，确认安装时才正常关闭空闲 MO2。\n\n"
                "【整合包更新】检查星黎整合内容版本。发现新版本后只会提示并打开作者提供的网盘页面，"
                "不会自动删除、安装或替换你的 MOD。\n\n"
                "主页会分别显示两种更新状态，不要把两者混在一起。"
            ),
        },
        {
            "name": "整合包检查与一键修复",
            "content": (
                "【整合包检查】会把当前 Profile 与你建立的基准比较。\n\n"
                "v2.1 起可安全修复：MOD 启停、满足安全条件时的左侧顺序，以及已记录的配置文件。\n"
                "缺失 MOD、新增 MOD、安装/卸载和存档不会自动处理。\n\n"
                "修复前会预览；失败会回滚。第一次使用 v2.1 基准时建议先点击【更新基准】。"
            ),
        },
    ],
    "日常使用": [
        {
            "name": "启动失败时先做什么？",
            "content": (
                "1. 打开【星黎用户助手】。\n"
                "2. 点击【重新自检】。\n"
                "3. 如果 SKSE 路径错误，可使用【修复 SKSE 路径】。\n"
                "4. 仍有问题时复制诊断报告给维护人员。\n\n"
                "不要先通过随机启停 MOD 的方式排错。"
            ),
        },
        {
            "name": "ENB 应该怎么管理？",
            "content": (
                "普通玩家建议只通过星黎的【ENB 管理】入口启用、更换或禁用 ENB。\n\n"
                "不要直接在游戏根目录里随意删除 ENB 文件，避免留下不完整组件。"
            ),
        },
        {
            "name": "哪些操作新手不要随意做？",
            "content": (
                "不熟悉整合结构时，请避免：\n"
                "• 随意更新核心框架；\n"
                "• 改动 MO2 左侧 MOD 优先级；\n"
                "• 改动右侧插件加载顺序；\n"
                "• 删除 Overwrite 中不认识的生成结果；\n"
                "• 手动替换 LOD/Grass 输出。\n\n"
                "先使用小助手的检查、事务和自动化入口。"
            ),
        },
    ],
    "高级维护": [
        {
            "name": "PBR / PGPatcher：什么时候必须运行？",
            "content": (
                "TruePBR 材质需要 Community Shaders，并需要 PGPatcher 根据当前 MO2 的纹理、模型和插件栈生成适配结果。\n"
                "Skyrim 1.5.97–1.6.659 按 PGPatcher 官方要求还需要安装 Backported Extended ESL Support (BEES)。\n\n"
                "推荐从星黎小助手的【PBR / PGPatcher】入口启动，确保程序运行在当前 MO2 VFS 下。\n"
                "重新生成前必须禁用旧的 PGPatcher_Output；生成完成后再启用它。\n\n"
                "推荐顺序：BodySlide → PGPatcher → TexGen → DynDOLOD。\n"
                "更换大型 PBR / Parallax / Mesh 资源后，应重新运行 PGPatcher，并重新生成后续 TexGen / DynDOLOD 输出。"
            ),
        },
        {
            "name": "Grass / LOD 自动化的基本规则",
            "content": (
                "流水线顺序：Grass Cache → xLODGen → TexGen → DynDOLOD。\n\n"
                "只有真正开始生成时才会应用阶段 MOD 启停规则；打开页面、检查变化或查看设置不会修改 MOD 状态。\n"
                "阶段结束与整条流水线结束都会恢复运行前状态。Grass 断点暂停时会暂时保留必要状态，"
                "继续完成或清除断点后再恢复。"
            ),
        },
        {
            "name": "模组管理与安全事务",
            "content": (
                "星黎的批量启停使用预览、快照、提交和回滚。\n"
                "受保护 MOD 不应被普通批量操作关闭。\n\n"
                "物理卸载不是启停事务的一部分，执行前仍需单独确认。"
            ),
        },
    ],
    "外部参考（可能与当前版本不同）": [
        {"name": "MO 基础操作（外部视频）", "url": "https://www.bilibili.com/video/BV1WZfZYxENU"},
        {"name": "星黎整合从入门到入土（外部视频）", "url": "https://www.bilibili.com/video/BV1KUU2YqEAA"},
        {"name": "从零开始打 MOD（外部视频）", "url": "https://www.bilibili.com/video/BV1zY411J7zw"},
        {"name": "OAR 使用与设置（外部视频）", "url": "https://www.bilibili.com/video/BV1Bt421J7iK"},
    ],
}
