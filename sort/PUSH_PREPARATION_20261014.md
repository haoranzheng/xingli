# 星黎排序备份推送准备（2026-10-14）

状态：**仅准备，尚未公开推送**。正式 `main/sort/latest.json` 仍为空，不得提前让玩家收到失效下载通知。

用户提供四个真实 MO2 Profile 文件，已经校验 UTF-8 文本、无重复记录、右侧启用插件全部存在于 loadorder。

| 类型 | 需要上传的仓库路径 | ZIP 字节数 | SHA256 |
|---|---|---:|---|
| 左侧 MOD 排序 | `sort/releases/xingli-modlist-20261014-r01_v1.0.0_mods.zip` | 38519 | `f6c686fdc02b1a83b8302ef3b60d55d8e7e7f6323c11540d97984282ba8ef050` |
| 右侧插件排序 | `sort/releases/xingli-plugins-20261014-r01_v1.0.0_plugins.zip` | 37692 | `32dc189276c8d7a21461f4c67607cc86c2d9ac8198644cd7d6ba6f247263c3b1` |

这两份 ZIP 已生成在本次 ChatGPT 交付的 `xingli_sort_push_20261014.zip` 中，不包含任何游戏 MOD 本体。

发布顺序：先在 GitHub `main` 上传两个 ZIP → 下载与 SHA256 验证 → 确认 Gitee 镜像同步 → 最后发布本次交付中的 `sort/latest.json`。任何阶段不得覆盖玩家当前 `modlist.txt` 或 `plugins.txt`。最终发布后，v2.4.1 启动检查才会收到两条更新。
