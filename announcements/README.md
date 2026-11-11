# 星黎助手 · 远程更新公告

用于独立发布个人着色器等资源的下载通知。此公告不会改变星黎整合包版本、插件版本、MOD 排序或本地安装文件。

## 发布方式

直接编辑 `announcements/latest.json`。每次新增公告使用不同的 `id`（例如 `shader-20261111`），修改已有公告时增加对应 `revision`。

保留 `schema: 1`、`product: xingli_announcements`，各条目填写 `title`、`message`、`published_at` 和 `links`。目前外部下载入口仅允许 `pan.quark.cn` 和 `pan.baidu.com`。

GitHub 主分支的清单发布后，插件 v2.4.9 及以上启动时会读取 Gitee/GitHub 公告源。若 Gitee 同步较慢，GitHub 的相同 ID 且更新 revision 的公告优先。

玩家可以点击「夸克网盘」「百度网盘」，或稍后提醒 / 不再提醒；已读回执保存在 MO2 实例插件数据目录中。

**注意**：2.4.8 及更早版本并未实现通用公告拉取，需要先升级至 2.4.9 才能收到此通道的推送。
