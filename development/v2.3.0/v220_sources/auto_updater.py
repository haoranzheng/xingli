# coding=utf-8
"""Xingli Assistant update subsystem.

Design goals for MO2 2.4.4 / Python 3.8:
- one update implementation only;
- Gitee -> GitCode -> GitHub mirror failover;
- no third-party Python dependency;
- HTTPS certificate verification stays enabled;
- mandatory SHA-256 and ZIP integrity verification;
- never overwrite a loaded plugin in-process;
- stage first, apply after MO2 exits, atomic directory swap + rollback.
"""

import configparser
import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

try:
    import PyQt6.QtWidgets as QtWidgets
    import PyQt6.QtGui as QtGui
    from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
except ImportError:
    import PyQt5.QtWidgets as QtWidgets
    import PyQt5.QtGui as QtGui
    from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal

from . import stability

logger = stability.get_logger("updater")

SECTION = "AutoUpdate"
PRODUCT_ID = "xingli_Little_assistant"
SOURCES_FILE = "update_sources.json"
DEFAULT_TIMEOUT_SECONDS = 8
USER_AGENT = "Xingli-MO2-Assistant-Updater/2.0.6"
PLACEHOLDER_MARKERS = ("YOUR_", "<owner>", "<repo>", "example.com")
UPDATE_BACKUP_LIMIT = 1
RUNTIME_ARTIFACT_DIRS = {"logs", "__pycache__", "_XingliUpdates"}
RUNTIME_ARTIFACT_SUFFIXES = (".log", ".download", ".pyc")



def _exec_dialog(dialog):
    if hasattr(dialog, "exec"):
        return dialog.exec()
    return dialog.exec_()


def _align_center():
    if hasattr(Qt, "AlignmentFlag"):
        return Qt.AlignmentFlag.AlignCenter
    return Qt.AlignCenter


def _question(parent, title, text):
    buttons = (
        QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
        if hasattr(QtWidgets.QMessageBox, "StandardButton")
        else QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
    )
    yes = QtWidgets.QMessageBox.StandardButton.Yes if hasattr(QtWidgets.QMessageBox, "StandardButton") else QtWidgets.QMessageBox.Yes
    return QtWidgets.QMessageBox.question(parent, title, text, buttons) == yes


