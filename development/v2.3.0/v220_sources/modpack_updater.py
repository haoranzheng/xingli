# -*- coding: utf-8 -*-
"""Xingli modpack content update notifier.

This subsystem is deliberately separate from the assistant self-updater:
- it never downloads or modifies the modpack;
- it only compares the installed pack version with a mirrored remote manifest;
- when a newer pack is published it asks the user before opening the author's
  download page in the default browser.

Compatible with MO2 2.4.4 / Python 3.8 / PyQt5, with PyQt6 fallback support.
"""

import json
import os
import re
import urllib.request
import webbrowser
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

try:
    import PyQt6.QtWidgets as QtWidgets
    from PyQt6.QtCore import QThread, QTimer, pyqtSignal
except ImportError:
    import PyQt5.QtWidgets as QtWidgets
    from PyQt5.QtCore import QThread, QTimer, pyqtSignal

from . import stability
from . import mainmenu_sync

logger = stability.get_logger("modpack_updater")

PRODUCT_ID = "xingli_modpack"
LOCAL_RELEASE_FILE = "modpack_release.json"
ROOT_RELEASE_FILE = "xingli_modpack_release.json"
PERSISTENT_RELEASE_FILE = "installed_release.json"
SOURCES_FILE = "modpack_update_sources.json"
DEFAULT_TIMEOUT_SECONDS = 8
ALLOWED_MANIFEST_HOSTS = {"gitee.com", "raw.githubusercontent.com", "gitcode.com"}
ALLOWED_DOWNLOAD_HOSTS = {"pan.quark.cn", "pan.baidu.com"}
USER_AGENT = "Xingli-Modpack-Update-Notifier/2.0.4"


def normalize_version(version: str) -> Tuple[int, ...]:
    nums = re.findall(r"\d+", str(version or ""))
    return tuple(int(x) for x in nums[:6]) if nums else (0,)


def compare_versions(left: str, right: str) -> int:
    a = list(normalize_version(left))
    b = list(normalize_version(right))
    width = max(len(a), len(b))
    a += [0] * (width - len(a))
    b += [0] * (width - len(b))
    return (a > b) - (a < b)


def _plugin_path(controller) -> str:
    return os.path.abspath(getattr(controller, "plugin_path", os.path.dirname(__file__)))


def _base_path(controller) -> str:
    try:
        organizer = getattr(controller, "organizer", None)
        if organizer:
            value = organizer.basePath()
            if value:
                return os.path.abspath(str(value))
    except Exception:
        pass
    return os.path.abspath(os.path.join(_plugin_path(controller), "..", ".."))


def _plugin_data_modpack_dir(controller) -> str:
    organizer = getattr(controller, "organizer", None)
    if organizer:
        try:
            root = str(organizer.pluginDataPath() or "")
            if root:
                return os.path.join(root, "xingli_assistant", "modpack")
        except Exception:
            pass
    return os.path.join(_plugin_path(controller), "data", "modpack")


def _read_json(path: str) -> Optional[Dict[str, object]]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except Exception:
        logger.exception("读取 JSON 失败: %s", path)
        return None


def _write_json_atomic(path: str, data: Dict[str, object]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temp, path)


