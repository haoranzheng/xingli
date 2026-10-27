#!/usr/bin/env python3
# coding: utf-8
"""Build v2.4.4 strictly as a small patch over the published v2.4.3 ZIP."""
import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "update/releases/xingli_Little_assistant_v2.4.3_update.zip"
OUTPUT = ROOT / "update/releases/xingli_Little_assistant_v2.4.4_update.zip"
LATEST = ROOT / "update/latest.json"
PREFIX = "xingli_Little_assistant/"


def change_once(text, old, new, label):
    found = text.count(old)
    if found != 1:
        raise RuntimeError("Expected one {} in baseline, got {}".format(label, found))
    return text.replace(old, new, 1)


def patch_controller(text):
    text = change_once(text, "from . import mod_manager\n",
                       "from . import mod_manager\nfrom . import nexus_archive_metadata\n", "metadata import")
    text = change_once(text, 'DEFAULT_VERSION = "2.4.3"',
                       'DEFAULT_VERSION = "2.4.4"', "default version")
    text = change_once(text, "version() 返回版本 2.4.3-user",
                       "version() 返回版本 2.4.4-user", "version diagnostics")
    text = change_once(text, "return mobase.VersionInfo(2, 4, 3)",
                       "return mobase.VersionInfo(2, 4, 4)", "MO2 plugin version")
    target = "        stability.log_runtime_compatibility(organizer, self.plugin_path)\n"
    replacement = target + """        # MO2 install completion hook: update metadata only for high-confidence filenames.
        try:
            organizer.modList().onModInstalled(self._on_nexus_metadata_installed)
        except Exception:
            logger.exception("无法注册 MOD 安装元数据校验回调")
"""
    text = change_once(text, target, replacement, "install hook registration")
    target = "    def _on_mo2_about_to_run(self, executable):\n"
    replacement = """    def _on_nexus_metadata_installed(self, mod_or_name):
        # Defer until MO2 has finished its installation and written metadata.
        try:
            name = str(mod_or_name.name()) if hasattr(mod_or_name, "name") else str(mod_or_name)
            def after_install():
                try:
                    mod = self.organizer.modList().getMod(name)
                    if mod is None:
                        return
                    game = nexus_archive_metadata.nexus_game_domain(self.organizer)
                    suggestion = nexus_archive_metadata.propose(mod, game)
                    if suggestion and nexus_archive_metadata.apply(self.organizer, mod, suggestion):
                        logger.info("修正新装 MOD 的 Nexus 信息: %s -> %s",
                                    name, suggestion["mod_id"])
                except Exception:
                    logger.exception("新装 MOD 元数据检查失败: %s", name)
            QTimer.singleShot(0, after_install)
        except Exception:
            logger.exception("安排 MOD 元数据检查失败")

""" + target
    return change_once(text, target, replacement, "on install callback")


def patch_mod_manager(text):
    text = change_once(text,
                       "from .mod_state_service import ModRecord, ModStateService\n",
                       "from .mod_state_service import ModRecord, ModStateService\n"
                       "from . import nexus_archive_metadata\n",
                       "mod manager metadata import")
    target = "        installed_layout.addWidget(self.browser)\n"
    replacement = target + """        self.nexus_repair_button = QtWidgets.QPushButton("预览并修复 Nexus MOD ID / 链接…")
        self.nexus_repair_button.setToolTip("从安装压缩包中识别高置信度 Nexus ID；确认前不会改动已有 MOD。")
        installed_layout.addWidget(self.nexus_repair_button)
        self.nexus_repair_button.clicked.connect(self._preview_nexus_repairs)
"""
    text = change_once(text, target, replacement, "repair preview button")
    target = "    def _offer_pending_transaction_recovery(self):\n"
    replacement = """    def _preview_nexus_repairs(self):
        try:
            candidates = nexus_archive_metadata.find_candidates(self.organizer)
        except Exception:
            logger.exception("扫描 Nexus MOD 信息失败")
            QtWidgets.QMessageBox.warning(self, "Nexus 信息修复", "扫描失败，未修改任何 MOD。")
            return
        if not candidates:
            QtWidgets.QMessageBox.information(
                self, "Nexus 信息修复",
                "没有发现可安全识别的错误编号。仅支持带明确 MOD ID、版本和时间戳的归档名；"
                "不会凭 MOD 名称猜测编号。")
            return

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("预览 Nexus 信息修复（默认不勾选）")
        dialog.resize(930, 520)
        layout = QtWidgets.QVBoxLayout(dialog)
        hint = QtWidgets.QLabel(
            "以下 MOD 的压缩包文件名含有可核对的 Nexus ID。"
            "勾选需要修复的项并确认；不会修改 MOD 文件、排序或自定义网址。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        table = QtWidgets.QTableWidget(len(candidates), 4, dialog)
        table.setHorizontalHeaderLabels(["勾选 / MOD", "当前 ID", "识别出的 ID", "URL 处理"])
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        for row, (_, candidate) in enumerate(candidates):
            name = QtWidgets.QTableWidgetItem(candidate["name"])
            name.setFlags(name.flags() | Qt.ItemIsUserCheckable)
            name.setCheckState(_unchecked_value())
            name.setToolTip(candidate["archive"])
            table.setItem(row, 0, name)
            table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(candidate["old_id"])))
            table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(candidate["mod_id"])))
            table.setItem(row, 3, QtWidgets.QTableWidgetItem(
                "修正为 Nexus 页面" if candidate["change_url"] else "保留自定义网址"))
        table.horizontalHeader().setStretchLastSection(True)
        try:
            table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        except Exception:
            pass
        layout.addWidget(table)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        accepted = dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
        if accepted != QtWidgets.QDialog.Accepted:
            return
        chosen = [candidates[i] for i in range(len(candidates))
                  if table.item(i, 0).checkState() == _checked_value()]
        if not chosen:
            QtWidgets.QMessageBox.information(self, "Nexus 信息修复", "没有勾选 MOD，未做修改。")
            return
        completed = 0
        failed = []
        for mod, proposal in chosen:
            try:
                if nexus_archive_metadata.apply(self.organizer, mod, proposal):
                    completed += 1
                else:
                    failed.append(proposal["name"] + "（元数据已变化，跳过）")
            except Exception as exc:
                logger.exception("修复 Nexus 元数据失败: %s", proposal["name"])
                failed.append("{}: {}".format(proposal["name"], exc))
        self.browser.reload()
        message = "已修复 {} 个 MOD；跳过/失败 {} 个。".format(completed, len(failed))
        if failed:
            message += "\\n" + "\\n".join(failed[:12])
        QtWidgets.QMessageBox.information(self, "Nexus 信息修复", message)

""" + target
    return change_once(text, target, replacement, "repair preview dialog")


