# v1.9.2 — 国内镜像安全更新基础版

- 移除失效的 `gh.wobu.ip-ddns.com` 单点更新地址。
- 删除旧 `network.py` / `VersionCheckThread` 重复更新实现，只保留 `auto_updater.py`。
- 更新清单支持 Gitee → GitCode → GitHub 顺序故障转移。
- 更新包支持多个下载镜像，单个源失败自动切换。
- SHA256 变为自动安装的强制条件，并校验 ZIP 完整性、包根目录与版本号。
- 不再在 MO2 运行中覆盖插件文件；先 staging，再由外部更新助手等待 MO2 正常退出。
- 安装采用目录级替换并自动备份；失败时自动恢复旧版本。
- HTTPS 证书验证保持开启，不再使用不验证证书的网络上下文。
- 增加 `update_sources.json`、`update_manifest.example.json`、国内发布指南和 manifest 构建脚本。