def load_local_release(controller) -> Dict[str, str]:
    """Return installed modpack identity/version without assistant-update drift.

    Priority:
      1. MO2-root xingli_modpack_release.json shipped by a real modpack update;
      2. persistent pluginData installed_release.json;
      3. plugin-bundled bootstrap modpack_release.json (copied once to pluginData);
      4. health baseline as a legacy fallback.

    The persistent copy is crucial: updating Xingli Assistant itself must never
    silently change the user's installed *modpack content* version.
    """
    persistent_dir = _plugin_data_modpack_dir(controller)
    persistent_path = os.path.join(persistent_dir, PERSISTENT_RELEASE_FILE)
    root_path = os.path.join(_base_path(controller), ROOT_RELEASE_FILE)
    bootstrap_path = os.path.join(_plugin_path(controller), LOCAL_RELEASE_FILE)

    root_release = _read_json(root_path) or {}
    persistent = _read_json(persistent_path) or {}
    bootstrap = _read_json(bootstrap_path) or {}

    # A modpack package update owns the root marker. When present, mirror it into
    # pluginData so future assistant updates keep the same installed version.
    source = root_release if str(root_release.get("installed_version", "") or "").strip() else persistent
    if source is root_release and source:
        try:
            _write_json_atomic(persistent_path, source)
        except Exception:
            logger.exception("持久化整合包版本失败")

    # First v2.0.3 migration for the current 3.3.1 distribution: seed once only.
    if not str(source.get("installed_version", "") or "").strip():
        bootstrap_version = str(bootstrap.get("installed_version", "") or "").strip()
        if bootstrap_version:
            source = dict(bootstrap)
            try:
                _write_json_atomic(persistent_path, source)
            except Exception:
                logger.exception("初始化整合包版本记录失败")

    pack_id = str(source.get("pack_id", bootstrap.get("pack_id", "xingli-skyrim")) or "xingli-skyrim").strip()
    name = str(source.get("name", bootstrap.get("name", "星黎整合包")) or "星黎整合包").strip()
    version = str(source.get("installed_version", "") or "").strip()

    if not version:
        baseline = _read_json(os.path.join(persistent_dir, "xingli_modpack_manifest.json")) or {}
        baseline_version = str(baseline.get("pack_version", "") or "").strip()
        if baseline_version:
            version = baseline_version
            name = str(baseline.get("pack_name", name) or name).strip()

    return {
        "pack_id": pack_id,
        "name": name,
        "installed_version": version,
    }


def _is_allowed_https(url: str, allowed_hosts) -> bool:
    value = str(url or "").strip()
    if not value.startswith("https://"):
        return False
    try:
        host = (urlparse(value).hostname or "").lower()
    except Exception:
        return False
    return host in allowed_hosts


def load_sources(controller) -> Dict[str, object]:
    data = _read_json(os.path.join(_plugin_path(controller), SOURCES_FILE)) or {}
    urls = []
    for raw in data.get("manifest_urls", []) if isinstance(data.get("manifest_urls"), list) else []:
        url = str(raw or "").strip()
        if _is_allowed_https(url, ALLOWED_MANIFEST_HOSTS):
            urls.append(url)
    try:
        timeout = int(data.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS)
    except Exception:
        timeout = DEFAULT_TIMEOUT_SECONDS
    return {"manifest_urls": urls, "timeout_seconds": max(3, min(30, timeout))}


def _source_name(url: str) -> str:
    value = str(url or "").lower()
    if "gitee.com" in value:
        return "Gitee 国内主源"
    if "github.com" in value or "githubusercontent.com" in value:
        return "GitHub 备用源"
    return "整合包更新源"


def validate_manifest(data: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(data, dict):
        raise RuntimeError("整合包更新清单格式无效。")
    if int(data.get("schema", 0) or 0) < 1:
        raise RuntimeError("整合包更新清单版本无效。")
    if str(data.get("product", "")) != PRODUCT_ID:
        raise RuntimeError("该更新清单不属于星黎整合包。")

    result = dict(data)
    enabled = bool(result.get("enabled", True))
    result["enabled"] = enabled
    if not enabled:
        return result

    pack_id = str(result.get("pack_id", "") or "").strip()
    version = str(result.get("version", "") or "").strip()
    if not pack_id:
        raise RuntimeError("整合包更新清单缺少 pack_id。")
    if not version or normalize_version(version) == (0,):
        raise RuntimeError("整合包更新清单缺少有效版本号。")

    pages = []
    raw_pages = result.get("download_pages", [])
    if isinstance(raw_pages, list):
        for item in raw_pages:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "") or "").strip()
            if not _is_allowed_https(url, ALLOWED_DOWNLOAD_HOSTS):
                continue
            pages.append({
                "name": str(item.get("name", "网盘") or "网盘").strip(),
                "url": url,
                "code": str(item.get("code", "") or "").strip(),
            })
    # Backward-compatible single-link schema.
    legacy = str(result.get("download_page", "") or "").strip()
    if not pages and _is_allowed_https(legacy, ALLOWED_DOWNLOAD_HOSTS):
        pages.append({"name": "网盘", "url": legacy, "code": str(result.get("extract_code", "") or "").strip()})
    if not pages:
        raise RuntimeError("整合包更新清单缺少有效网盘地址。")

    result["pack_id"] = pack_id
    result["version"] = version
    result["download_pages"] = pages
    result["download_page"] = pages[0]["url"]
    return result


