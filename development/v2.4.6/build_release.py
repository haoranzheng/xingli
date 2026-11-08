#!/usr/bin/env python3
# coding: utf-8
"""Build Xingli Assistant v2.4.6 from the published v2.4.5 runtime ZIP."""
import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "update/releases/xingli_Little_assistant_v2.4.5_update.zip"
OUTPUT = ROOT / "update/releases/xingli_Little_assistant_v2.4.6_update.zip"
LATEST = ROOT / "update/latest.json"
PREFIX = "xingli_Little_assistant/"


def change_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError("{}: expected one anchor, found {}".format(label, count))
    return text.replace(old, new, 1)


def replace_method(text, start, end, replacement, label):
    if text.count(start) != 1 or text.count(end) != 1:
        raise RuntimeError("{}: method anchors changed".format(label))
    begin = text.index(start)
    finish = text.index(end, begin)
    if finish <= begin:
        raise RuntimeError("{}: invalid method range".format(label))
    return text[:begin] + replacement.rstrip() + "\n\n" + text[finish:]


def patch_controller(source):
    source = change_once(source, 'DEFAULT_VERSION = "2.4.5"',
                         'DEFAULT_VERSION = "2.4.6"', "default plugin version")
    source = change_once(source, "version() 返回版本 2.4.5-user",
                         "version() 返回版本 2.4.6-user", "version diagnostics")
    source = change_once(source, "return mobase.VersionInfo(2, 4, 5)",
                         "return mobase.VersionInfo(2, 4, 6)", "MO2 plugin version")
    return source


def patch_modpack_updater(source):
    source = change_once(
        source,
        "from . import mainmenu_sync\n",
        "from . import mainmenu_sync\nfrom . import modpack_confirmation\n",
        "confirmation service import",
    )
    method = '''def prompt_update(controller, manifest: Optional[Dict[str, object]] = None, automatic: bool = False) -> bool:
    """Offer download or explicit confirmation that this MO2 instance already runs the release."""
    manifest = manifest or getattr(controller, "_modpack_update_manifest", None)
    if not isinstance(manifest, dict):
        return False
    local = load_local_release(controller)
    current = str(local.get("installed_version", "") or "")
    latest = str(manifest.get("version", "") or "")
    pack_name = str(manifest.get("name", local.get("name", "星黎整合包")) or "星黎整合包")
    title = str(manifest.get("title", "") or "").strip()
    changelog = str(manifest.get("changelog", "") or "").strip()
    parent = getattr(controller, "window", None)

    def button_role(name):
        roles = getattr(QtWidgets.QMessageBox, "ButtonRole", QtWidgets.QMessageBox)
        return getattr(roles, name)

    def execute(box):
        return box.exec() if hasattr(box, "exec") else box.exec_()

    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle("整合包有新版本")
    box.setIcon(QtWidgets.QMessageBox.Information)
    headline = "{} 已发布新版本 {}".format(pack_name, latest)
    if title:
        headline += "\\n{}".format(title)
    box.setText(headline)
    detail = "当前记录版本：{}\\n最新发布版本：{}".format(current or "未记录", latest)
    if changelog:
        detail += "\\n\\n更新内容：\\n{}".format(changelog)
    pages = manifest.get("download_pages", [])
    if isinstance(pages, list) and pages:
        detail += "\\n\\n下载方式："
        for item in pages:
            if isinstance(item, dict):
                name = str(item.get("name", "网盘") or "网盘")
                code = str(item.get("code", "") or "")
                detail += "\\n• {}{}".format(name, "（提取码：{}）".format(code) if code else "")
    detail += (
        "\\n\\n如果当前 MO2 已经安装并正在使用该版本，可以确认并修正本地版本记录。"
        "\\n仅下载了压缩包、尚未安装的用户，请选择“前往网盘”或“稍后”。"
        "\\n确认只修改当前 MO2 实例的版本记录，不会下载、解压、安装或覆盖任何 MOD。"
    )
    box.setInformativeText(detail)
    confirm_btn = box.addButton(
        "我已安装并正在使用此版本", button_role("AcceptRole"))
    open_btn = box.addButton("前往网盘", button_role("ActionRole"))
    box.addButton("稍后", button_role("RejectRole"))
    execute(box)
    clicked = box.clickedButton()

    if clicked is confirm_btn:
        verify = QtWidgets.QMessageBox(parent)
        verify.setWindowTitle("确认当前整合包版本")
        verify.setIcon(QtWidgets.QMessageBox.Warning)
        verify.setText(
            "请确认：当前打开的 MO2 实例已经安装并正在使用星黎整合包 {}。".format(latest))
        verify.setInformativeText(
            "继续后会把本实例的本地版本标记更新为 {}。"
            "\\n如果你只是下载了压缩包，或尚未切换到该整合包，请取消。".format(latest))
        yes_btn = verify.addButton(
            "确认并更新本地版本", button_role("AcceptRole"))
        verify.addButton("取消", button_role("RejectRole"))
        execute(verify)
        if verify.clickedButton() is not yes_btn:
            return False
        try:
            result = modpack_confirmation.confirm_installed_version(
                controller, manifest, local)
            confirmed = str(result.get("installed_version", latest))
            try:
                mainmenu_sync.sync(controller, confirmed, manifest)
            except Exception:
                logger.exception("确认整合包版本后同步主菜单信息失败")
            status = getattr(controller, "_xingli_modpack_update_status", {}) or {}
            _publish_home_status(
                controller, "up_to_date", installed=confirmed, latest=confirmed,
                source=str(status.get("source", "") or ""),
                message="已由用户确认当前 MO2 实例正在使用整合包 {}。".format(confirmed))
            controller._modpack_update_prompted_version = confirmed
            message = "当前 MO2 实例的本地整合包版本已更新为 {}。".format(confirmed)
            message += "\\n没有下载、安装、覆盖或修改任何 MOD 文件。"
            if result.get("warning"):
                message += "\\n\\n注意：" + str(result["warning"])
            QtWidgets.QMessageBox.information(parent, "版本记录已更新", message)
            return True
        except Exception as exc:
            logger.exception("用户确认整合包版本失败")
            QtWidgets.QMessageBox.warning(
                parent, "无法更新版本记录",
                "本地版本记录没有完成更新：\\n{}".format(exc))
            return False

    if clicked is open_btn:
        return _open_download_page(parent, manifest)
    return False
'''
    return replace_method(
        source,
        "def prompt_update(controller, manifest: Optional[Dict[str, object]] = None, automatic: bool = False) -> bool:\n",
        "def _start_check(controller, prompt_when_new: bool) -> None:\n",
        method,
        "modpack update prompt",
    )


