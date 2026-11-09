#!/usr/bin/env python3
# coding: utf-8
"""Build Xingli MO2 Assistant v2.4.8 from the shipped v2.4.7 runtime ZIP."""
import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "update/releases/xingli_Little_assistant_v2.4.7_update.zip"
OUTPUT = ROOT / "update/releases/xingli_Little_assistant_v2.4.8_update.zip"
LATEST = ROOT / "update/latest.json"
PREFIX = "xingli_Little_assistant/"


def replace_once(source, old, new, label):
    count = source.count(old)
    if count != 1:
        raise RuntimeError("{}: expected one exact anchor, got {}".format(label, count))
    return source.replace(old, new, 1)


def patch_controller(source):
    source = replace_once(
        source,
        "from . import sort_update_ui\n",
        "from . import profile_download_ui\n",
        "replace sorting updater import",
    )
    source = replace_once(
        source,
        "# Disabled temporarily: single-MOD update startup check.\n"
        "        QTimer.singleShot(5400, lambda: sort_update_ui.startup_check(self))",
        "# MO2 档案下载：启动后检查作者是否发布了新档案。\n"
        "        QTimer.singleShot(5400, lambda: profile_download_ui.startup_check(self))",
        "replace sorting startup check",
    )
    source = replace_once(
        source,
        "# The per-MOD update implementation remains packaged, but its entry\n"
        "        # point and startup checks are intentionally hidden until re-enabled.\n"
        '        sort_updates_button = QtWidgets.QPushButton("📑  排序备份更新")\n'
        '        sort_updates_button.setToolTip("从国内镜像下载可由 MO2 原生还原的排序备份，不直接修改排序。")\n'
        "        sort_updates_button.clicked.connect(self.show_sort_updates)\n"
        "        daily_grid.addWidget(sort_updates_button, 2, 0)",
        "# 排序更新暂时停用；保留旧模块和数据，不执行远程排序检查。\n"
        '        profile_download_button = QtWidgets.QPushButton("📁  档案下载")\n'
        '        profile_download_button.setToolTip("下载作者发布的 MO2 Profile 档案，作为新档案添加，不覆盖现有档案。")\n'
        "        profile_download_button.clicked.connect(self.show_profile_downloads)\n"
        "        daily_grid.addWidget(profile_download_button, 2, 0)",
        "replace sorting button with profile downloads",
    )
    source = replace_once(
        source,
        "    def show_sort_updates(self):\n"
        '        """打开排序备份下载中心，不自动恢复排序。"""\n'
        "        return sort_update_ui.show_center(self)\n",
        "    def show_profile_downloads(self):\n"
        '        """打开 MO2 档案下载中心；下载只会新增 Profile 文件夹。"""\n'
        "        return profile_download_ui.show_center(self)\n",
        "replace sorting center action",
    )
    source = replace_once(
        source,
        'DEFAULT_VERSION = "2.4.7"',
        'DEFAULT_VERSION = "2.4.8"',
        "default plugin version",
    )
    source = replace_once(
        source,
        "version() 返回版本 2.4.7-user",
        "version() 返回版本 2.4.8-user",
        "diagnostic version",
    )
    source = replace_once(
        source,
        "return mobase.VersionInfo(2, 4, 7)",
        "return mobase.VersionInfo(2, 4, 8)",
        "MO2 version",
    )
    if "sort_update_ui" in source or "show_sort_updates" in source or "排序备份更新" in source:
        raise RuntimeError("sorting updater is still reachable from the v2.4.8 controller")
    for token in (
        "profile_download_ui.startup_check(self)",
        "profile_download_ui.show_center(self)",
        "档案下载",
    ):
        if token not in source:
            raise RuntimeError("profile download integration missing: " + token)
    return source


