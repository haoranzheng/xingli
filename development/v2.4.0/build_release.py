#!/usr/bin/env python3
# coding: utf-8
"""Build Xingli v2.4.0 safely from the verified 2.3.1 runtime ZIP."""
import ast
import hashlib
import json
from pathlib import Path
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "update/releases/xingli_Little_assistant_v2.3.1_update.zip"
TARGET = ROOT / "update/releases/xingli_Little_assistant_v2.4.0_update.zip"
LATEST = ROOT / "update/latest.json"
PREFIX = "xingli_Little_assistant/"


def replace_exact(source, original, updated, label):
    count = source.count(original)
    if count != 1:
        raise RuntimeError("Unexpected %s anchor count: %d" % (label, count))
    return source.replace(original, updated, 1)


def main():
    with zipfile.ZipFile(BASE) as z:
        assert z.testzip() is None
        files = {info.filename: z.read(info) for info in z.infolist() if not info.is_dir()}
    controller_key = PREFIX + "consolidation_controller.py"
    if controller_key not in files:
        raise RuntimeError("MO2 controller missing")
    s = files[controller_key].decode("utf-8-sig").replace("\r\n", "\n")
    s = replace_exact(s, 'DEFAULT_VERSION = "2.3.1"',
                      'DEFAULT_VERSION = "2.4.0"', "default version")
    s = replace_exact(s, "version() 返回版本 2.3.1-user",
                      "version() 返回版本 2.4.0-user", "version log")
    s = replace_exact(s, "return mobase.VersionInfo(2, 3, 1)",
                      "return mobase.VersionInfo(2, 4, 0)", "MO2 version")
    s = replace_exact(s, "from . import modpack_updater\n",
                      "from . import modpack_updater\nfrom . import mod_update_ui\n", "import")
    s = replace_exact(s, 'self._modpack_update_prompted_version = ""',
                      'self._modpack_update_prompted_version = ""\n'
                      '        self._xingli_mod_update_prompted = ""\n'
                      '        self._xingli_mod_update_check_thread = None\n'
                      '        self._xingli_mod_update_dialog = None', "controller state")
    s = replace_exact(s,
                      "QTimer.singleShot(3300, lambda: modpack_updater.maybe_auto_check(self))",
                      "QTimer.singleShot(3300, lambda: modpack_updater.maybe_auto_check(self))\n"
                      "        QTimer.singleShot(4400, lambda: mod_update_ui.maybe_auto_check(self))",
                      "MO2 startup hook")
    # Accessible to standard (non-advanced) MO2 users from Xingli's home UI.
    s = replace_exact(s, "daily_grid.addWidget(self.update_button, 1, 1)",
                      "daily_grid.addWidget(self.update_button, 1, 1)\n\n"
                      "        mod_updates_button = QtWidgets.QPushButton(\"📦  单独 MOD 更新\")\n"
                      "        mod_updates_button.setToolTip(\"查看并安装来自星黎镜像/R2 的指定 MOD 更新。\")\n"
                      "        mod_updates_button.clicked.connect(self.show_mod_updates)\n"
                      "        daily_grid.addWidget(mod_updates_button, 2, 0)",
                      "home button")
    s = replace_exact(s, "    def show_auto_updater(self):",
                      "    def show_mod_updates(self):\n"
                      "        \"\"\"打开独立 MOD 更新中心，不更改整个整合包版本。\"\"\"\n"
                      "        return mod_update_ui.show_update_center(self)\n\n"
                      "    def show_auto_updater(self):",
                      "open update center")
    files[controller_key] = s.encode("utf-8")
    files[PREFIX + "version.ini"] = b"[Version]\nlocal = 2.4.0\n"
    for source in ("mod_update_core.py", "mod_update_ui.py", "mod_update_sources.json"):
        path = ROOT / "development/v2.4.0" / source
        if not path.is_file():
            raise RuntimeError("Missing MOD update integration source: " + source)
        files[PREFIX + source] = path.read_bytes()
    files[PREFIX + "CHANGELOG_v2.4.0.md"] = (
        "# 星黎 MO2 小助手 v2.4.0\n\n"
        "- 新增独立 MOD 远程更新检查，不必更新整个整合包。\n"
        "- MO2 启动后自动检查 Gitee/GitHub 镜像清单，发现已安装 MOD 的更高版本时主动弹窗。\n"
        "- 新增单独 MOD 更新中心、镜像切换、HTTP Range 断点续传、SHA256 校验。\n"
        "- 支持作者在受信任的更新清单中声明 Cloudflare R2 自有域名，无需再次升级插件。\n"
        "- 安装前完整备份旧 MOD，使用 MO2 原生安装流程处理 FOMOD/覆盖确认。\n"
        "- 安装后的旧版备份不会自动删除；MO2 安装失败时保留备份供退出后安全恢复。\n"
        "- 本版不自动覆盖未知本地版本、非 MO2 管理的 MOD，不静默修改优先级。\n"
        "- MOD 更新清单初始为空。只有作者实际上传 MOD ZIP、验证公开 URL 并发布 MOD 清单后才会推送。\n"
    ).encode("utf-8")
    for path, content in files.items():
        if path.endswith(".py"):
            ast.parse(content.decode("utf-8-sig"), filename=path,
                      feature_version=(3, 8))
    assert b"mod_update_ui.maybe_auto_check(self)" in files[controller_key]
    assert b"mod_update_ui.show_update_center(self)" in files[controller_key]
    assert b"2.4.0" in files[PREFIX + "version.ini"]
    assert json.loads(files[PREFIX + "mod_update_sources.json"])["manifest_urls"][0].startswith("https://gitee.com/")
    catalog = json.loads((ROOT / "mods/latest.json").read_text(encoding="utf-8"))
    assert catalog["product"] == "xingli_mod_updates" and catalog["mods"] == []
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(TARGET, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in sorted(files):
            z.writestr(name, files[name])
    with zipfile.ZipFile(TARGET) as z:
        assert z.testzip() is None
        assert len(z.namelist()) == len(files)

    old = json.loads(LATEST.read_text(encoding="utf-8-sig"))
    assert old["version"] == "2.3.1", "Unexpected published source version"
    data = TARGET.read_bytes()
    release = TARGET.name
    old["version"] = "2.4.0"
    old["title"] = "星黎 MO2 小助手 v2.4.0 — 独立 MOD 更新"
    old["published_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    old["changelog"] = (
        "新增独立 MOD 更新功能：MO2 启动检测并主动通知，"
        "Cloudflare R2 自定义域名 + Gitee/GitHub 清单，"
        "断点续传、SHA256 校验和 MO2 安装前完整备份。"
        "MOD 清单初始为空，等待作者上传首个实际 MOD 更新包。"
    )
    old["package"] = {
        "filename": release,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "mirrors": [
            {"name": "Gitee 国内主源",
             "url": "https://gitee.com/HR-world/xingli/raw/main/update/releases/" + release},
            {"name": "GitHub 备用源",
             "url": "https://raw.githubusercontent.com/haoranzheng/xingli/main/update/releases/" + release},
        ],
    }
    LATEST.write_text(json.dumps(old, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("BUILD_240_PASS", len(files), len(data), old["package"]["sha256"])


if __name__ == "__main__":
    main()
