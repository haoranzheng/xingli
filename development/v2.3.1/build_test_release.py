# coding: utf-8
"""Build a harmless v2.3.1 update-notification test release from v2.3.0.

Only version metadata and a test changelog are changed. Every other plugin
file remains byte-for-byte identical to the published v2.3.0 archive.
"""
import ast
import hashlib
import json
import pathlib
import zipfile
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
BASE = ROOT / "update/releases/xingli_Little_assistant_v2.3.0_update.zip"
TARGET = ROOT / "update/releases/xingli_Little_assistant_v2.3.1_update.zip"
LATEST = ROOT / "update/latest.json"
PREFIX = "xingli_Little_assistant/"
EXCEPTIONS = {
    PREFIX + "consolidation_controller.py",
    PREFIX + "version.ini",
    PREFIX + "CHANGELOG_v2.3.1.md",
}


def replace_once(text, original, new, label):
    count = text.count(original)
    if count != 1:
        raise AssertionError("%s: expected exactly one anchor, found %d" % (label, count))
    return text.replace(original, new, 1)


def build():
    manifest = json.loads(LATEST.read_text(encoding="utf-8-sig"))
    if manifest["version"] != "2.3.0":
        raise RuntimeError("Unexpected published source version: " + repr(manifest["version"]))
    with zipfile.ZipFile(BASE) as z:
        bad = z.testzip()
        if bad is not None:
            raise RuntimeError("Invalid 2.3.0 update archive member: " + bad)
        original = {i.filename: z.read(i) for i in z.infolist() if not i.is_dir()}
    files = dict(original)
    controller = PREFIX + "consolidation_controller.py"
    ini = PREFIX + "version.ini"
    updater = PREFIX + "auto_updater.py"
    for path in (controller, ini, updater):
        if path not in files:
            raise RuntimeError("Published package missing required file " + path)
    s = files[controller].decode("utf-8-sig")
    s = replace_once(s, 'DEFAULT_VERSION = "2.3.0"',
                     'DEFAULT_VERSION = "2.3.1"', "default version")
    s = replace_once(s, 'version() 返回版本 2.3.0-user',
                     'version() 返回版本 2.3.1-user', "diagnostic version")
    s = replace_once(s, 'return mobase.VersionInfo(2, 3, 0)',
                     'return mobase.VersionInfo(2, 3, 1)', "MO2 API version")
    files[controller] = s.encode("utf-8")
    files[ini] = b"[Version]\nlocal = 2.3.1\n"
    files[PREFIX + "CHANGELOG_v2.3.1.md"] = (
        "# v2.3.1 — 更新主动弹窗测试版\n\n"
        "- 本版用于测试 MO2 启动时的新版本主动提示。\n"
        "- 保留 v2.3.0 所有插件功能，未新增其它功能。\n"
        "- 完整更新包可通过现有国内镜像源下载、校验并安装。\n"
        "- 用户无需先打开星黎小助手窗口；仅需重启 MO2 测试提醒。\n"
        "- 如不打算执行安装，可点击“稍后提醒”验证通知行为。\n"
    ).encode("utf-8")
    # Ensure there are no unintended payload modifications.
    for name, payload in original.items():
        if name not in EXCEPTIONS and files[name] != payload:
            raise AssertionError("Unintended change to " + name)
        if name.endswith(".py"):
            ast.parse(files[name].decode("utf-8-sig"), filename=name, feature_version=(3, 8))
    assert "def _prompt_startup_update" in files[updater].decode("utf-8-sig")
    assert "mark_startup_healthy(self)" in s
    assert "QTimer.singleShot(2600, lambda: auto_updater.maybe_auto_check(self))" in s
    assert "2.3.1" in files[ini].decode("utf-8")
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(TARGET, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in sorted(files):
            z.writestr(name, files[name])
    with zipfile.ZipFile(TARGET) as z:
        assert z.testzip() is None
        assert len(z.namelist()) == len(files)
    blob = TARGET.read_bytes()
    release = TARGET.name
    manifest["version"] = "2.3.1"
    manifest["title"] = "星黎 MO2 小助手 2.3.1（更新通知测试）"
    manifest["published_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest["changelog"] = "v2.3.1 虚拟更新推送测试：验证启动 MO2 后的新版本主动弹窗提示。插件功能与 v2.3.0 保持一致；可选择稍后提醒，也可正常安装测试包。"
    manifest["package"] = {
        "filename": release,
        "size": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "mirrors": [
            {
                "name": "Gitee 国内主源",
                "url": "https://gitee.com/HR-world/xingli/raw/main/update/releases/" + release,
            },
            {
                "name": "GitHub 备用源",
                "url": "https://raw.githubusercontent.com/haoranzheng/xingli/main/update/releases/" + release,
            },
        ],
    }
    LATEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("BUILD_231_PASS archive_files=%d bytes=%d sha256=%s" % (
        len(files), len(blob), manifest["package"]["sha256"]))


if __name__ == "__main__":
    build()