def main():
    if not INPUT.is_file():
        raise RuntimeError("Published v2.4.7 runtime ZIP is missing")
    with zipfile.ZipFile(INPUT) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("v2.4.7 baseline ZIP is corrupt")
        files = {
            item.filename: archive.read(item)
            for item in archive.infolist() if not item.is_dir()
        }

    controller_name = PREFIX + "consolidation_controller.py"
    version_name = PREFIX + "version.ini"
    for required in (
        controller_name,
        version_name,
        PREFIX + "auto_updater.py",
        PREFIX + "sort_update_core.py",
        PREFIX + "sort_update_ui.py",
    ):
        if required not in files:
            raise RuntimeError("v2.4.7 runtime is missing " + required)

    old_version = files[version_name]
    if old_version != b"[Version]\nlocal = 2.4.7\n":
        raise RuntimeError("Unexpected v2.4.7 version.ini: {!r}".format(old_version))

    files[controller_name] = patch_controller(
        files[controller_name].decode("utf-8-sig").replace("\r\n", "\n")
    ).encode("utf-8")
    files[version_name] = b"[Version]\nlocal = 2.4.8\n"

    for module in ("profile_download_core.py", "profile_download_ui.py"):
        source_path = ROOT / "development/v2.4.8" / module
        if not source_path.is_file():
            raise RuntimeError("Profile module source missing: " + str(source_path))
        files[PREFIX + module] = source_path.read_bytes()

    files[PREFIX + "CHANGELOG_v2.4.8.md"] = (
        "# 星黎 MO2 小助手 v2.4.8：档案下载与排序更新暂时停用\n\n"
        "- 暂时关闭排序更新的启动检查、通知和手动入口；原排序文件、备份及 MO2 原生还原能力不受影响。\n"
        "- 新增 MO2 Profile 档案发布清单检查，发现新档案或新版本时主动通知。\n"
        "- 新增档案下载中心，支持查看说明、校验 SHA256 并下载完整 Profile 文件夹。\n"
        "- 下载只会在当前 MO2 profiles 根目录新增一个不冲突的档案目录，不覆盖现有档案。\n"
        "- 不修改当前活动档案，不自动切换 Profile，不安装或启用 MOD。\n"
        "- 档案使用显式数字版本号和独立 ID，不依赖日期命名或文件时间判断。\n"
        "- 新增 ZIP 路径穿越、符号链接、重复文件名、文件数量和解压体积校验。\n"
        "- 保留整合包版本确认、Nexus 修复、MOD 管理和其他既有功能。\n"
    ).encode("utf-8")

    for name, body in files.items():
        if name.endswith(".py"):
            ast.parse(body.decode("utf-8-sig"), filename=name, feature_version=(3, 8))

    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
    with zipfile.ZipFile(OUTPUT) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("v2.4.8 output ZIP is corrupt")
        version_bytes = archive.read(version_name)
        if version_bytes != b"[Version]\nlocal = 2.4.8\n":
            raise RuntimeError("v2.4.8 version.ini validation failed")
        controller = archive.read(controller_name).decode("utf-8")
        if "sort_update_ui" in controller or "show_sort_updates" in controller:
            raise RuntimeError("sort update entry point remains enabled")
        if "profile_download_ui.startup_check(self)" not in controller:
            raise RuntimeError("profile startup check missing")

    manifest = json.loads(LATEST.read_text(encoding="utf-8-sig"))
    if manifest.get("version") != "2.4.7":
        raise RuntimeError("Expected v2.4.7 updater manifest baseline")
    blob = OUTPUT.read_bytes()
    manifest["version"] = "2.4.8"
    manifest["title"] = "星黎 MO2 小助手 2.4.8：档案下载"
    manifest["published_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest["changelog"] = (
        "暂时停用排序更新检查、弹窗和手动入口，保留原排序数据与 MO2 原生还原功能；"
        "新增 MO2 Profile 档案发布检查、通知、下载和安全安装。下载会在 profiles 目录"
        "创建不冲突的新档案，不覆盖现有档案、不修改当前档案、不自动切换或启用 MOD。"
    )
    manifest["package"] = {
        "filename": OUTPUT.name,
        "size": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "mirrors": [
            {
                "name": "Gitee 国内主源",
                "url": "https://gitee.com/HR-world/xingli/raw/main/update/releases/" + OUTPUT.name,
            },
            {
                "name": "GitHub 备用源",
                "url": "https://raw.githubusercontent.com/haoranzheng/xingli/main/update/releases/" + OUTPUT.name,
            },
        ],
    }
    LATEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("BUILD_V248_PASS", len(blob), manifest["package"]["sha256"])


if __name__ == "__main__":
    main()
