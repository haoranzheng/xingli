#!/usr/bin/env python3
# coding: utf-8
"""Build v2.4.5 as a verified compatibility patch over actual v2.4.4 ZIP."""
import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "update/releases/xingli_Little_assistant_v2.4.4_update.zip"
OUTPUT = ROOT / "update/releases/xingli_Little_assistant_v2.4.5_update.zip"
LATEST = ROOT / "update/latest.json"
PREFIX = "xingli_Little_assistant/"


def change_once(text, before, after, label):
    hits = text.count(before)
    if hits != 1:
        raise RuntimeError("{}: expected one anchor, found {}".format(label, hits))
    return text.replace(before, after, 1)


def replace_method(text, start, end, method, label):
    if text.count(start) != 1 or text.count(end) != 1:
        raise RuntimeError("{}: method anchors changed".format(label))
    begin = text.index(start)
    finish = text.index(end, begin)
    if finish <= begin:
        raise RuntimeError("{}: invalid method range".format(label))
    return text[:begin] + method + "\n" + text[finish:]


def patch_controller(text):
    text = change_once(text,
                       "from . import nexus_archive_metadata\n",
                       "from . import nexus_archive_metadata\n"
                       "from . import nexus_repair_service\n",
                       "service import")
    text = change_once(text, 'DEFAULT_VERSION = "2.4.4"',
                       'DEFAULT_VERSION = "2.4.5"', "default version")
    text = change_once(text, "version() 返回版本 2.4.4-user",
                       "version() 返回版本 2.4.5-user", "version diagnostics")
    text = change_once(text, "return mobase.VersionInfo(2, 4, 4)",
                       "return mobase.VersionInfo(2, 4, 5)", "MO2 version")
    callback = '''    def _on_nexus_metadata_installed(self, mod_or_name):
        """After native MO2 installs, retry after the asynchronous mod-list refresh."""
        try:
            name = str(mod_or_name.name()) if hasattr(mod_or_name, "name") else str(mod_or_name)
            archive_hint = ""
            if hasattr(mod_or_name, "installationFile"):
                try:
                    archive_hint = str(mod_or_name.installationFile() or "")
                except Exception:
                    pass

            def recheck(stage):
                try:
                    if not self.organizer:
                        return
                    status = nexus_repair_service.apply_new_install(
                        self.organizer, name, archive_hint)
                    logger.info("Nexus install check (%s) %s: %s", stage, name, status)
                except Exception:
                    logger.exception("Nexus install check (%s) failed for %s", stage, name)

            # Important: a single QTimer.singleShot(0) was too early for some
            # MO2 installs; later passes are idempotent and touch only known IDs.
            QTimer.singleShot(350, lambda: recheck("early"))
            QTimer.singleShot(1800, lambda: recheck("settled"))
            QTimer.singleShot(4500, lambda: recheck("final"))
            try:
                self.organizer.onNextRefresh(
                    lambda: QTimer.singleShot(0, lambda: recheck("refresh")), False)
            except Exception:
                logger.debug("MO2 onNextRefresh unavailable for %s", name)
        except Exception:
            logger.exception("Unable to schedule new MOD Nexus metadata check")
'''
    return replace_method(
        text, "    def _on_nexus_metadata_installed(self, mod_or_name):\n",
        "    def _on_mo2_about_to_run(self, executable):\n",
        callback, "native install hook")


