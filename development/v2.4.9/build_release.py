#!/usr/bin/env python3
# coding: utf-8
"""Build Xingli Assistant v2.4.9 announcement update from published v2.4.8."""
import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "update/releases/xingli_Little_assistant_v2.4.8_update.zip"
OUTPUT = ROOT / "update/releases/xingli_Little_assistant_v2.4.9_update.zip"
MANIFEST = ROOT / "update/latest.json"
PREFIX = "xingli_Little_assistant/"


def once(text, old, new, label):
    number = text.count(old)
    if number != 1:
        raise RuntimeError("{}: expected exactly one anchor, found {}".format(label, number))
    return text.replace(old, new, 1)


def patch_controller(text):
    text = once(
        text,
        "from . import profile_download_ui\n",
        "from . import profile_download_ui\nfrom . import announcement_ui\n",
        "announcement service import",
    )
    text = once(
        text,
        "        QTimer.singleShot(5400, lambda: profile_download_ui.startup_check(self))",
        "        QTimer.singleShot(5400, lambda: profile_download_ui.startup_check(self))\n"
        "        # 独立作者更新公告：不修改整合包版本或本地 MOD。\n"
        "        QTimer.singleShot(6100, lambda: announcement_ui.startup_check(self))",
        "background announcement check",
    )
    text = once(
        text,
        "        daily_grid.addWidget(profile_download_button, 2, 0)",
        "        daily_grid.addWidget(profile_download_button, 2, 0)\n"
        '        announcement_button = QtWidgets.QPushButton("📢  更新公告")\n'
        '        announcement_button.setToolTip("查看星黎个人着色器和其他模组的独立更新公告与网盘入口。")\n'
        "        announcement_button.clicked.connect(self.show_announcements)\n"
        "        daily_grid.addWidget(announcement_button, 2, 1)",
        "new announcement button",
    )
    text = once(
        text,
        "    def show_profile_downloads(self):\n"
        '        """打开 MO2 档案下载中心；下载只会新增 Profile 文件夹。"""\n'
        "        return profile_download_ui.show_center(self)\n",
        "    def show_profile_downloads(self):\n"
        '        """打开 MO2 档案下载中心；下载只会新增 Profile 文件夹。"""\n'
        "        return profile_download_ui.show_center(self)\n\n"
        "    def show_announcements(self):\n"
        '        """打开独立的作者资源更新公告中心。"""\n'
        "        return announcement_ui.show_center(self)\n",
        "controller announcement center",
    )
    text = once(text, 'DEFAULT_VERSION = "2.4.8"',
                'DEFAULT_VERSION = "2.4.9"', "default version")
    text = once(text, "version() 返回版本 2.4.8-user",
                "version() 返回版本 2.4.9-user", "diagnostic version")
    text = once(text, "return mobase.VersionInfo(2, 4, 8)",
                "return mobase.VersionInfo(2, 4, 9)", "MO2 plugin version")
    for expected in (
        "announcement_ui.startup_check(self)",
        "announcement_ui.show_center(self)",
        "profile_download_ui.startup_check(self)",
        "modpack_updater.maybe_auto_check(self)",
        "auto_updater.maybe_auto_check(self)",
    ):
        if expected not in text:
            raise RuntimeError("required existing or new integration missing: " + expected)
    for forbidden in ("sort_update_ui", "show_sort_updates", "排序备份更新"):
        if forbidden in text:
            raise RuntimeError("sorting updater must remain disabled: " + forbidden)
    return text


def main():
    with zipfile.ZipFile(BASE) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("v2.4.8 base ZIP failed CRC")
        files = {
            f.filename: archive.read(f)
            for f in archive.infolist() if not f.is_dir()
        }
    controller = PREFIX + "consolidation_controller.py"
    version = PREFIX + "version.ini"
    if files.get(version) != b"[Version]\nlocal = 2.4.8\n":
        raise RuntimeError("unexpected v2.4.8 version.ini baseline")
    files[controller] = patch_controller(
        files[controller].decode("utf-8-sig").replace("\r\n", "\n")
    ).encode("utf-8")
    files[version] = b"[Version]\nlocal = 2.4.9\n"

    for name in ("announcement_core.py", "announcement_ui.py"):
        source = ROOT / "development/v2.4.9" / name
        files[PREFIX + name] = source.read_bytes()

    files[PREFIX + "CHANGELOG_v2.4.9.md"] = (
        "# 星黎 MO2 小助手 v2.4.9：独立模组公告\n\n"
        "- 新增远程作者公告通道及「更新公告」入口。\n"
        "- 现推送星黎个人定制着色器的更新消息与夸克/百度网盘地址。\n"
        "- 下载仅打开系统浏览器，绝不自动替换着色器或改动 MOD。\n"
        "- 支持作者以后仅修改远程公告 JSON，不必每次重新发布插件。\n"
        "- 按公告 ID 与修订号记忆已读状态；稍后提醒不会标记已读。\n"
        "- 公告与整合包版本、插件版本、Profile 下载、排序更新完全独立。\n"
        "- 排序更新仍保持停用状态，既有 MO2/Nexus 功能保持不变。\n"
    ).encode("utf-8")

    for name, source in files.items():
        if name.endswith(".py"):
            ast.parse(source.decode("utf-8-sig"), filename=name, feature_version=(3, 8))

    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])

    with zipfile.ZipFile(OUTPUT) as archive:
        if archive.testzip() is not None or archive.read(version) != b"[Version]\nlocal = 2.4.9\n":
            raise RuntimeError("v2.4.9 packaged self-update metadata invalid")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    if manifest.get("version") != "2.4.8":
        raise RuntimeError("expected v2.4.8 updater manifest baseline")
    blob = OUTPUT.read_bytes()
    manifest["version"] = "2.4.9"
    manifest["title"] = "星黎 MO2 小助手 2.4.9：独立模组更新公告"
    manifest["published_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest["changelog"] = (
        "新增星黎模组更新公告通道：推送个人定制着色器下载信息，支持夸克网盘与百度网盘，"
        "可选择稍后提醒或不再提醒，并支持后续独立公告无需升级插件。"
        "不更改整合包版本、当前 MOD、排序或档案功能。"
    )
    manifest["package"] = {
        "filename": OUTPUT.name,
        "size": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "mirrors": [
            {"name": "Gitee 国内主源", "url":
             "https://gitee.com/HR-world/xingli/raw/main/update/releases/" + OUTPUT.name},
            {"name": "GitHub 备用源", "url":
             "https://raw.githubusercontent.com/haoranzheng/xingli/main/update/releases/" + OUTPUT.name},
        ],
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("BUILD_V249_PASS", len(blob), manifest["package"]["sha256"])


if __name__ == "__main__":
    main()
