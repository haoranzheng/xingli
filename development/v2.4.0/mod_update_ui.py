# coding: utf-8
"""Xingli per-MOD update UI, independent from the helper's main window.

Download workers only perform HTTP and filesystem cache operations.
MO2 API calls and all widgets remain on the Qt UI thread.
"""
import os
from pathlib import Path

try:
    from PyQt6 import QtWidgets
    from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
except ImportError:
    from PyQt5 import QtWidgets
    from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal

from . import mod_update_core as core
from . import stability

logger = stability.get_logger("per_mod_updates")


def _role(name):
    roles = getattr(QtWidgets.QMessageBox, "ButtonRole", QtWidgets.QMessageBox)
    return getattr(roles, name)


def _exec(dialog):
    return dialog.exec() if hasattr(dialog, "exec") else dialog.exec_()


def _main_parent(controller):
    try:
        widget = controller._parentWidget()
        if widget:
            return widget
    except Exception:
        pass
    # Helper's own UI might never have been created. Qt finds MO2's main window.
    app = QtWidgets.QApplication.instance()
    if app:
        for widget in app.topLevelWidgets():
            if isinstance(widget, QtWidgets.QMainWindow):
                return widget
    return None


def _paths(controller):
    folder = Path(__file__).parent / "mod_update_sources.json"
    sources = core.load_sources(folder)
    downloads = Path(str(controller.organizer.downloadsPath())) / "XingliModUpdates"
    try:
        base = str(controller.organizer.pluginDataPath())
    except Exception:
        base = str(controller.organizer.basePath())
    backups = Path(base) / "xingli_assistant" / "mod_backups"
    return sources, downloads, backups


class CatalogThread(QThread):
    ready = pyqtSignal(object)
    failure = pyqtSignal(str)

    def __init__(self, sources, parent=None):
        super().__init__(parent)
        self.sources = sources

    def run(self):
        try:
            records, source = core.fetch_catalog(self.sources)
            self.ready.emit(records)
        except Exception as exc:
            self.failure.emit(str(exc))


class DownloadThread(QThread):
    ready = pyqtSignal(str)
    failure = pyqtSignal(str)
    progress_changed = pyqtSignal(int)

    def __init__(self, record, cache, sources, parent=None):
        super().__init__(parent)
        self.record = record
        self.cache = cache
        self.sources = sources
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            file = core.download_verified(
                self.record, self.cache, self.sources["hosts"],
                timeout=max(15, self.sources["timeout"]),
                progress=lambda percent: self.progress_changed.emit(percent),
                cancelled=lambda: self._cancelled,
            )
            self.ready.emit(file)
        except Exception as exc:
            self.failure.emit(str(exc))


def maybe_auto_check(controller):
    """Called by MO2 startup timer without creating Xingli's main window."""
    if getattr(controller, "_xingli_mod_update_check_thread", None):
        return
    try:
        sources, _, _ = _paths(controller)
    except Exception:
        logger.exception("MOD update source config invalid")
        return
    thread = CatalogThread(sources)
    controller._xingli_mod_update_check_thread = thread

    def completed(records):
        try:
            updates = core.eligible_updates(controller.organizer, records)
            if not updates:
                return
            summary = "|".join(sorted(x["id"] + ":" + x["version"] for x in updates))
            if getattr(controller, "_xingli_mod_update_prompted", "") == summary:
                return
            controller._xingli_mod_update_prompted = summary
            QTimer.singleShot(500, lambda: _prompt(controller, updates))
        except Exception:
            logger.exception("Cannot process MOD update results")

    def failed(message):
        logger.warning("MOD updates unavailable: %s", message)

    thread.ready.connect(completed)
    thread.failure.connect(failed)
    thread.finished.connect(lambda: setattr(controller, "_xingli_mod_update_check_thread", None))
    thread.start()


