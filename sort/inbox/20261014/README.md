# 上传排序文件（无需 ZIP）

这是供作者上传本次排序的 GitHub 收件目录。请选择 **Add file → Upload files**，把下列四份原始文件一次拖入：

- `modlist(2).txt`（也接受 `modlist.txt`）
- `plugins.txt`
- `loadorder.txt`
- `lockedorder.txt`

一次拖入后点击 **Commit changes**。GitHub Actions 将自动检查整套文件，按原始内容归档到 `sort/releases/`，计算每个文件的长度与 SHA256，最后更新 `sort/latest.json`。

不需要上传 ZIP，也不需要手动修改 JSON。文件不齐全时不会推送任何排序更新。

本目录以 2026-10-14 为批次标识。若本日后再次调整排序，请建立一个新的日期批次，或升级发布脚本中的版本号；已公开的原始排序文件禁止被覆盖。