def main():
    if not INPUT.is_file():
        raise RuntimeError("Published v2.4.5 baseline ZIP is missing: {}".format(INPUT))
    with zipfile.ZipFile(INPUT) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("v2.4.5 baseline ZIP is corrupt")
        files = {
            item.filename: archive.read(item)
            for item in archive.infolist() if not item.is_dir()
        }

    controller_name = PREFIX + "consolidation_controller.py"
    updater_name = PREFIX + "modpack_updater.py"
    for required in (controller_name, updater_name, PREFIX + "modpack_release.json"):
        if required not in files:
            raise RuntimeError("baseline runtime is missing " + required)

    files[controller_name] = patch_controller(
        files[controller_name].decode("utf-8-sig").replace("\\r\\n", "\\n")
    ).encode("utf-8")
    files[updater_name] = patch_modpack_updater(
        files[updater_name].decode("utf-8-sig").replace("\\r\\n", "\\n")
    ).encode("utf-8")

    confirmation_source = ROOT / "development/v2.4.6/modpack_confirmation.py"
    if not confirmation_source.is_file():
        raise RuntimeError("confirmation service source missing")
    files[PREFIX + "modpack_confirmation.py"] = confirmation_source.read_bytes()
    files[PREFIX + "version.ini"] = b"[Version]\\nlocal = 2.4.6\\n"
    files[PREFIX + "CHANGELOG_v2.4.6.md"] = (
        "# 星黎 MO2 小助手 v2.4.6：整合包版本确认\\n\\n"
        "- 新增“我已安装并正在使用此版本”确认入口。\\n"
        "- 确认后仅更新当前 MO2 实例的整合包版本记录，不下载或修改 MOD。\\n"
        "- 同步 MO2 根目录权威标记与插件持久记录，防止旧标记覆盖新版本。\\n"
        "- 保留插件内的首次启动 bootstrap 版本文件，避免更新助手时静默升级整合包版本。\\n"
        "- 支持任意后续整合包版本，不为 4.0 或单个版本写死逻辑。\\n"
        "- 仅接受同一 pack_id 且高于当前版本的有效远程版本；拒绝降级和无效版本。\\n"
    ).encode("utf-8")

    for name, body in files.items():
        if name.endswith(".py"):
            ast.parse(body.decode("utf-8-sig"), filename=name, feature_version=(3, 8))

    updater = files[updater_name].decode("utf-8")
    for token in (
        "我已安装并正在使用此版本",
        "确认并更新本地版本",
        "confirm_installed_version",
        "仅下载了压缩包",
    ):
        if token not in updater:
            raise RuntimeError("modpack confirmation UI missing: " + token)

    # Keep the bootstrap marker unchanged; installed content version is held in
    # MO2 root/pluginData markers and changes only after an explicit user action.
    bootstrap = json.loads(files[PREFIX + "modpack_release.json"].decode("utf-8-sig"))
    if bootstrap.get("installed_version") != "3.3.1":
        raise RuntimeError("unexpected bootstrap marker; refusing silent content-version change")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
    with zipfile.ZipFile(OUTPUT) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("v2.4.6 output ZIP is corrupt")

    old = json.loads(LATEST.read_text(encoding="utf-8-sig"))
    if old.get("version") != "2.4.5":
        raise RuntimeError("expected updater manifest baseline 2.4.5")
    blob = OUTPUT.read_bytes()
    old["version"] = "2.4.6"
    old["title"] = "星黎 MO2 小助手 2.4.6：整合包版本确认"
    old["published_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    old["changelog"] = (
        "新增整合包版本确认：用户确认当前 MO2 实例已安装并正在使用远程版本后，"
        "自动同步本地根目录/插件持久版本记录；仅下载未安装时可继续前往网盘，"
        "取消或稍后不会修改本地版本。适用于所有后续整合包版本。"
    )
    old["package"] = {
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
    LATEST.write_text(
        json.dumps(old, ensure_ascii=False, indent=2) + "\\n",
        encoding="utf-8",
    )
    print("BUILD_V246_PASS", len(blob), old["package"]["sha256"])


if __name__ == "__main__":
    main()
