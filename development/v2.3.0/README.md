# 星黎 v2.3.0：启动检查 / 更新提示 / 事务回滚（开发中）

本目录是独立开发分支的实现源文件，不是可安装的 MO2 插件发布包。当前 `main` 上的 v2.2.0 稳定 ZIP 与 `update/latest.json` 不变。

## 目标

- MO2 用户界面初始化后约 2.5 秒，在后台检查国内 Gitee 主源，失败时再试 GitHub raw 备用源；无论用户是否打开星黎窗口，主 MO2 窗口都能收到新版本弹窗
- 弹窗展示更新日志，“查看并更新”调用原有自更新 UI，不允许未经用户确认安装
- 外部更新器先校验 ZIP 大小和 SHA256、预解压到同盘 staging；MO2 完全退出后执行 live -> backup、staging -> live
- 下一次 MO2 初始化并确认升级成功才清除备份，否则独立外部进程执行回滚，重启 MO2
- 禁止 GitHub Releases。JSON 镜像与 ZIP 镜像继续沿用现有地址

## 尚需进行的集成（本分支**未交付可安装包**）

1. 从 `update/releases/xingli_Little_assistant_v2.2.0_update.zip` 解包实际 v2.2.0 代码，检查 `consolidation_controller.py` 的 `init()` / `onUserInterfaceInitialized` 回调。
2. 在 MO2 的 UI ready 回调中实例化并保持 `StartupUpdateChecker` 引用：`parent_window` 指向 MO2 主窗口，`on_install_requested` 回调打开现有更新器；使用主程序现有的本地版本值。启动清单 URL 使用 `https://gitee.com/HR-world/xingli/raw/main/update/latest.json`，回退 `https://raw.githubusercontent.com/haoranzheng/xingli/main/update/latest.json`。
3. 将 `transactional_update.py` 集成到 **MO2 进程外** 的安装器，由该进程等待 MO2 正常退出，`prepare_install()`、`apply_prepared()`，监测新版启动与健康确认，未确认或超时则在 MO2 退出后 `rollback()`。不能在 MO2 正在载入插件时替换文件。
4. 更新包发布前应覆盖：HTTP 超时/离线/镜像回退；较老与相同版本；重复弹窗；ZIP 路径穿越与 SHA256 失败；应用中途异常；新版崩溃后的外部进程回滚；备份丢失时保留现场并报告；MO2 2.4.4 人工验收。
5. 将稳定发布版本改为 2.3.0，产出可安装 ZIP 并生成真实的 SHA256 后，才允许更新 `update/latest.json`。

## 重要限制

- `startup_update.py` 提供非阻塞后台检查与弹窗接口，但尚未挂载到 MO2 启动生命周期。
- `transactional_update.py` 提供安全事务原语，尚未连接旧版安装器与外部健康监视。
- 不要把这些开发文件当作已经实现的完整自动更新功能，也不要提前发布给整合包用户。
