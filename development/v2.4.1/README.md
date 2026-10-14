# 星黎 v2.4.1 · MO2 排序备份更新

## 关键行为

本次更新**暂时屏蔽** v2.4.0 的「单独 MOD 更新」首页按钮与启动自动检查；原有代码与用户资料不删除。

新增「📑 排序备份更新」按钮，MO2 启动后检查独立的 `sort/latest.json`。只要发现当前 Profile 没有下载过的排序发布版本，就主动通知用户；不会要求先打开助手界面。

**特别注意：下载不是应用。** 插件只会在当前 MO2 Profile 目录内新建一个可由 MO2 原生【还原】选择的排序备份。它绝不会直接替换 `modlist.txt`、`plugins.txt`、`loadorder.txt` 或 `lockedorder.txt`。

## MO2 官方备份/还原规则

根据 [MO2 官方 mainwindow.cpp](https://github.com/ModOrganizer2/modorganizer/blob/master/src/mainwindow.cpp#L3823-L3939)：

- 左侧 MOD 列表：「备份」生成 `modlist.txt.YYYY_MM_DD_hh_mm_ss`；「还原」选择一个 `modlist.txt.*` 备份，将其复制覆盖当前 `modlist.txt` 后刷新。
- 右侧插件列表：「备份」同时生成 `plugins.txt.<suffix>`、`loadorder.txt.<suffix>`、`lockedorder.txt.<suffix>`；「还原」根据同一个后缀寻找 **三个** 文件，所以必须成套存在。
- 官方时间戳是 `yyyy_MM_dd_hh_mm_ss`。MO2 的 `queryRestore()` 除识别该固定日期，还会接受 `<原文件名>.<其他后缀>`，在还原菜单中显示原始后缀。
- MO2 自己的备份维护会清理最多 10 个符合日期 glob 的旧备份；星黎文件带 `_Xingli_001`，不匹配原生「纯日期后缀」清理 glob，避免下载的排序被 MO2 的常规备份操作自动清理。
- MO2 `Profile::refreshModStatus()` 从 `modlist.txt` 读取列表时**倒序**换算为显示优先级，因此星黎不重新排序 ZIP 内的原始行，直接保持提供者文件的逐字节内容。

下载文件名举例：

```
profiles/当前Profile/
  modlist.txt                                      # 当前排序，原封不动
  modlist.txt.2026_10_14_13_22_00                 # 原有 MO2 本地备份，原封不动
  modlist.txt.2026_10_14_13_22_00_Xingli_001      # 第一次下载
  modlist.txt.2026_10_14_13_22_00_Xingli_002      # 同秒第二次下载
```

右侧的三件套使用**完全相同** `2026_10_14_13_22_00_Xingli_001` 后缀。

## 作者如何推送（无需存储桶）

只需上传一个不大于 2 MiB 的 ZIP 到已有的 `xingli` GitHub 仓库，Gitee 镜像同步即可。

仓库文件：
- `sort/latest.json`：只包含排序更新元数据、版本、SHA256 和镜像 URL。
- `sort/releases/`：每次推送的真正 ZIP；当前默认清单为空，不推送假内容。

仓库提供 `development/v2.4.1/publish_sort.py`，可根据本机 Profile 中现有的排序文件自动封装并计算 SHA256。

**左侧 MOD 排序示例**

```powershell
python development/v2.4.1/publish_sort.py --kind mods --id wasteland-mod-order --version 1.0.0 --title "废土排序" --changelog "调整模组优先级" --modlist "D:\MO2\profiles\Default\modlist.txt" --dry-run
```

核对后去掉 `--dry-run`，脚本在本地工作副本创建 ZIP 和修改 `sort/latest.json`；随后提交这两个文件到 GitHub 的 `main`，并检查 Gitee 镜像同步。

**右侧插件排序示例**

```powershell
python development/v2.4.1/publish_sort.py --kind plugins --id wasteland-plugin-order --version 1.0.0 --title "插件顺序" --changelog "调整 ESP/ESM 载入顺序" --plugins "D:\MO2\profiles\Default\plugins.txt" --loadorder "D:\MO2\profiles\Default\loadorder.txt" --lockedorder "D:\MO2\profiles\Default\lockedorder.txt"
```

注意：某些 Profile 的 `lockedorder.txt` 可能为空；这是有效情况，仍然需要在 ZIP 中包含该文件。

## 与现有功能关系

- 星黎助手自身更新：`update/latest.json`（不变）
- 整合包版本通知：`modpack/latest.json`（不变）
- 独立 MOD 更新：`mods/latest.json`（暂不自动检查、不显示入口）
- **排序备份推送：`sort/latest.json`（新）**

排序下载采用 Gitee 优先 / GitHub Raw 备用；HTTP 响应上限、ZIP 文件成员检查、大小、CRC 和 SHA256 校验。仅导入到 MO2 API 提供的真实当前 Profile 目录；文件通过 `O_EXCL` 独占创建，发生名称冲突时加序号而不是覆盖现有备份。

## 实际人工验收

升级到 v2.4.1 后检查：单独 MOD 更新按钮消失；排序更新按钮出现；空远程清单不会弹窗或下载；作者发布排序 ZIP 后启动 MO2 会提示；点击下载创建备份而当前排序不变；从 MO2 原生【还原】菜单选择带 `Xingli` 后缀的项目，再由 MO2 执行还原。

即使备份可被 MO2 识别，如果玩家本地缺少排序文件所列 MOD 或其游戏插件，MO2 还原后的实际效果仍可能不一致。作者应只向对应整合包版本发布匹配的排序文件，用户应人工确认还原内容。

远程 CI 不执行真实 MO2 GUI 操作；该交互仍需人工验收。
