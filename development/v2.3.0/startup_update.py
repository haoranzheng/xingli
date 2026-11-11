# coding: utf-8
"""MO2-startup update check: non-blocking, independent of opening the Xingli window.

Integration contract:
    checker = StartupUpdateChecker(
        installed_version="2.2.0",
        manifest_urls=[...],
        parent_window=mo2_main_window,
        on_install_requested=lambda manifest: open_existing_updater(manifest),
    )
    checker.start(delay_ms=2500)
Keep checker alive on the controller/organizer instance.
"""
import json
import logging
import re
import threading
import urllib.request

try:
    from PyQt6.QtCore import QObject, QTimer, pyqtSignal
    from PyQt6.QtWidgets import QMessageBox
except ImportError:
    from PyQt5.QtCore import QObject, QTimer, pyqtSignal
    from PyQt5.QtWidgets import QMessageBox

_LOG = logging.getLogger(__name__)
MAX_MANIFEST_BYTES = 65536
DEFAULT_TIMEOUT_SECONDS = 8
_VERSION_RE = re.compile(r"^\d+(?:\.\d+){1,3}$")


def version_tuple(value):
    value = str(value).strip()
    if not _VERSION_RE.fullmatch(value):
        raise ValueError("invalid update version: " + repr(value))
    parts = [int(x) for x in value.split(".")]
    return tuple((parts + [0] * 4)[:4])


def read_manifest(urls, timeout_seconds=DEFAULT_TIMEOUT_SECONDS):
    errors = []
    for url in urls:
        if not isinstance(url, str) or not url.startswith("https://"):
            errors.append("untrusted manifest URL")
            continue
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "XingliAssistant-UpdateCheck/2.3", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                raw = response.read(MAX_MANIFEST_BYTES + 1)
                if len(raw) > MAX_MANIFEST_BYTES:
                    raise ValueError("manifest exceeds size limit")
                data = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(data, dict) or data.get("product") != "xingli_Little_assistant":
                raise ValueError("unexpected update product")
            if data.get("channel") != "stable":
                raise ValueError("unexpected update channel")
            version_tuple(data.get("version", ""))
            package = data.get("package")
            if not isinstance(package, dict):
                raise ValueError("missing update package")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", str(package.get("sha256", ""))):
                raise ValueError("missing package SHA256")
            mirrors = package.get("mirrors")
            if not isinstance(mirrors, list) or not any(
                isinstance(item, dict) and str(item.get("url", "")).startswith("https://")
                for item in mirrors
            ):
                raise ValueError("missing HTTPS package mirrors")
            return data
        except Exception as exc:
            errors.append("%s: %s" % (url, exc))
    raise RuntimeError("all update manifests unavailable: " + "; ".join(errors))


class StartupUpdateChecker(QObject):
    """A background manifest read with a single UI-thread notification."""

    _completed = pyqtSignal(object, object)

    def __init__(self, installed_version, manifest_urls, parent_window,
                 on_install_requested, parent=None):
        super().__init__(parent)
        self.installed_version = str(installed_version)
        self.manifest_urls = list(manifest_urls)
        self.parent_window = parent_window
        self.on_install_requested = on_install_requested
        self._running = False
        self._shown_version = None
        self._completed.connect(self._on_completed)

    def start(self, delay_ms=2500):
        # MO2's UI can exist without Xingli's window ever being opened.
        QTimer.singleShot(max(0, int(delay_ms)), self.check_now)

    def check_now(self):
        if self._running:
            return
        self._running = True

        def worker():
            try:
                manifest = read_manifest(self.manifest_urls)
                self._completed.emit(manifest, None)
            except Exception as exc:
                self._completed.emit(None, str(exc))

        thread = threading.Thread(target=worker, daemon=True, name="xingli-update-check")
        thread.start()

    def _on_completed(self, manifest, error):
        self._running = False
        if error:
            _LOG.info("Xingli update check skipped: %s", error)
            return
        try:
            newest = str(manifest["version"])
            if version_tuple(newest) <= version_tuple(self.installed_version):
                return
            if self._shown_version == newest:
                return
            self._shown_version = newest
            dialog = QMessageBox(self.parent_window)
            dialog.setWindowTitle("星黎小助手 · 发现新版本")
            dialog.setIcon(QMessageBox.Icon.Information if hasattr(QMessageBox, "Icon") else QMessageBox.Information)
            dialog.setText("发现星黎小助手新版本 %s（当前 %s）。" % (newest, self.installed_version))
            dialog.setInformativeText(str(manifest.get("changelog", ""))[:3000])
            install_btn = dialog.addButton("查看并更新", QMessageBox.ButtonRole.AcceptRole if hasattr(QMessageBox, "ButtonRole") else QMessageBox.AcceptRole)
            dialog.addButton("稍后提醒", QMessageBox.ButtonRole.RejectRole if hasattr(QMessageBox, "ButtonRole") else QMessageBox.RejectRole)
            dialog.exec()
            if dialog.clickedButton() is install_btn:
                self.on_install_requested(manifest)
        except Exception:
            _LOG.exception("Xingli startup update notification failed")
