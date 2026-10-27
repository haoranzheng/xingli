#!/usr/bin/env python3
# coding: utf-8
"""Build the v2.4.3 MO2 plugin as a minimal patch over the real v2.4.2 ZIP."""
import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "update/releases/xingli_Little_assistant_v2.4.2_update.zip"
OUTPUT = ROOT / "update/releases/xingli_Little_assistant_v2.4.3_update.zip"
LATEST = ROOT / "update/latest.json"
PREFIX = "xingli_Little_assistant/"


def change_once(text, old, new, label):
    if text.count(old) != 1:
        raise RuntimeError("Expected exactly one {}: found {}".format(label, text.count(old)))
    return text.replace(old, new, 1)


def main():
    with zipfile.ZipFile(INPUT) as archive:
        assert archive.testzip() is None
        files = {item.filename: archive.read(item) for item in archive.infolist()
                 if not item.is_dir()}
    controller = PREFIX + "consolidation_controller.py"
    sort_core = PREFIX + "sort_update_core.py"
    sort_ui = PREFIX + "sort_update_ui.py"
    text = files[controller].decode("utf-8-sig").replace("\r\n", "\n")
    text = change_once(text, 'DEFAULT_VERSION = "2.4.2"',
                       'DEFAULT_VERSION = "2.4.3"', "default version")
    text = change_once(text, "version() 返回版本 2.4.2-user",
                       "version() 返回版本 2.4.3-user", "diagnostics")
    text = change_once(text, "return mobase.VersionInfo(2, 4, 2)",
                       "return mobase.VersionInfo(2, 4, 3)", "MO2 plugin version")
    files[controller] = text.encode("utf-8")
    files[sort_core] = (ROOT / "development/v2.4.3/sort_update_core.py").read_bytes()
    ui = files[sort_ui].decode("utf-8-sig")
    ui = change_once(ui, "正在下载并校验排序 ZIP……",
                     "正在下载并校验排序 TXT 文件……", "download message")
    files[sort_ui] = ui.encode("utf-8")
    files[PREFIX + "version.ini"] = b"[Version]\nlocal = 2.4.3\n"
    files[PREFIX + "CHANGELOG_v2.4.3.md"] = (
        "# 星黎 v2.4.3：排序下载可靠性修复\n\n"
        "- 修复 Gitee 原始文件跳转到 raw.giteeusercontent.com 被错误阻止的问题。\n"
        "- 仅接受 Gitee 本域跳转至匹配路径的官方 CDN，其他跳转仍被拒绝。\n"
        "- 同时检查 Gitee 和 GitHub 排序清单，选择有效且更新较新的发布记录。\n"
        "- 下载出错时列出具体失败镜像；仍严格校验大小和 SHA256。\n"
        "- 排序只保存为当前 MO2 Profile 的新增恢复备份，不覆盖当前排序。\n"
    ).encode("utf-8")
    for name, body in files.items():
        if name.endswith(".py"):
            ast.parse(body.decode("utf-8-sig"), filename=name, feature_version=(3, 8))
    assert b"GITEE_CDN" in files[sort_core]
    assert b"def install_as_backups" in files[sort_core]
    assert b"mod_update_ui.maybe_auto_check(self)" not in files[controller]
    assert b"sort_update_ui.startup_check(self)" in files[controller]
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
    with zipfile.ZipFile(OUTPUT) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(files)
    blob = OUTPUT.read_bytes()
    data = json.loads(LATEST.read_text(encoding="utf-8-sig"))
    if data["version"] != "2.4.2":
        raise RuntimeError("Expected updater baseline 2.4.2")
    data["version"] = "2.4.3"
    data["title"] = "星黎 MO2 小助手 2.4.3：排序下载可靠性修复"
    data["changelog"] = ("修复 Gitee 排序 TXT 下载重定向和镜像清单不同步问题；"
                         "保持 MO2 排序备份的 SHA256 校验及非覆盖行为。")
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
    LATEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print("BUILD_V243_PASS", len(blob), data["package"]["sha256"])


if __name__ == "__main__":
    main()