def fetch_manifest(urls: List[str], timeout: int) -> Tuple[Dict[str, object], str]:
    if not urls:
        raise RuntimeError("作者尚未配置整合包更新源。")
    errors = []
    for url in urls:
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json,*/*"},
            )
            with urllib.request.urlopen(request, timeout=int(timeout)) as response:
                raw = response.read().decode("utf-8-sig")
            return validate_manifest(json.loads(raw)), url
        except Exception as exc:
            errors.append("{}: {}".format(_source_name(url), exc))
            logger.warning("整合包更新源失败 %s: %s", url, exc)
    raise RuntimeError("所有整合包更新源均不可用。\n" + "\n".join(errors[-2:]))


class ModpackCheckThread(QThread):
    checked = pyqtSignal(dict, str)
    failed = pyqtSignal(str)

    def __init__(self, urls: List[str], timeout: int):
        super().__init__()
        self.urls = list(urls)
        self.timeout = int(timeout)

    def run(self):
        try:
            manifest, source = fetch_manifest(self.urls, self.timeout)
            self.checked.emit(manifest, source)
        except Exception as exc:
            self.failed.emit(str(exc))


def _publish_home_status(controller, state: str, installed: str = "", latest: str = "",
                         source: str = "", message: str = "") -> None:
    payload = {
        "state": str(state or "unknown"),
        "installed": str(installed or ""),
        "latest": str(latest or ""),
        "source": str(source or ""),
        "message": str(message or ""),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    controller._xingli_modpack_update_status = payload
    callback = getattr(controller, "_set_modpack_update_home_status", None)
    if callable(callback):
        try:
            callback(payload)
        except Exception:
            logger.exception("更新整合包主页状态失败")


def _open_url(parent, url: str) -> bool:
    if not _is_allowed_https(url, ALLOWED_DOWNLOAD_HOSTS):
        QtWidgets.QMessageBox.warning(parent, "整合包更新", "网盘地址未通过安全检查。")
        return False
    try:
        ok = webbrowser.open_new_tab(url)
        if ok is False:
            raise RuntimeError("系统没有接受打开网页请求")
        return True
    except Exception as exc:
        logger.exception("打开整合包网盘失败")
        QtWidgets.QMessageBox.warning(parent, "无法打开网页", "无法打开网盘页面：\n{}".format(exc))
        return False


def _open_download_page(parent, manifest: Dict[str, object]) -> bool:
    pages = manifest.get("download_pages", [])
    if not isinstance(pages, list) or not pages:
        url = str(manifest.get("download_page", "") or "").strip()
        return _open_url(parent, url)
    if len(pages) == 1:
        item = pages[0] if isinstance(pages[0], dict) else {}
        return _open_url(parent, str(item.get("url", "") or "").strip())

    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle("选择下载网盘")
    box.setIcon(QtWidgets.QMessageBox.Information)
    detail = ["请选择要打开的网盘："]
    buttons = []
    for item in pages:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "网盘") or "网盘").strip()
        code = str(item.get("code", "") or "").strip()
        detail.append("{}{}".format(name, "　提取码：" + code if code else ""))
        btn = box.addButton("打开{}".format(name), QtWidgets.QMessageBox.AcceptRole)
        buttons.append((btn, item))
    box.setText("整合包下载地址")
    box.setInformativeText("\n".join(detail))
    box.addButton("取消", QtWidgets.QMessageBox.RejectRole)
    if hasattr(box, "exec"):
        box.exec()
    else:
        box.exec_()
    clicked = box.clickedButton()
    for btn, item in buttons:
        if clicked is btn:
            code = str(item.get("code", "") or "").strip()
            if code:
                try:
                    QtWidgets.QApplication.clipboard().setText(code)
                except Exception:
                    pass
            return _open_url(parent, str(item.get("url", "") or "").strip())
    return False


def prompt_update(controller, manifest: Optional[Dict[str, object]] = None, automatic: bool = False) -> bool:
    manifest = manifest or getattr(controller, "_modpack_update_manifest", None)
    if not isinstance(manifest, dict):
        return False
    local = load_local_release(controller)
    current = str(local.get("installed_version", "") or "")
    latest = str(manifest.get("version", "") or "")
    pack_name = str(manifest.get("name", local.get("name", "星黎整合包")) or "星黎整合包")
    title = str(manifest.get("title", "") or "").strip()
    changelog = str(manifest.get("changelog", "") or "").strip()

    box = QtWidgets.QMessageBox(getattr(controller, "window", None))
    box.setWindowTitle("整合包有新版本")
    box.setIcon(QtWidgets.QMessageBox.Information)
    headline = "{} 已发布新版本 {}".format(pack_name, latest)
    if title:
        headline += "\n{}".format(title)
    box.setText(headline)
    detail = "当前版本：{}\n最新版本：{}".format(current or "未记录", latest)
    if changelog:
        detail += "\n\n更新内容：\n{}".format(changelog)
    pages = manifest.get("download_pages", [])
    if isinstance(pages, list) and pages:
        detail += "\n\n下载方式："
        for item in pages:
            if isinstance(item, dict):
                name = str(item.get("name", "网盘") or "网盘")
                code = str(item.get("code", "") or "")
                detail += "\n• {}{}".format(name, "（提取码：{}）".format(code) if code else "")
    detail += "\n\n是否选择网盘并打开下载页面？"
    box.setInformativeText(detail)
    open_btn = box.addButton("前往网盘", QtWidgets.QMessageBox.AcceptRole)
    box.addButton("稍后", QtWidgets.QMessageBox.RejectRole)
    if hasattr(box, "exec"):
        box.exec()
    else:
        box.exec_()
    if box.clickedButton() is open_btn:
        return _open_download_page(getattr(controller, "window", None), manifest)
    return False


def _start_check(controller, prompt_when_new: bool) -> None:
    local = load_local_release(controller)
    installed = str(local.get("installed_version", "") or "").strip()
    if not installed:
        _publish_home_status(
            controller,
            "unconfigured",
            message="尚未记录当前整合包版本。作者可在 modpack_release.json 中设置，或先建立整合包基准。",
        )
        return

    sources = load_sources(controller)
    urls = list(sources.get("manifest_urls", []))
    if not urls:
        _publish_home_status(controller, "unconfigured", installed=installed, message="作者尚未配置整合包更新源。")
        return

    _publish_home_status(controller, "checking", installed=installed)
    thread = ModpackCheckThread(urls, int(sources.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)))
    controller._modpack_update_thread = thread

    def on_checked(manifest, source):
        controller._modpack_update_thread = None
        source_name = _source_name(source)
        if not bool(manifest.get("enabled", True)):
            _publish_home_status(controller, "disabled", installed=installed, source=source_name,
                                 message=str(manifest.get("message", "整合包更新提示尚未启用。") or "整合包更新提示尚未启用。"))
            return
        remote_pack_id = str(manifest.get("pack_id", "") or "")
        if remote_pack_id != str(local.get("pack_id", "") or ""):
            _publish_home_status(controller, "error", installed=installed, source=source_name,
                                 message="远程整合包标识与本机不一致。")
            return
        latest = str(manifest.get("version", "") or "")
        controller._modpack_update_manifest = dict(manifest)
        try:
            mainmenu_sync.sync(controller, installed, manifest)
        except Exception:
            logger.exception("同步游戏主菜单整合包版本失败")
        if compare_versions(latest, installed) > 0:
            _publish_home_status(controller, "update_available", installed=installed, latest=latest, source=source_name)
            if prompt_when_new:
                prompted = str(getattr(controller, "_modpack_update_prompted_version", "") or "")
                if prompted != latest:
                    controller._modpack_update_prompted_version = latest
                    QTimer.singleShot(250, lambda: prompt_update(controller, manifest, automatic=True))
        else:
            _publish_home_status(controller, "up_to_date", installed=installed, latest=latest, source=source_name)

    def on_failed(message):
        controller._modpack_update_thread = None
        _publish_home_status(controller, "error", installed=installed, message=str(message))

    thread.checked.connect(on_checked)
    thread.failed.connect(on_failed)
    thread.start()


def maybe_auto_check(controller) -> None:
    _start_check(controller, prompt_when_new=True)


def manual_check(controller, prompt_when_new: bool = True) -> None:
    _start_check(controller, prompt_when_new=bool(prompt_when_new))


def handle_home_action(controller) -> None:
    status = getattr(controller, "_xingli_modpack_update_status", {}) or {}
    if str(status.get("state", "")) == "update_available" and isinstance(getattr(controller, "_modpack_update_manifest", None), dict):
        prompt_update(controller)
        return
    manual_check(controller)