def _prompt(controller, updates):
    parent = _main_parent(controller)
    message = QtWidgets.QMessageBox(parent)
    message.setWindowTitle("星黎小助手 · MOD 更新提醒")
    message.setText("发现 %d 个已安装 MOD 有新版本。" % len(updates))
    lines = [
        "%s  %s → %s" % (x["mo2_name"], x["installed_version"], x["version"])
        for x in updates[:12]
    ]
    message.setInformativeText("\n".join(lines) + (
        "\n……" if len(updates) > 12 else ""
    ))
    button = message.addButton("查看并选择更新", _role("AcceptRole"))
    message.addButton("稍后提醒", _role("RejectRole"))
    _exec(message)
    if message.clickedButton() is button:
        show_update_center(controller, updates=updates)


class ModUpdateDialog(QtWidgets.QDialog):
    def __init__(self, controller, updates=None, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.sources, self.download_dir, self.backups_dir = _paths(controller)
        self.records = list(updates or [])
        self.catalog_thread = None
        self.download_thread = None
        self.download_record = None
        self.progress_dialog = None
        self.setWindowTitle("星黎 · 独立 MOD 更新")
        self.resize(710, 460)
        self._build()
        self._render()
        if updates is None:
            QTimer.singleShot(0, self.check)

    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "仅显示 MO2 已安装、且可以可靠读取当前版本的 MOD。"
            " 下载时优先使用国内镜像；更新交由 MO2 原生安装器确认。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.listing = QtWidgets.QListWidget(self)
        self.listing.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
                                      if hasattr(QtWidgets.QAbstractItemView, "SelectionMode")
                                      else QtWidgets.QAbstractItemView.SingleSelection)
        layout.addWidget(self.listing, 1)
        self.status = QtWidgets.QLabel("等待检查")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QtWidgets.QHBoxLayout()
        self.check_button = QtWidgets.QPushButton("重新检查")
        self.check_button.clicked.connect(self.check)
        row.addWidget(self.check_button)
        row.addStretch(1)
        self.install_button = QtWidgets.QPushButton("下载并通过 MO2 安装")
        self.install_button.clicked.connect(self.download_selected)
        row.addWidget(self.install_button)
        close = QtWidgets.QPushButton("关闭")
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)

    def _render(self):
        self.listing.clear()
        for item in self.records:
            label = "%s    %s → %s    %.1f MB\n%s" % (
                item["mo2_name"], item["installed_version"], item["version"],
                item["package"]["size"] / 1048576,
                item["changelog"].replace("\n", " ")[:170],
            )
            self.listing.addItem(label)
        self.install_button.setEnabled(bool(self.records) and self.download_thread is None)
        self.status.setText(
            "发现 %d 个可以更新的 MOD；未确认本地版本的 MOD 不会被自动覆盖。" %
            len(self.records)
        )

    def check(self):
        if self.catalog_thread is not None or self.download_thread is not None:
            return
        self.status.setText("正在检查 MOD 更新目录……")
        self.check_button.setEnabled(False)
        self.catalog_thread = CatalogThread(self.sources, self)

        def ready(records):
            self.check_button.setEnabled(True)
            try:
                self.records = core.eligible_updates(self.controller.organizer, records)
                self._render()
            except Exception as exc:
                self.status.setText("检查失败：" + str(exc))

        def failed(error):
            self.check_button.setEnabled(True)
            self.status.setText("连接 MOD 更新目录失败：" + str(error))

        self.catalog_thread.ready.connect(ready)
        self.catalog_thread.failure.connect(failed)
        self.catalog_thread.finished.connect(lambda: setattr(self, "catalog_thread", None))
        self.catalog_thread.start()

    def download_selected(self):
        if self.download_thread is not None:
            return
        index = self.listing.currentRow()
        if not 0 <= index < len(self.records):
            QtWidgets.QMessageBox.information(self, "选择 MOD", "请先选择需要更新的 MOD。")
            return
        if bool(getattr(self.controller, "_mo2_managed_app_running", False)):
            QtWidgets.QMessageBox.warning(self, "更新被阻止", "游戏或工具正在通过 MO2 运行，请先退出再更新 MOD。")
            return
        self.download_record = self.records[index]
        message = (
            "将从镜像下载：%s\n版本：%s → %s\n\n"
            "SHA256 校验通过后，先完整备份现有 MOD，再交给 MO2 原生安装器处理。"
            "有个人修改、FOMOD 选项或 DLL 的 MOD，请在 MO2 安装向导中仔细确认。\n\n"
            "是否继续？"
        ) % (self.download_record["mo2_name"],
             self.download_record["installed_version"], self.download_record["version"])
        if QtWidgets.QMessageBox.question(self, "更新指定 MOD", message) != (
            QtWidgets.QMessageBox.StandardButton.Yes
            if hasattr(QtWidgets.QMessageBox, "StandardButton")
            else QtWidgets.QMessageBox.Yes
        ):
            return
        thread = DownloadThread(self.download_record, self.download_dir, self.sources, self)
        self.download_thread = thread
        progress = QtWidgets.QProgressDialog("正在下载并校验更新包……", "取消", 0, 100, self)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        self.progress_dialog = progress
        self.install_button.setEnabled(False)
        thread.progress_changed.connect(progress.setValue)
        progress.canceled.connect(thread.cancel)

        self._download_result = None
        self._download_error = None
        thread.ready.connect(lambda path: setattr(self, "_download_result", path))
        thread.failure.connect(lambda error: setattr(self, "_download_error", error))

        def on_finished():
            self._finish_download()
            if self._download_error:
                QtWidgets.QMessageBox.warning(self, "下载失败", self._download_error)
            elif self._download_result:
                self._install_via_mo2(self._download_result)

        thread.finished.connect(on_finished)
        thread.start()

    def _finish_download(self):
        if self.progress_dialog is not None:
            self.progress_dialog.close()
            self.progress_dialog = None
        self.download_thread = None
        self.install_button.setEnabled(bool(self.records))

    def closeEvent(self, event):
        if ((self.catalog_thread and self.catalog_thread.isRunning()) or
                (self.download_thread and self.download_thread.isRunning())):
            event.ignore()
            self.status.setText("检查或下载尚未完成；请稍候或在下载进度窗口取消。")
            return
        super().closeEvent(event)

    def _install_via_mo2(self, archive):
        record = self.download_record
        try:
            backup = core.backup_mod(
                self.controller.organizer, record["mo2_name"],
                self.backups_dir, record["id"])
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "备份失败 · 已取消安装",
                "为保护用户原有 MOD，无法创建完整备份：\n%s" % exc,
            )
            return
        try:
            # MO2 controls FOMOD prompts and any overwrite decisions.
            try:
                result = self.controller.organizer.installMod(str(archive), record["mo2_name"])
            except TypeError:
                result = self.controller.organizer.installMod(str(archive))
            if not result:
                raise core.UpdateError("MO2 安装返回取消或失败")
            self.controller.organizer.refresh(True)
            QtWidgets.QMessageBox.information(
                self, "MOD 安装流程完成",
                "MO2 已执行安装流程。\n完整旧版备份已保留：\n%s\n\n"
                "请检查 MOD 启用状态、优先级、FOMOD 选项及游戏运行情况。" %
                backup["backup_path"])
            self.check()
        except Exception as exc:
            logger.exception("MO2 MOD install failed")
            QtWidgets.QMessageBox.warning(
                self, "MOD 安装未完成",
                "MO2 安装失败或被取消：%s\n\n旧版完整备份仍在：\n%s\n\n"
                "为避免对运行中的 MO2 目录强制改写，"
                "如发现文件已改变，请先退出 MO2，再从备份恢复。" %
                (exc, backup["backup_path"]))


def show_update_center(controller, updates=None):
    dialog = getattr(controller, "_xingli_mod_update_dialog", None)
    if dialog and dialog.isVisible():
        dialog.raise_()
        dialog.activateWindow()
        return
    try:
        dialog = ModUpdateDialog(controller, updates=updates, parent=_main_parent(controller))
        controller._xingli_mod_update_dialog = dialog
        dialog.show()
    except Exception as exc:
        logger.exception("Cannot open MOD update center")
        QtWidgets.QMessageBox.critical(_main_parent(controller), "MOD 更新中心", str(exc))


def manual_check(controller):
    show_update_center(controller, updates=None)
