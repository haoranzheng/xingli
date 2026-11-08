#!/usr/bin/env python3
# coding: utf-8
"""Release v2.4.7 from shipped v2.4.6; fix invalid literal-escape INI.

Intentional minimal hotfix: all prior gameplay tools, version confirmation,
MO2 metadata paths, and sorting behavior must remain byte-identical.
"""
import ast
import configparser
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "update/releases/xingli_Little_assistant_v2.4.6_update.zip"
OUTPUT = ROOT / "update/releases/xingli_Little_assistant_v2.4.7_update.zip"
LATEST = ROOT / "update/latest.json"
PREFIX = "xingli_Little_assistant/"


def change_once(text, before, after, label):
    count = text.count(before)
    if count != 1:
        raise RuntimeError("{}: expected one match, got {}".format(label, count))
    return text.replace(before, after, 1)


def validate_runtime_ini(blob, version):
    if b"\\n" in blob or b"\\r" in blob:
        raise RuntimeError("version.ini contains literal escaped newline bytes")
    if b"\x00" in blob:
        raise RuntimeError("version.ini contains NUL")
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_file(io.StringIO(blob.decode("utf-8-sig")))
    actual = parser.get("Version", "local", fallback="").strip()
    if actual != version:
        raise RuntimeError("package version.ini mismatch: {} != {}".format(actual, version))
    if not blob.endswith(b"\n"):
        raise RuntimeError("version.ini needs a physical trailing newline")


def main():
    with zipfile.ZipFile(INPUT) as source:
        if source.testzip() is not None:
            raise RuntimeError("baseline zip failed CRC test")
        files = {item.filename: source.read(item)
                 for item in source.infolist() if not item.is_dir()}
    version_file = PREFIX + "version.ini"
    controller_file = PREFIX + "consolidation_controller.py"
    old_changelog = PREFIX + "CHANGELOG_v2.4.6.md"
    for required in (version_file, controller_file, old_changelog,
                     PREFIX + "modpack_confirmation.py"):
        if required not in files:
            raise RuntimeError("baseline missing {}".format(required))

    bad_ini = files[version_file]
    # The v2.4.6 builder accidentally published literal backslash+n bytes.
    if bad_ini != b"[Version]\\nlocal = 2.4.6\\n":
        raise RuntimeError("Unexpected baseline version.ini: {!r}".format(bad_ini))
    fixed_ini = b"[Version]\nlocal = 2.4.7\n"
    validate_runtime_ini(fixed_ini, "2.4.7")
    files[version_file] = fixed_ini

    controller = files[controller_file].decode("utf-8-sig").replace("\r\n", "\n")
    controller = change_once(controller, 'DEFAULT_VERSION = "2.4.6"',
                             'DEFAULT_VERSION = "2.4.7"', "default version")
    controller = change_once(controller, "version() 返回版本 2.4.6-user",
                             "version() 返回版本 2.4.7-user", "diagnostics version")
    controller = change_once(controller, "return mobase.VersionInfo(2, 4, 6)",
                             "return mobase.VersionInfo(2, 4, 7)", "MO2 version")
    files[controller_file] = controller.encode("utf-8")

    # The same escaping error affected the preceding changelog formatting.
    changelog = files[old_changelog].decode("utf-8-sig")
    if "\\n" in changelog and "\n" not in changelog:
        files[old_changelog] = changelog.replace("\\n", "\n").encode("utf-8")
    else:
        raise RuntimeError("Unexpected baseline changelog format; review required")

    files[PREFIX + "CHANGELOG_v2.4.7.md"] = (
        "# 星黎 MO2 小助手 v2.4.7：修复更新安装准备失败\n\n"
        "- 修复 v2.4.6 版本信息 INI 将换行符错误写为字面量的发布缺陷。\n"
        "- 恢复从 v2.4.5 及更早版本通过插件内自更新到新版本的能力。\n"
        "- 保留 v2.4.6 的整合包 4.0 已安装确认及通用后续版本处理。\n"
        "- 未修改整合包下载地址、MOD 元数据、MOD 排序或本地版本记录。\n"
        "- 发布前实际从 ZIP 读取并用同一 ConfigParser 方式校验版本。\n"
    ).encode("utf-8")

    for name, body in files.items():
        if name.endswith(".py"):
            ast.parse(body.decode("utf-8-sig"), filename=name, feature_version=(3, 8))

    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as out:
        for name in sorted(files):
            out.writestr(name, files[name])
    with zipfile.ZipFile(OUTPUT) as check:
        if check.testzip() is not None:
            raise RuntimeError("output ZIP failed CRC")
        validate_runtime_ini(check.read(version_file), "2.4.7")

    update = json.loads(LATEST.read_text(encoding="utf-8-sig"))
    if update.get("version") != "2.4.6":
        raise RuntimeError("expected v2.4.6 update manifest baseline")
    blob = OUTPUT.read_bytes()
    update["version"] = "2.4.7"
    update["title"] = "星黎 MO2 小助手 2.4.7：修复更新安装准备失败"
    update["changelog"] = (
        "修复 2.4.6 更新包 version.ini 的错误换行编码，解决"
        "“更新文件已下载，但准备安装失败：更新包版本信息无效”。"
        "保留 2.4.6 的整合包版本确认功能和既有 MOD、排序功能。"
    )
    update["published_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    update["package"] = {
        "filename": OUTPUT.name,
        "size": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "mirrors": [
            {
                "name": "Gitee 国内主源",
                "url": "https://gitee.com/HR-world/xingli/raw/main/update/releases/" + OUTPUT.name
            },
            {
                "name": "GitHub 备用源",
                "url": "https://raw.githubusercontent.com/haoranzheng/xingli/main/update/releases/" + OUTPUT.name
            },
        ],
    }
    LATEST.write_text(json.dumps(update, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print("BUILD_V247_PASS", len(blob), update["package"]["sha256"])


if __name__ == "__main__":
    main()
