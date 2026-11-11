#!/usr/bin/env python3
# coding: utf-8
"""Build v2.5.0 from v2.4.9, correcting UI ownership and modal dialog stacking."""
import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "update/releases/xingli_Little_assistant_v2.4.9_update.zip"
OUTPUT = ROOT / "update/releases/xingli_Little_assistant_v2.5.0_update.zip"
MANIFEST = ROOT / "update/latest.json"
PREFIX = "xingli_Little_assistant/"


def once(text, before, after, label):
    count = text.count(before)
    if count != 1:
        raise RuntimeError("{}: expected one source anchor, got {}".format(label, count))
    return text.replace(before, after, 1)


def patch_controller(text):
    for name in ("tutorial_window", "enb_window", "resolution_window"):
        text = once(
            text,
            "{} = QtWidgets.QDialog()".format(name),
            "{} = QtWidgets.QDialog(self.window)".format(name),
            "explicit assistant parent for {}".format(name),
        )
    text = once(text, 'DEFAULT_VERSION = "2.4.9"',
                'DEFAULT_VERSION = "2.5.0"', "plugin version")
    text = once(text, "version() 返回版本 2.4.9-user",
                "version() 返回版本 2.5.0-user", "diagnostic version")
    text = once(text, "return mobase.VersionInfo(2, 4, 9)",
                "return mobase.VersionInfo(2, 5, 0)", "MO2 version")
    # Main remains a modal QDialog. Child dialogs must own that window rather
    # than MO2's _parentWidget (a different, blocked top-level window).
    if "self.window.exec()" not in text:
        raise RuntimeError("Main assistant's dialog lifecycle changed")
    return text


def patch_notification_ui(source, module):
    source = once(
        source,
        "from . import stability\n",
        "from . import stability\nfrom . import dialog_ownership\n",
        module + " ownership import",
    )
    fn = "def _main_parent(controller):\n" if module == "mod_update_ui.py" else "def _parent(controller):\n"
    source = once(
        source, fn,
        fn + "    main = dialog_ownership.owner(controller)\n"
             "    if main is not None:\n"
             "        return main\n",
        module + " owner priority",
    )
    # Existing fallback code remains for extraordinary startup/no-assistant
    # paths, but the visible assistant now ALWAYS gets first choice.
    if module in ("announcement_ui.py", "profile_download_ui.py", "mod_update_ui.py", "sort_update_ui.py"):
        source = once(
            source, "    dialog.show()\n",
            "    dialog_ownership.show_owned_dialog(dialog, controller)\n",
            module + " open child with modal ownership",
        )
    if "dialog_ownership.owner(controller)" not in source:
        raise RuntimeError(module + " failed to use assistant-first parent")
    return source


def patch_auto_updater(source):
    # The startup update prompt is also fired through a timer while the
    # Xingli assistant QDialog.exec() modal loop is running.
    anchor = (
        "        parent = None\n"
        "        try:\n"
        "            parent = controller._parentWidget()\n"
        "        except Exception:\n"
        "            pass\n"
        "        dialog = QtWidgets.QMessageBox(parent)\n"
    )
    fixed = (
        "        from . import dialog_ownership\n"
        "        parent = dialog_ownership.owner(controller)\n"
        "        dialog = QtWidgets.QMessageBox(parent)\n"
    )
    return once(source, anchor, fixed, "self-updater prompt owner")


def main():
    with zipfile.ZipFile(BASE) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("v2.4.9 baseline ZIP corrupt")
        files = {
            entry.filename: archive.read(entry)
            for entry in archive.infolist() if not entry.is_dir()
        }

    version = PREFIX + "version.ini"
    if files.get(version) != b"[Version]\nlocal = 2.4.9\n":
        raise RuntimeError("Unexpected v2.4.9 update marker")

    controller = PREFIX + "consolidation_controller.py"
    files[controller] = patch_controller(
        files[controller].decode("utf-8-sig").replace("\r\n", "\n")
    ).encode("utf-8")

    for module in ("announcement_ui.py", "profile_download_ui.py", "mod_update_ui.py", "sort_update_ui.py"):
        key = PREFIX + module
        files[key] = patch_notification_ui(
            files[key].decode("utf-8-sig").replace("\r\n", "\n"), module
        ).encode("utf-8")

    key = PREFIX + "auto_updater.py"
    files[key] = patch_auto_updater(
        files[key].decode("utf-8-sig").replace("\r\n", "\n")
    ).encode("utf-8")

    files[PREFIX + "dialog_ownership.py"] = (
        ROOT / "development/v2.5.0/dialog_ownership.py"
    ).read_bytes()
    files[version] = b"[Version]\nlocal = 2.5.0\n"
    files[PREFIX + "CHANGELOG_v2.5.0.md"] = (
        "# 星黎 MO2 小助手 v2.5.0：修复弹窗被主界面遮挡\n\n"
        "- 修复更新公告优先绑定 MO2 窗口导致被星黎模态主窗口遮挡的问题。\n"
        "- 统一档案下载、MOD 更新和暂时停用的排序中心窗口归属及模态显示。\n"
        "- 修复小助手自身更新提醒绑定到错误窗口的潜在问题。\n"
        "- 为教程、ENB 管理、分辨率设置窗口显式设置星黎主界面为父窗口。\n"
        "- 禁止以 WindowStaysOnTopHint 全局置顶方式掩盖问题；子窗口关闭后主窗口恢复可操作。\n"
        "- 其他更新、MOD、档案、着色器公告与排序禁用行为保持不变。\n"
    ).encode("utf-8")

    for name, content in files.items():
        if name.endswith(".py"):
            ast.parse(content.decode("utf-8-sig"), filename=name, feature_version=(3, 8))

    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])

    with zipfile.ZipFile(OUTPUT) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("v2.5.0 ZIP failed CRC")
        assert archive.read(version) == b"[Version]\nlocal = 2.5.0\n"

    latest = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    if latest.get("version") != "2.4.9":
        raise RuntimeError("expected v2.4.9 updater manifest baseline")
    blob = OUTPUT.read_bytes()
    latest.update({
        "version": "2.5.0",
        "title": "星黎 MO2 小助手 2.5.0：修复窗口遮挡与关闭顺序",
        "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "changelog": (
            "修复着色器更新公告窗口显示在星黎主界面后方、导致无法正常关闭的缺陷；"
            "同步修复档案下载、MOD 更新及插件更新提示的窗口归属，"
            "统一子窗口模态显示；教程/ENB/分辨率窗口也明确绑定主界面。"
            "不修改 MOD、排序、整合包版本或着色器文件。"
        ),
        "package": {
            "filename": OUTPUT.name,
            "size": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
            "mirrors": [
                {"name": "Gitee 国内主源", "url":
                 "https://gitee.com/HR-world/xingli/raw/main/update/releases/" + OUTPUT.name},
                {"name": "GitHub 备用源", "url":
                 "https://raw.githubusercontent.com/haoranzheng/xingli/main/update/releases/" + OUTPUT.name},
            ],
        },
    })
    MANIFEST.write_text(
        json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("BUILD_V250_PASS", len(blob), latest["package"]["sha256"])


if __name__ == "__main__":
    main()
