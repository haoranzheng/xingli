# 星黎 MO2 档案发布目录

本目录用于发布可供用户下载的 **MO2 Profile 档案文件夹**。档案更新与排序更新、整合包版本更新相互独立。

## 上传方式

每个档案在 `profiles/releases/` 下建立一个独立文件夹，目录名使用小写英文 ID，例如：

```text
profiles/
  releases/
    xingli-default/
      profile.json
      modlist.txt
      plugins.txt
      loadorder.txt
      lockedorder.txt
      archives.txt
      Skyrim.ini
      SkyrimPrefs.ini
```

将 MO2 的一个 Profile 文件夹中的实际内容复制到 `profiles/releases/xingli-default/`，再添加 `profile.json`。不要把外层 Profile 文件夹再嵌套一层。

## profile.json 格式

```json
{
  "id": "xingli-default",
  "name": "星黎默认档案",
  "version": "1.0.0",
  "description": "适用版本与档案内容说明",
  "changelog": "首次发布"
}
```

- `id` 必须与目录名完全一致，只能使用小写字母、数字、下划线和连字符。
- `name` 是用户在下载中心看到的名称。
- `version` 必须明确填写数字版本，例如 `1.0.0`，不使用日期作为版本依据。
- `description` 和 `changelog` 为可选说明。
- Profile 根目录必须有 `modlist.txt`。其他 MO2 档案文件会一并打包。

## 发布流程

1. 上传或更新 `profiles/releases/<id>/` 下的文件。
2. 新档案首次发布时填写 `1.0.0`。
3. 更新同一个档案时，必须提高 `profile.json` 中的版本号，例如 `1.0.1`；同一 ID + 版本的 ZIP 不允许被覆盖。
4. 提交到 GitHub `main` 后，Actions 会检查文件、生成 ZIP、计算 SHA256，并更新 `profiles/latest.json`。
5. 插件从 Gitee 国内镜像优先检查，GitHub 为备用源。

用户下载时，插件会把 ZIP 解压为 MO2 `profiles` 目录中的一个新文件夹。已有同名档案会自动避让，不会覆盖当前档案，也不会自动切换或启用 MOD。

## 安全与限制

- 不要上传 MOD 本体、游戏目录、Overwrite 或整个 MO2 实例；只上传 Profile 文件夹内的配置文件。
- 不接受符号链接、目录穿越路径、重复文件名或加密 ZIP。
- 单个文件最大 32 MiB，解压后总量最大 128 MiB，文件数最多 500。
- 发布 ZIP 最大 64 MiB。
- 不要上传账号信息、个人路径、私人存档或其他敏感数据。