def patch_manager(text):
    text = change_once(text,
                       "from . import nexus_archive_metadata\n",
                       "from . import nexus_archive_metadata\n"
                       "from . import nexus_repair_ui\n"
                       "from . import nexus_repair_service\n",
                       "manager imports")
    method = '''    def _preview_nexus_repairs(self):
        """Qt slots must not leak Python exceptions into MO2."""
        try:
            return nexus_repair_ui.show_preview(
                self, self.organizer, self.browser.reload)
        except Exception:
            logger.exception("Nexus repair UI failure")
            QtWidgets.QMessageBox.warning(
                self, "Nexus 信息修复", "修复预览出现异常，没有执行后续修改。")
            return 0
'''
    text = replace_method(
        text, "    def _preview_nexus_repairs(self):\n",
        "    def _offer_pending_transaction_recovery(self):\n",
        method, "repair UI replacement")
    target = '''                result = self.organizer.installMod(path)
                if result:
                    ok += 1
'''
    replacement = '''                result = self.organizer.installMod(path)
                if result:
                    ok += 1
                    # The original selected archive path is authoritative here.
                    try:
                        installed_name = str(result.name())
                        status = nexus_repair_service.apply_new_install(
                            self.organizer, installed_name, path)
                        logger.info("Nexus batch install check for %s: %s",
                                    installed_name, status)
                    except Exception:
                        logger.exception("Nexus batch install metadata check failed: %s", path)
'''
    return change_once(text, target, replacement, "batch post-install check")


def main():
    with zipfile.ZipFile(INPUT) as archive:
        assert archive.testzip() is None
        files = {info.filename: archive.read(info)
                 for info in archive.infolist() if not info.is_dir()}
    controller = PREFIX + "consolidation_controller.py"
    manager = PREFIX + "mod_manager.py"
    files[controller] = patch_controller(
        files[controller].decode("utf-8-sig").replace("\r\n", "\n")).encode("utf-8")
    files[manager] = patch_manager(
        files[manager].decode("utf-8-sig").replace("\r\n", "\n")).encode("utf-8")
    for filename in ("nexus_repair_ui.py", "nexus_repair_service.py"):
        files[PREFIX + filename] = (
            ROOT / "development/v2.4.5" / filename).read_bytes()
    files[PREFIX + "version.ini"] = b"[Version]\nlocal = 2.4.5\n"
    files[PREFIX + "CHANGELOG_v2.4.5.md"] = (
        "# 星黎 v2.4.5：Nexus 修复稳定性\n\n"
        "- 修复 Qt5/Qt6 兼容性和修复预览异常导致的退出风险。\n"
        "- 支持全选、取消全选、Ctrl/Shift 多选行、勾选与取消选中行。\n"
        "- 预览时不再持有 MO2 原生 MOD 对象，修复前重新读取和校验。\n"
        "- 新安装后重试核对；星黎批量安装直接使用明确的归档路径。\n"
        "- 原有自定义 URL、已有有效 ID 和 MOD 排序均保持保护。\n"
    ).encode("utf-8")
    for path, data in files.items():
        if path.endswith(".py"):
            ast.parse(data.decode("utf-8-sig"), filename=path, feature_version=(3, 8))
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
    with zipfile.ZipFile(OUTPUT) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(files)
    blob = OUTPUT.read_bytes()
    latest = json.loads(LATEST.read_text(encoding="utf-8-sig"))
    if latest["version"] != "2.4.4":
        raise RuntimeError("Expected published updater baseline 2.4.4")
    latest["version"] = "2.4.5"
    latest["title"] = "星黎 MO2 小助手 2.4.5：Nexus 修复稳定性"
    latest["changelog"] = (
        "修复 MOD 元数据修复闪退风险、支持全选和多选；"
        "重新获取 MO2 MOD 对象防止失效引用，并增加安装后校验重试。")
    latest["published_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    latest["package"] = {
        "filename": OUTPUT.name, "size": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "mirrors": [
            {"name": "Gitee 国内主源",
             "url": "https://gitee.com/HR-world/xingli/raw/main/update/releases/" + OUTPUT.name},
            {"name": "GitHub 备用源",
             "url": "https://raw.githubusercontent.com/haoranzheng/xingli/main/update/releases/" + OUTPUT.name},
        ],
    }
    LATEST.write_text(
        json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("BUILD_V245_PASS", len(blob), latest["package"]["sha256"])


if __name__ == "__main__":
    main()