def _request_graceful_mo2_shutdown() -> bool:
    """Ask the MO2 main window to close normally. Never terminates the process."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return False
    try:
        for widget in list(app.topLevelWidgets()):
            try:
                if isinstance(widget, QtWidgets.QMainWindow):
                    title = str(widget.windowTitle() or "").lower()
                    if "mod organizer" in title:
                        return bool(widget.close())
            except Exception:
                continue
        # Fallback: close all top-level windows through Qt close events.
        app.closeAllWindows()
        return True
    except Exception:
        logger.exception("请求 MO2 正常关闭失败")
        return False


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


def settings_path(controller) -> str:
    return getattr(controller, "settings_path", os.path.join(_plugin_path(controller), "settings.ini"))


def sources_path(controller) -> str:
    return os.path.join(_plugin_path(controller), SOURCES_FILE)


def updates_dir(controller) -> str:
    path = os.path.join(_base_path(controller), "_XingliUpdates")
    os.makedirs(path, exist_ok=True)
    return path


def _plugin_logs_dir(controller) -> str:
    organizer = getattr(controller, "organizer", None)
    if organizer is not None:
        try:
            base = str(organizer.pluginDataPath())
            if base:
                return os.path.join(base, "xingli_assistant", "logs")
        except Exception:
            pass
    return os.path.join(_plugin_path(controller), "logs")


def _version_from_update_filename(filename: str) -> str:
    match = re.search(r"_v([0-9]+(?:\.[0-9]+){1,5})_update\.zip$", str(filename or ""), re.I)
    return match.group(1) if match else ""


def _prune_backup_dirs(root: str, keep: int = UPDATE_BACKUP_LIMIT) -> None:
    if not os.path.isdir(root):
        return
    entries = []
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if os.path.isdir(path):
            try:
                stamp = os.path.getmtime(path)
            except Exception:
                stamp = 0
            entries.append((stamp, path))
    entries.sort(reverse=True)
    for _stamp, path in entries[max(0, int(keep)):]:
        shutil.rmtree(path, ignore_errors=True)


def cleanup_update_workspace(controller) -> None:
    """Prune obsolete updater artifacts without deleting a future package awaiting install."""
    root = updates_dir(controller)
    local_version = str(getattr(controller, "local_version", "") or "0.0.0")
    pending_path = os.path.join(root, "pending_update.json")
    pending = {}
    if os.path.isfile(pending_path):
        try:
            with open(pending_path, "r", encoding="utf-8-sig") as handle:
                pending = json.load(handle)
        except Exception:
            pending = {}
    pending_version = str(pending.get("version", "") or "") if isinstance(pending, dict) else ""
    pending_package = os.path.abspath(str(pending.get("package", "") or "")) if isinstance(pending, dict) else ""
    pending_is_future = bool(pending_version and compare_versions(pending_version, local_version) > 0)

    try:
        for name in list(os.listdir(root)):
            path = os.path.join(root, name)
            lower = name.lower()
            if os.path.isfile(path) and lower.endswith(".download"):
                try:
                    os.remove(path)
                except Exception:
                    pass
            elif os.path.isfile(path) and lower.endswith(".zip"):
                preserve = pending_is_future and pending_package and os.path.abspath(path) == pending_package
                if not preserve:
                    try:
                        os.remove(path)
                    except Exception:
                        pass
            elif os.path.isfile(path) and (
                (lower.startswith("install_xingli_update_") and lower.endswith(".ps1"))
                or (lower.startswith("安装星黎更新_") and lower.endswith(".bat"))
                or (lower.startswith("allow_auto_close_") and lower.endswith(".flag"))
            ):
                safe_pending = re.sub(r"[^0-9A-Za-z_.-]+", "_", pending_version) if pending_version else ""
                preserve = pending_is_future and safe_pending and safe_pending.lower() in lower
                if not preserve:
                    try:
                        os.remove(path)
                    except Exception:
                        pass
    except Exception:
        logger.exception("清理更新工作目录失败")

    staged_root = os.path.join(root, "staged")
    if os.path.isdir(staged_root):
        for name in list(os.listdir(staged_root)):
            path = os.path.join(staged_root, name)
            safe_pending = re.sub(r"[^0-9A-Za-z_.-]+", "_", pending_version) if pending_version else ""
            preserve = pending_is_future and safe_pending and name == safe_pending
            if not preserve:
                shutil.rmtree(path, ignore_errors=True)

    if os.path.isfile(pending_path) and not pending_is_future:
        try:
            os.remove(pending_path)
        except Exception:
            pass
    _prune_backup_dirs(os.path.join(root, "backups"), UPDATE_BACKUP_LIMIT)


def _archive_member_is_runtime_artifact(name: str) -> bool:
    normalized = str(name or "").replace("\\", "/").strip("/")
    if not normalized:
        return False
    parts = [part for part in normalized.split("/") if part]
    lower_parts = [part.lower() for part in parts]
    runtime_dirs = {x.lower() for x in RUNTIME_ARTIFACT_DIRS}
    if any(part in runtime_dirs for part in lower_parts):
        return True
    filename = lower_parts[-1] if lower_parts else ""
    if filename.endswith(RUNTIME_ARTIFACT_SUFFIXES) or ".log." in filename:
        return True
    if filename == "pending_update.json":
        return True
    return False


def _validate_update_archive_hygiene(archive: zipfile.ZipFile) -> None:
    leaked = [item.filename for item in archive.infolist() if _archive_member_is_runtime_artifact(item.filename)]
    if leaked:
        raise RuntimeError("更新包包含不应发布的运行缓存或日志：{}".format(", ".join(leaked[:5])))


def _read_ini(path: str) -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    if os.path.exists(path):
        try:
            config.read(path, encoding="utf-8")
        except Exception:
            config = configparser.ConfigParser()
    if SECTION not in config:
        config[SECTION] = {}
    return config


def _write_ini(path: str, config: configparser.ConfigParser) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        config.write(handle)


def get_update_settings(controller) -> Dict[str, str]:
    config = _read_ini(settings_path(controller))
    section = config[SECTION]
    defaults = {
        "AutoCheckOnStartup": "true",
        "LastCheck": "",
        "SkippedVersion": "",
    }
    for key, value in defaults.items():
        if not section.get(key):
            section[key] = value
    # Old builds stored CheckIntervalHours. It is intentionally ignored from v2.0.0.
    return {k: section.get(k, "") for k in section.keys()}


def set_update_settings(controller, auto_check: bool,
                        last_check: Optional[str] = None, skipped_version: Optional[str] = None) -> None:
    path = settings_path(controller)
    config = _read_ini(path)
    config[SECTION]["AutoCheckOnStartup"] = "true" if auto_check else "false"
    # Remove the legacy interval setting so the UI and configuration stay consistent.
    config[SECTION].pop("CheckIntervalHours", None)
    if last_check is not None:
        config[SECTION]["LastCheck"] = last_check
    if skipped_version is not None:
        config[SECTION]["SkippedVersion"] = skipped_version
    _write_ini(path, config)


def _publish_home_status(controller, state: str, latest: str = "", source: str = "", message: str = "") -> None:
    payload = {
        "state": str(state or "unknown"),
        "latest": str(latest or ""),
        "source": str(source or ""),
        "message": str(message or ""),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        controller._xingli_update_status = payload
    except Exception:
        pass
    try:
        callback = getattr(controller, "_set_update_home_status", None)
        if callable(callback):
            callback(payload)
    except Exception:
        logger.exception("更新主页状态失败")


ALLOWED_UPDATE_HOSTS = {"gitee.com", "raw.githubusercontent.com", "gitcode.com"}

def _valid_remote_url(url: str) -> bool:
    value = str(url or "").strip()
    if not value:
        return False
    if any(marker.lower() in value.lower() for marker in PLACEHOLDER_MARKERS):
        return False
    if value.startswith("file://"):
        return os.environ.get("XINGLI_UPDATE_DEV_MODE", "") == "1"
    if not value.startswith("https://"):
        return False
    try:
        host = (urlparse(value).hostname or "").lower()
    except Exception:
        return False
    return host in ALLOWED_UPDATE_HOSTS


def load_update_sources(controller) -> Dict[str, object]:
    path = sources_path(controller)
    if not os.path.exists(path):
        return {"schema": 1, "timeout_seconds": DEFAULT_TIMEOUT_SECONDS, "manifest_urls": []}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        urls = [str(x).strip() for x in data.get("manifest_urls", []) if _valid_remote_url(str(x))]
        timeout = int(data.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS)
        return {
            "schema": int(data.get("schema", 1) or 1),
            "timeout_seconds": max(3, min(30, timeout)),
            "manifest_urls": urls,
        }
    except Exception as exc:
        logger.exception("读取更新源失败")
        raise RuntimeError("更新源配置损坏：{}".format(exc))


def _request_bytes(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def _source_name(url: str) -> str:
    low = url.lower()
    if "gitee.com" in low:
        return "Gitee 国内主源"
    if "gitcode.com" in low:
        return "GitCode 国内备用源"
    if "github.com" in low or "githubusercontent.com" in low:
        return "GitHub 备用源"
    if low.startswith("file://"):
        return "本地测试源"
    return "备用更新源"


def validate_manifest(data: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(data, dict):
        raise RuntimeError("更新清单不是 JSON 对象。")
    schema = int(data.get("schema", 0) or 0)
    if schema < 2:
        raise RuntimeError("更新清单版本过旧，需要 schema 2 或更高。")
    if str(data.get("product", "")) != PRODUCT_ID:
        raise RuntimeError("更新清单不属于星黎助手。")
    version = str(data.get("version", "")).strip()
    if not version or normalize_version(version) == (0,):
        raise RuntimeError("更新清单缺少有效版本号。")
    package = data.get("package")
    if not isinstance(package, dict):
        raise RuntimeError("更新清单缺少 package。")
    sha256 = str(package.get("sha256", "")).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise RuntimeError("更新包必须提供有效 SHA256。")
    mirrors = package.get("mirrors")
    if not isinstance(mirrors, list) or not mirrors:
        raise RuntimeError("更新清单没有可用下载源。")
    usable = []
    for item in mirrors:
        if isinstance(item, str):
            url = item
            name = _source_name(url)
        elif isinstance(item, dict):
            url = str(item.get("url", ""))
            name = str(item.get("name", "") or _source_name(url))
        else:
            continue
        if _valid_remote_url(url):
            usable.append({"name": name, "url": url})
    if not usable:
        raise RuntimeError("更新清单中的下载源都不可用。")
    package = dict(package)
    package["sha256"] = sha256
    package["mirrors"] = usable
    result = dict(data)
    result["package"] = package
    return result


def fetch_manifest_from_mirrors(urls: List[str], timeout: int) -> Tuple[Dict[str, object], str]:
    if not urls:
        raise RuntimeError("作者尚未配置更新源。")
    errors = []
    for url in urls:
        try:
            raw = _request_bytes(url, timeout).decode("utf-8-sig")
            data = validate_manifest(json.loads(raw))
            return data, url
        except Exception as exc:
            errors.append("{}: {}".format(_source_name(url), exc))
            logger.warning("更新清单源失败 %s: %s", url, exc)
    raise RuntimeError("所有更新源均不可用。\n" + "\n".join(errors[-3:]))


class ManifestCheckThread(QThread):
    checked = pyqtSignal(dict, str)
    failed = pyqtSignal(str)

    def __init__(self, urls: List[str], timeout: int):
        super().__init__()
        self.urls = list(urls)
        self.timeout = int(timeout)

    def run(self):
        try:
            manifest, source = fetch_manifest_from_mirrors(self.urls, self.timeout)
            self.checked.emit(manifest, source)
        except Exception as exc:
            self.failed.emit(str(exc))


class MirrorDownloadThread(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(str, str)
    failed = pyqtSignal(str)

    def __init__(self, mirrors: List[Dict[str, str]], target_path: str, expected_sha256: str,
                 expected_size: int = 0, timeout: int = 15):
        super().__init__()
        self.mirrors = list(mirrors)
        self.target_path = target_path
        self.expected_sha256 = expected_sha256.lower()
        self.expected_size = int(expected_size or 0)
        self.timeout = int(timeout)

    def run(self):
        errors = []
        for mirror in self.mirrors:
            if self.isInterruptionRequested():
                self.failed.emit("用户已取消下载。")
                return
            name = str(mirror.get("name", "更新源"))
            url = str(mirror.get("url", ""))
            temp_path = None
            try:
                self.progress.emit(0, "正在连接 {}…".format(name))
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/zip,*/*"})
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    total = int(response.headers.get("Content-Length", 0) or 0)
                    fd, temp_path = tempfile.mkstemp(prefix="xingli_update_", suffix=".download")
                    os.close(fd)
                    sha = hashlib.sha256()
                    done = 0
                    with open(temp_path, "wb") as handle:
                        while True:
                            if self.isInterruptionRequested():
                                raise InterruptedError("用户已取消下载。")
                            chunk = response.read(256 * 1024)
                            if not chunk:
                                break
                            handle.write(chunk)
                            sha.update(chunk)
                            done += len(chunk)
                            if total > 0:
                                self.progress.emit(max(0, min(99, int(done * 100 / total))), "正在从 {} 下载…".format(name))
                if self.expected_size and done != self.expected_size:
                    raise RuntimeError("文件大小不一致（{} / {} 字节）".format(done, self.expected_size))
                digest = sha.hexdigest().lower()
                if digest != self.expected_sha256:
                    raise RuntimeError("SHA256 校验失败")
                if not zipfile.is_zipfile(temp_path):
                    raise RuntimeError("下载内容不是有效 ZIP")
                with zipfile.ZipFile(temp_path, "r") as archive:
                    bad = archive.testzip()
                    if bad:
                        raise RuntimeError("ZIP 校验失败：{}".format(bad))
                os.makedirs(os.path.dirname(self.target_path), exist_ok=True)
                if os.path.exists(self.target_path):
                    os.remove(self.target_path)
                shutil.move(temp_path, self.target_path)
                temp_path = None
                self.progress.emit(100, "下载完成")
                self.finished.emit(self.target_path, name)
                return
            except InterruptedError:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
                self.failed.emit("用户已取消下载。")
                return
            except Exception as exc:
                errors.append("{}: {}".format(name, exc))
                logger.warning("更新下载源失败 %s %s: %s", name, url, exc)
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
        self.failed.emit("所有下载源均失败。\n" + "\n".join(errors[-3:]))


def _safe_extract_zip(archive: zipfile.ZipFile, target_dir: str) -> None:
    root = os.path.abspath(target_dir)
    prefix = root + os.sep
    for member in archive.infolist():
        path = os.path.abspath(os.path.join(root, member.filename))
        if path != root and not path.startswith(prefix):
            raise RuntimeError("更新包包含异常路径：{}".format(member.filename))
    archive.extractall(root)


def _find_plugin_source(stage: str) -> str:
    direct = os.path.join(stage, PRODUCT_ID)
    if os.path.isfile(os.path.join(direct, "__init__.py")):
        return direct
    if os.path.isfile(os.path.join(stage, "__init__.py")):
        return stage
    for root, dirs, files in os.walk(stage):
        if os.path.basename(root) == PRODUCT_ID and "__init__.py" in files:
            return root
    raise RuntimeError("更新包里没有找到星黎助手插件目录。")


def _read_staged_version(source_dir: str) -> str:
    path = os.path.join(source_dir, "version.ini")
    config = configparser.ConfigParser()
    if not os.path.exists(path):
        raise RuntimeError("更新包缺少 version.ini。")
    config.read(path, encoding="utf-8")
    value = config.get("Version", "local", fallback="").strip()
    if not value:
        raise RuntimeError("更新包版本信息无效。")
    return value


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def prepare_staged_update(controller, package_path: str, manifest: Dict[str, object]) -> Dict[str, str]:
    root = updates_dir(controller)
    version = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(manifest.get("version", "update")))
    stage = os.path.join(root, "staged", version)
    if os.path.isdir(stage):
        shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage, exist_ok=True)
    with zipfile.ZipFile(package_path, "r") as archive:
        _validate_update_archive_hygiene(archive)
        _safe_extract_zip(archive, stage)
    source = _find_plugin_source(stage)
    staged_version = _read_staged_version(source)
    expected_version = str(manifest.get("version", ""))
    if compare_versions(staged_version, expected_version) != 0:
        raise RuntimeError("更新包版本 {} 与清单版本 {} 不一致。".format(staged_version, expected_version))

    plugin_dir = _plugin_path(controller)
    mo2_dir = _base_path(controller)
    mo2_exe = os.path.join(mo2_dir, "ModOrganizer.exe")
    pending = {
        "schema": 1,
        "product": PRODUCT_ID,
        "version": expected_version,
        "prepared_at": datetime.now().isoformat(timespec="seconds"),
        "source_dir": source,
        "plugin_dir": plugin_dir,
        "package": package_path,
    }
    pending_path = os.path.join(root, "pending_update.json")
    with open(pending_path, "w", encoding="utf-8") as handle:
        json.dump(pending, handle, ensure_ascii=False, indent=2)

    ps1_path = os.path.join(root, "Install_Xingli_Update_{}.ps1".format(version))
    bat_path = os.path.join(root, "安装星黎更新_{}.bat".format(version))
    result_path = os.path.join(root, "last_update_result.json")
    auto_close_flag = os.path.join(root, "allow_auto_close_{}.flag".format(version))
    backups = os.path.join(root, "backups")
    logs_dir = _plugin_logs_dir(controller)
    fallback_logs_dir = os.path.join(plugin_dir, "logs")

    ps1 = r'''$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$PluginDir = {plugin_dir}
$SourceDir = {source_dir}
$Mo2Exe = {mo2_exe}
$BackupRoot = {backup_root}
$ResultPath = {result_path}
$Version = {version}
$AutoCloseFlag = {auto_close_flag}
$PackagePath = {package_path}
$StageDir = {stage_dir}
$PendingPath = {pending_path}
$UpdateRoot = {update_root}
$LogDir = {log_dir}
$FallbackLogDir = {fallback_log_dir}

Write-Host '========================================'
Write-Host '        星黎助手安全更新'
Write-Host '========================================'
Write-Host '更新已准备完成。'
Write-Host '如果由星黎助手发起更新且 MO2 当前空闲，将请求 MO2 正常关闭。'
Write-Host '安装器只会请求 MO2 正常退出，不会强制结束进程。'
Write-Host ''

if (Test-Path -LiteralPath $AutoCloseFlag) {{
    try {{
        Remove-Item -LiteralPath $AutoCloseFlag -Force -ErrorAction SilentlyContinue
        $mo = Get-Process -Name 'ModOrganizer' -ErrorAction SilentlyContinue
        if ($mo) {{
            foreach ($p in @($mo)) {{
                try {{ [void]$p.CloseMainWindow() }} catch {{}}
            }}
        }}
    }} catch {{}}
}}

while (Get-Process -Name 'ModOrganizer' -ErrorAction SilentlyContinue) {{ Start-Sleep -Seconds 1 }}
Start-Sleep -Milliseconds 800

$PluginsParent = Split-Path -Parent $PluginDir
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$BackupDir = Join-Path $BackupRoot ('xingli_Little_assistant_' + $Stamp)
$NewDir = Join-Path $PluginsParent 'xingli_Little_assistant.__new__'
$OldDir = Join-Path $PluginsParent 'xingli_Little_assistant.__old__'

function Write-Result([string]$status, [string]$message, [string]$backup) {{
    $obj = [ordered]@{{ schema = 1; status = $status; version = $Version; message = $message; backup = $backup; time = (Get-Date).ToString('s') }}
    $obj | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ResultPath -Encoding UTF8
}}

function Cleanup-AfterSuccess {{
    try {{ if (Test-Path -LiteralPath $PackagePath) {{ Remove-Item -LiteralPath $PackagePath -Force -ErrorAction SilentlyContinue }} }} catch {{}}
    try {{ if (Test-Path -LiteralPath $StageDir) {{ Remove-Item -LiteralPath $StageDir -Recurse -Force -ErrorAction SilentlyContinue }} }} catch {{}}
    try {{ if (Test-Path -LiteralPath $PendingPath) {{ Remove-Item -LiteralPath $PendingPath -Force -ErrorAction SilentlyContinue }} }} catch {{}}
    try {{ if (Test-Path -LiteralPath $LogDir) {{ Remove-Item -LiteralPath $LogDir -Recurse -Force -ErrorAction SilentlyContinue }} }} catch {{}}
    try {{ if (Test-Path -LiteralPath $FallbackLogDir) {{ Remove-Item -LiteralPath $FallbackLogDir -Recurse -Force -ErrorAction SilentlyContinue }} }} catch {{}}
    try {{
        Get-ChildItem -LiteralPath $UpdateRoot -File -ErrorAction SilentlyContinue | Where-Object {{ $_.Extension -in @('.zip', '.download') }} | Remove-Item -Force -ErrorAction SilentlyContinue
        $stagedRoot = Join-Path $UpdateRoot 'staged'
        if (Test-Path -LiteralPath $stagedRoot) {{ Remove-Item -LiteralPath $stagedRoot -Recurse -Force -ErrorAction SilentlyContinue }}
        Get-ChildItem -LiteralPath $UpdateRoot -File -ErrorAction SilentlyContinue | Where-Object {{ $_.Name -like 'allow_auto_close_*.flag' }} | Remove-Item -Force -ErrorAction SilentlyContinue
    }} catch {{}}
    try {{
        if (Test-Path -LiteralPath $BackupRoot) {{
            $old = @(Get-ChildItem -LiteralPath $BackupRoot -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -Skip 1)
            foreach ($item in $old) {{ Remove-Item -LiteralPath $item.FullName -Recurse -Force -ErrorAction SilentlyContinue }}
        }}
    }} catch {{}}
}}

try {{
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    if (Test-Path -LiteralPath $NewDir) {{ Remove-Item -LiteralPath $NewDir -Recurse -Force }}
    if (Test-Path -LiteralPath $OldDir) {{ Remove-Item -LiteralPath $OldDir -Recurse -Force }}
    Copy-Item -LiteralPath $SourceDir -Destination $NewDir -Recurse -Force
    if (!(Test-Path -LiteralPath (Join-Path $NewDir '__init__.py'))) {{ throw '新版本缺少 __init__.py' }}
    if (!(Test-Path -LiteralPath (Join-Path $NewDir 'version.ini'))) {{ throw '新版本缺少 version.ini' }}

    if (Test-Path -LiteralPath $PluginDir) {{
        Copy-Item -LiteralPath $PluginDir -Destination $BackupDir -Recurse -Force
        Move-Item -LiteralPath $PluginDir -Destination $OldDir
    }}
    Move-Item -LiteralPath $NewDir -Destination $PluginDir
    if (Test-Path -LiteralPath $OldDir) {{ Remove-Item -LiteralPath $OldDir -Recurse -Force }}
    Write-Result 'success' '更新安装成功' $BackupDir
    Cleanup-AfterSuccess
    Write-Host '[完成] 更新安装成功，已清理更新包、临时文件和旧日志。'
}} catch {{
    $msg = $_.Exception.Message
    Write-Host ('[失败] ' + $msg)
    try {{
        if (Test-Path -LiteralPath $PluginDir) {{ Remove-Item -LiteralPath $PluginDir -Recurse -Force }}
        if (Test-Path -LiteralPath $OldDir) {{ Move-Item -LiteralPath $OldDir -Destination $PluginDir }}
        elseif (Test-Path -LiteralPath $BackupDir) {{ Copy-Item -LiteralPath $BackupDir -Destination $PluginDir -Recurse -Force }}
        Write-Result 'rolled_back' ('更新失败，已恢复旧版本：' + $msg) $BackupDir
        Write-Host '[已恢复] 已恢复更新前版本。'
    }} catch {{
        Write-Result 'rollback_failed' ('更新失败且自动恢复失败：' + $msg) $BackupDir
        Write-Host '[严重] 自动恢复失败，请保留此窗口并查看 backups 目录。'
    }}
}}

if (Test-Path -LiteralPath $Mo2Exe) {{ Start-Process -FilePath $Mo2Exe }}
Start-Sleep -Seconds 2
'''.format(
        plugin_dir=_ps_quote(plugin_dir), source_dir=_ps_quote(source), mo2_exe=_ps_quote(mo2_exe),
        backup_root=_ps_quote(backups), result_path=_ps_quote(result_path), version=_ps_quote(expected_version),
        auto_close_flag=_ps_quote(auto_close_flag), package_path=_ps_quote(package_path),
        stage_dir=_ps_quote(stage), pending_path=_ps_quote(pending_path), update_root=_ps_quote(root),
        log_dir=_ps_quote(logs_dir), fallback_log_dir=_ps_quote(fallback_logs_dir)
    )
    bat = '@echo off\r\nchcp 65001 >nul\r\ntitle 星黎助手安全更新\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0{}"\r\n'.format(os.path.basename(ps1_path))
    with open(ps1_path, "w", encoding="utf-8-sig", newline="\r\n") as handle:
        handle.write(ps1)
    with open(bat_path, "w", encoding="utf-8-sig", newline="\r\n") as handle:
        handle.write(bat)
    return {"stage": stage, "source": source, "pending": pending_path, "installer": bat_path, "auto_close_flag": auto_close_flag}


def read_last_update_result(controller) -> Optional[Dict[str, object]]:
    path = os.path.join(updates_dir(controller), "last_update_result.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except Exception:
        return None


class AutoUpdateDialog(QtWidgets.QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.local_version = str(getattr(controller, "local_version", "") or "0.0.0")
        self.manifest = None
        self.manifest_source = ""
        self.prepared = None
        self.check_thread = None
        self.download_thread = None
        self.progress = None
        self.setWindowTitle("星黎助手更新")
        self.resize(700, 520)
        self._build_ui()
        self._load_settings()

    def _build_ui(self):
        self.setMinimumSize(620, 430)
        self.resize(680, 480)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("星黎助手更新")
        font = QtGui.QFont()
        font.setPointSize(15)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        subtitle = QtWidgets.QLabel("优先使用国内镜像。更新通过校验后，在 MO2 空闲时可自动关闭并完成安装。")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #666; padding-bottom: 4px;")
        layout.addWidget(subtitle)

        self.status_frame = QtWidgets.QFrame()
        self.status_frame.setObjectName("updateStatusCard")
        self.status_frame.setStyleSheet(
            "QFrame#updateStatusCard { background: #f6f8fa; border: 1px solid #d9dee5; "
            "border-radius: 8px; padding: 4px; }"
        )
        status_layout = QtWidgets.QVBoxLayout(self.status_frame)
        status_layout.setContentsMargins(14, 12, 14, 12)
        status_layout.setSpacing(4)
        self.status_title = QtWidgets.QLabel("尚未检查更新")
        status_font = QtGui.QFont()
        status_font.setPointSize(12)
        status_font.setBold(True)
        self.status_title.setFont(status_font)
        self.status_detail = QtWidgets.QLabel("当前版本 {}".format(self.local_version))
        self.status_detail.setWordWrap(True)
        self.status_detail.setStyleSheet("color: #555;")
        status_layout.addWidget(self.status_title)
        status_layout.addWidget(self.status_detail)
        layout.addWidget(self.status_frame)

        option_row = QtWidgets.QHBoxLayout()
        self.auto_check = QtWidgets.QCheckBox("打开 MO2 时自动检查更新")
        self.auto_check.setToolTip("每次启动 MO2 后静默检查一次；不会自动下载安装。")
        self.auto_check.toggled.connect(self._save_auto_check)
        option_row.addWidget(self.auto_check)
        option_row.addStretch(1)
        self.source_label = QtWidgets.QLabel("更新源：尚未检查")
        self.source_label.setStyleSheet("color: #777;")
        option_row.addWidget(self.source_label)
        layout.addLayout(option_row)

        buttons = QtWidgets.QHBoxLayout()
        self.check_button = QtWidgets.QPushButton("重新检查")
        self.primary_button = QtWidgets.QPushButton("正在检查…")
        self.primary_button.setDefault(True)
        self.primary_button.setEnabled(False)
        self.check_button.clicked.connect(self.check_updates)
        self.primary_button.clicked.connect(self._primary_action)
        buttons.addWidget(self.check_button)
        buttons.addStretch(1)
        buttons.addWidget(self.primary_button)
        layout.addLayout(buttons)

        details_group = QtWidgets.QGroupBox("更新说明")
        details_layout = QtWidgets.QVBoxLayout(details_group)
        self.info_text = QtWidgets.QPlainTextEdit()
        self.info_text.setReadOnly(True)
        self.info_text.setPlaceholderText("发现新版本时会在这里显示更新说明。")
        details_layout.addWidget(self.info_text)
        layout.addWidget(details_group, 1)

        foot = QtWidgets.QHBoxLayout()
        open_btn = QtWidgets.QPushButton("打开更新目录")
        open_btn.clicked.connect(self.open_updates_dir)
        close_btn = QtWidgets.QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        foot.addWidget(open_btn)
        foot.addStretch(1)
        foot.addWidget(close_btn)
        layout.addLayout(foot)

    def _load_settings(self):
        settings = get_update_settings(self.controller)
        auto = (settings.get("autocheckonstartup") or "true").lower() in ("1", "yes", "true", "on")
        self.auto_check.blockSignals(True)
        self.auto_check.setChecked(auto)
        self.auto_check.blockSignals(False)
        cached_manifest = getattr(self.controller, "_xingli_update_manifest", None)
        if isinstance(cached_manifest, dict):
            self.manifest = dict(cached_manifest)
            self.manifest_source = str(getattr(self.controller, "_xingli_update_manifest_source", "") or "")
        cached = getattr(self.controller, "_xingli_update_status", None)
        if isinstance(cached, dict):
            self._apply_cached_status(cached)
        result = read_last_update_result(self.controller)
        if result and not self.info_text.toPlainText().strip():
            self.info_text.setPlainText("上次更新：{}\n{}".format(result.get("status", ""), result.get("message", "")))

    def _save_auto_check(self, checked):
        try:
            set_update_settings(self.controller, bool(checked))
        except Exception:
            logger.exception("保存自动检查设置失败")

    def _set_status_card(self, title, detail, kind="neutral"):
        palette = {
            "ok": ("#edf7ed", "#b7ddb8", "#1f6b2a"),
            "update": ("#fff6e5", "#f1cf8b", "#8a5700"),
            "busy": ("#eef5ff", "#b8d1f3", "#285a91"),
            "error": ("#fff0f0", "#e6b3b3", "#9b2c2c"),
            "neutral": ("#f6f8fa", "#d9dee5", "#444"),
        }
        bg, border, fg = palette.get(kind, palette["neutral"])
        self.status_frame.setStyleSheet(
            "QFrame#updateStatusCard { background: %s; border: 1px solid %s; border-radius: 8px; padding: 4px; }" % (bg, border)
        )
        self.status_title.setText(title)
        self.status_title.setStyleSheet("color: %s;" % fg)
        self.status_detail.setText(detail)

    def _apply_cached_status(self, status):
        state = status.get("state", "unknown")
        latest = status.get("latest", "")
        source = status.get("source", "")
        if source:
            self.source_label.setText("更新源：{}".format(_source_name(source)))
        if state == "update_available":
            self._set_status_card("发现新版本 {}".format(latest), "当前版本 {} · 更新已可用".format(self.local_version), "update")
            self.primary_button.setText("更新到 {}".format(latest))
            self.primary_button.setEnabled(bool(self.manifest))
        elif state == "up_to_date":
            self._set_status_card("已是最新版本", "当前版本 {}".format(self.local_version), "ok")
            self.primary_button.setText("已是最新版本")
            self.primary_button.setEnabled(False)
        elif state == "checking":
            self._set_status_card("正在检查更新…", "请稍候", "busy")
            self.primary_button.setText("正在检查…")
            self.primary_button.setEnabled(False)
        elif state == "error":
            self._set_status_card("暂时无法检查更新", status.get("message", "请稍后重试"), "error")
            self.primary_button.setText("重新检查")
            self.primary_button.setEnabled(True)

    def check_updates(self):
        self._save_auto_check(self.auto_check.isChecked())
        try:
            sources = load_update_sources(self.controller)
            urls = list(sources.get("manifest_urls", []))
            timeout = int(sources.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        except Exception as exc:
            self._on_check_failed(str(exc))
            return
        if not urls:
            self._on_check_failed("作者尚未配置可用更新源。")
            return
        self._set_status_card("正在检查更新…", "正在连接可用镜像", "busy")
        self.info_text.clear()
        self.check_button.setEnabled(False)
        self.primary_button.setText("正在检查…")
        self.primary_button.setEnabled(False)
        _publish_home_status(self.controller, "checking")
        self.check_thread = ManifestCheckThread(urls, timeout)
        self.check_thread.checked.connect(self._on_manifest_checked)
        self.check_thread.failed.connect(self._on_check_failed)
        self.check_thread.finished.connect(lambda: self.check_button.setEnabled(True))
        self.check_thread.start()

    def _on_manifest_checked(self, manifest, source):
        self.manifest = manifest
        self.manifest_source = source
        latest = str(manifest.get("version", "0.0.0"))
        newer = compare_versions(latest, self.local_version) > 0
        self.source_label.setText("更新源：{}".format(_source_name(source)))
        if newer:
            self._set_status_card("发现新版本 {}".format(latest), "当前版本 {} · 更新已可用".format(self.local_version), "update")
            self.info_text.setPlainText(str(manifest.get("changelog", "未提供更新说明。")))
            state = "update_available"
        else:
            self._set_status_card("已是最新版本", "当前版本 {}".format(self.local_version), "ok")
            self.info_text.setPlainText("当前版本已经是最新版本。")
            state = "up_to_date"
        set_update_settings(self.controller, self.auto_check.isChecked(), last_check=datetime.now().isoformat(timespec="seconds"))
        _publish_home_status(self.controller, state, latest=latest, source=source)
        if newer:
            if not self._try_prepare_cached_package():
                self.primary_button.setText("更新到 {}".format(latest))
                self.primary_button.setEnabled(True)
        else:
            self.prepared = None
            self.primary_button.setText("已是最新版本")
            self.primary_button.setEnabled(False)

    def _on_check_failed(self, error):
        self.source_label.setText("更新源：检查失败")
        self._set_status_card("暂时无法检查更新", "请检查网络后重试", "error")
        self.info_text.setPlainText(str(error))
        self.primary_button.setText("重新检查")
        self.primary_button.setEnabled(True)
        _publish_home_status(self.controller, "error", message=str(error))
        QtWidgets.QMessageBox.warning(self, "检查失败", str(error))

    def _package_target_path(self):
        if not self.manifest:
            return ""
        package = self.manifest.get("package", {})
        if not isinstance(package, dict):
            return ""
        latest = str(self.manifest.get("version", "update"))
        safe_version = re.sub(r"[^0-9A-Za-z_.-]+", "_", latest)
        filename = str(package.get("filename", "xingli_update_{}.zip".format(safe_version)))
        if not filename.lower().endswith(".zip"):
            filename += ".zip"
        return os.path.join(updates_dir(self.controller), filename)

    def _try_prepare_cached_package(self):
        """Reuse a previously downloaded package only after full local verification."""
        if not self.manifest:
            return False
        package = self.manifest.get("package", {})
        if not isinstance(package, dict):
            return False
        target = self._package_target_path()
        if not target or not os.path.isfile(target):
            return False
        expected_sha = str(package.get("sha256", "")).strip().lower()
        expected_size = int(package.get("size", 0) or 0)
        try:
            if expected_size and os.path.getsize(target) != expected_size:
                raise RuntimeError("缓存文件大小不一致")
            sha = hashlib.sha256()
            with open(target, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    sha.update(chunk)
            if sha.hexdigest().lower() != expected_sha:
                raise RuntimeError("缓存文件 SHA256 校验失败")
            if not zipfile.is_zipfile(target):
                raise RuntimeError("缓存文件不是有效 ZIP")
            with zipfile.ZipFile(target, "r") as archive:
                bad = archive.testzip()
                if bad:
                    raise RuntimeError("缓存 ZIP 校验失败：{}".format(bad))
            self.prepared = prepare_staged_update(self.controller, target, self.manifest)
            self.primary_button.setText("立即安装已下载更新")
            self.primary_button.setEnabled(True)
            self.info_text.appendPlainText("\n已找到之前下载且通过校验的更新，可直接安装。")
            return True
        except Exception as exc:
            logger.warning("忽略无效的已下载更新 %s: %s", target, exc)
            self.prepared = None
            try:
                os.remove(target)
            except Exception:
                pass
            return False

    def _primary_action(self):
        if self.prepared:
            self.install_prepared_update()
            return
        if self.manifest and compare_versions(str(self.manifest.get("version", "0.0.0")), self.local_version) > 0:
            self.download_update()
            return
        self.check_updates()

    def download_update(self):
        if not self.manifest:
            return
        package = self.manifest.get("package", {})
        if not isinstance(package, dict):
            return
        target = self._package_target_path()
        if not target:
            return
        mirrors = list(package.get("mirrors", []))
        sha = str(package.get("sha256", ""))
        size = int(package.get("size", 0) or 0)
        self.primary_button.setText("正在下载…")
        self.primary_button.setEnabled(False)
        self.check_button.setEnabled(False)
        self.progress = QtWidgets.QProgressDialog("正在下载更新……", "取消", 0, 100, self)
        self.progress.setWindowTitle("星黎助手更新")
        self.progress.setWindowModality(Qt.WindowModal)
        self.progress.setAutoClose(False)
        self.progress.setMinimumDuration(0)
        self.progress.show()
        self.download_thread = MirrorDownloadThread(mirrors, target, sha, size)
        self.progress.canceled.connect(self.download_thread.requestInterruption)
        self.download_thread.progress.connect(self._on_download_progress)
        self.download_thread.finished.connect(self._on_download_finished)
        self.download_thread.failed.connect(self._on_download_failed)
        self.download_thread.start()

    def _on_download_progress(self, value, text):
        if self.progress:
            self.progress.setValue(value)
            self.progress.setLabelText(text)

    def _on_download_finished(self, path, mirror_name):
        if self.progress:
            self.progress.close()
        self.check_button.setEnabled(True)
        try:
            self.prepared = prepare_staged_update(self.controller, path, self.manifest or {})
            self.primary_button.setText("立即安装已下载更新")
            self.primary_button.setEnabled(True)
            self.info_text.appendPlainText("\n下载来源：{}\n更新已下载并通过安全校验。".format(mirror_name))
            reply = QtWidgets.QMessageBox.question(
                self,
                "更新已准备好",
                "更新已下载并通过校验。\n\n是否现在安装并重启 MO2？\n选择【否】会保留已下载的更新，稍后可直接安装。",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes,
            )
            if reply == QtWidgets.QMessageBox.Yes:
                self.install_prepared_update(confirm=False)
        except Exception as exc:
            logger.exception("准备更新失败")
            latest = str((self.manifest or {}).get("version", ""))
            self.primary_button.setText("更新到 {}".format(latest) if latest else "重新下载")
            self.primary_button.setEnabled(True)
            QtWidgets.QMessageBox.critical(self, "准备失败", "更新文件已下载，但准备安装失败：\n{}".format(exc))

    def _on_download_failed(self, error):
        if self.progress:
            self.progress.close()
        self.check_button.setEnabled(True)
        latest = str((self.manifest or {}).get("version", ""))
        self.primary_button.setText("更新到 {}".format(latest) if latest else "重新下载")
        self.primary_button.setEnabled(True)
        if str(error).strip() == "用户已取消下载。":
            self.info_text.appendPlainText("\n下载已取消。")
            return
        QtWidgets.QMessageBox.critical(self, "下载失败", str(error))

    def install_prepared_update(self, confirm=True):
        if not self.prepared:
            return
        installer = self.prepared.get("installer", "")
        if not installer or not os.path.exists(installer):
            QtWidgets.QMessageBox.warning(self, "安装文件缺失", "请重新下载并准备更新。")
            return

        running = False
        running_name = ""
        try:
            running = bool(self.controller._is_mo2_managed_app_running())
            running_name = str(getattr(self.controller, "_mo2_managed_app_name", "") or "")
        except Exception:
            running = bool(getattr(self.controller, "_mo2_managed_app_running", False))

        if running:
            detail = "\n\n正在运行：{}".format(running_name) if running_name else ""
            QtWidgets.QMessageBox.warning(
                self,
                "暂时不能更新",
                "当前有程序正在通过 MO2 运行。为了避免破坏虚拟文件系统，星黎不会自动关闭 MO2。"
                "{}\n\n请先结束正在运行的游戏或工具，再点击【安装更新】。".format(detail),
            )
            return

        if confirm and not _question(
            self,
            "安装更新",
            "MO2 当前处于空闲状态。\n\n"
            "继续后，星黎会先关闭星黎小助手窗口，再请求 MO2 正常关闭；MO2 退出完成后自动安装更新并重新打开 MO2。\n"
            "不会强制结束 MO2 进程。\n\n现在继续吗？",
        ):
            return

        auto_close_flag = self.prepared.get("auto_close_flag", "")
        try:
            if auto_close_flag:
                with open(auto_close_flag, "w", encoding="utf-8") as handle:
                    handle.write("allow\n")
            # Strict shutdown order: close Xingli UI first, then start the external
            # installer, then request MO2 to exit.  The plugin itself lives inside
            # MO2, so closing Xingli here means all Xingli-owned windows are gone
            # before MO2 receives its close request.
            if not bool(self.close()):
                raise RuntimeError("无法先关闭星黎更新窗口，已取消更新。")
            main_window = getattr(self.controller, "window", None)
            if main_window is not None and main_window is not self:
                if not bool(main_window.close()):
                    raise RuntimeError("无法先关闭星黎小助手窗口，已取消更新。")
            try:
                QtWidgets.QApplication.processEvents()
            except Exception:
                pass
            os.startfile(installer)  # type: ignore[attr-defined]
            QTimer.singleShot(350, _request_graceful_mo2_shutdown)
        except Exception as exc:
            try:
                if auto_close_flag and os.path.exists(auto_close_flag):
                    os.remove(auto_close_flag)
            except Exception:
                pass
            QtWidgets.QMessageBox.critical(self, "启动失败", str(exc))

    def open_updates_dir(self):
        path = updates_dir(self.controller)
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                webbrowser.open("file://" + path)
        except Exception:
            webbrowser.open("file://" + path)


def show_auto_updater(controller, parent=None):
    if parent is None:
        try:
            parent = controller._parentWidget()
        except Exception:
            parent = getattr(controller, "window", None)
    dialog = AutoUpdateDialog(controller, parent)
    # 用户从主页进入更新页即执行一次真实检查，避免还要再点一次“检查更新”。
    QTimer.singleShot(0, dialog.check_updates)
    _exec_dialog(dialog)


def _start_home_check(controller, respect_auto_setting: bool) -> None:
    """Run a non-modal update status check for the home page."""
    try:
        if respect_auto_setting:
            settings = get_update_settings(controller)
            auto = (settings.get("autocheckonstartup") or "true").lower() in ("1", "yes", "true", "on")
            if not auto:
                _publish_home_status(controller, "disabled", message="自动检查已关闭")
                return
        sources = load_update_sources(controller)
        urls = list(sources.get("manifest_urls", []))
        if not urls:
            logger.info("自动更新未配置远程源，跳过检查")
            _publish_home_status(controller, "error", message="更新源未配置")
            return
        _publish_home_status(controller, "checking")
        thread = ManifestCheckThread(urls, int(sources.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)))

        def on_checked(manifest, source):
            try:
                local = str(getattr(controller, "local_version", "") or "0.0.0")
                latest = str(manifest.get("version", "0.0.0"))
                set_update_settings(controller, True, last_check=datetime.now().isoformat(timespec="seconds"))
                state = "update_available" if compare_versions(latest, local) > 0 else "up_to_date"
                controller._xingli_update_manifest = dict(manifest)
                controller._xingli_update_manifest_source = str(source)
                _publish_home_status(controller, state, latest=latest, source=source)
            except Exception:
                logger.exception("助手更新结果处理失败")

        def on_failed(error):
            logger.warning("助手更新检查失败: %s", error)
            try:
                set_update_settings(controller, True, last_check=datetime.now().isoformat(timespec="seconds"))
            except Exception:
                pass
            _publish_home_status(controller, "error", message=str(error))

        controller._xingli_auto_update_thread = thread
        thread.checked.connect(on_checked)
        thread.failed.connect(on_failed)
        thread.start()
    except Exception:
        logger.exception("助手更新检查初始化失败")
        _publish_home_status(controller, "error", message="助手更新检查初始化失败")


def maybe_auto_check(controller, parent=None) -> None:
    """Check once after MO2 starts. Never interrupts the user; home page shows the result."""
    _start_home_check(controller, respect_auto_setting=True)


def manual_home_check(controller, parent=None) -> None:
    """Explicit home-page check. Ignores the startup auto-check preference."""
    _start_home_check(controller, respect_auto_setting=False)