def main():
    with zipfile.ZipFile(INPUT) as archive:
        assert archive.testzip() is None
        files = {item.filename: archive.read(item)
                 for item in archive.infolist() if not item.is_dir()}
    controller = PREFIX + "consolidation_controller.py"
    manager = PREFIX + "mod_manager.py"
    files[controller] = patch_controller(files[controller].decode("utf-8-sig").replace("\r\n", "\n")).encode("utf-8")
    files[manager] = patch_mod_manager(files[manager].decode("utf-8-sig").replace("\r\n", "\n")).encode("utf-8")
    files[PREFIX + "nexus_archive_metadata.py"] = (
        ROOT / "development/v2.4.4/nexus_archive_metadata.py").read_bytes()
    files[PREFIX + "version.ini"] = b"[Version]\nlocal = 2.4.4\n"
    files[PREFIX + "CHANGELOG_v2.4.4.md"] = (
        "# 星黎 v2.4.4：Nexus MOD ID 安全识别修复\n\n"
        "- 识别带编号、版本、UTC 时间戳及唯一标识的 Nexus 归档名。\n"
        "- 新装 MOD 自动修复错误的 Nexus ID（包括误识别为 10）。\n"
        "- 已安装 MOD 可在高级模式的模组管理中心预览、勾选、确认修复。\n"
        "- 不覆盖其他有效 MOD ID、不覆盖自定义 URL；不修改排序或 MOD 文件。\n"
    ).encode("utf-8")
    for name, body in files.items():
        if name.endswith(".py"):
            ast.parse(body.decode("utf-8-sig"), filename=name, feature_version=(3, 8))
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
    with zipfile.ZipFile(OUTPUT) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(files)
    blob = OUTPUT.read_bytes()
    data = json.loads(LATEST.read_text(encoding="utf-8-sig"))
    if data["version"] != "2.4.3":
        raise RuntimeError("Expected published updater baseline v2.4.3")
    data["version"] = "2.4.4"
    data["title"] = "星黎 MO2 小助手 2.4.4：Nexus MOD ID 识别修复"
    data["changelog"] = (
        "兼容含 Nexus 编号、版本和时间戳的归档名称，修正安装后误写为 10 的 MOD ID / 链接；"
        "已安装 MOD 可预览并逐项勾选修复；保留自定义网址和其他有效编号。")
    data["published_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["package"] = {
        "filename": OUTPUT.name, "size": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "mirrors": [
            {"name": "Gitee 国内主源",
             "url": "https://gitee.com/HR-world/xingli/raw/main/update/releases/" + OUTPUT.name},
            {"name": "GitHub 备用源",
             "url": "https://raw.githubusercontent.com/haoranzheng/xingli/main/update/releases/" + OUTPUT.name},
        ],
    }
    LATEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("BUILD_V244_PASS", len(blob), data["package"]["sha256"])


if __name__ == "__main__":
    main()
