# coding: utf-8
"""MO2 profile download notifications and safe profile installation UI."""
try:
    from PyQt6 import QtCore, QtWidgets
    from PyQt6.QtCore import QThread, QTimer, pyqtSignal
except ImportError:
    from PyQt5 import QtCore, QtWidgets
    from PyQt5.QtCore import QThread, QTimer, pyqtSignal

from . import profile_download_core as core
from . import stability

logger = stability.get_logger("profile_downloads")


def _exec(box):
    return box.exec() if hasattr(box, "exec") else box.exec_()


def _role(name):
    roles = getattr(QtWidgets.QMessageBox, "ButtonRole", QtWidgets.QMessageBox)
    return getattr(roles, name)


def _parent(controller):
    try:
        widget = controller._parentWidget()
        if widget is not None:
            return widget
    except Exception:
        pass
    app = QtWidgets.QApplication.instance()
    if app:
        for window in app.topLevelWidgets():
            if isinstance(window, QtWidgets.QMainWindow):
                return window
    return None


class CheckWorker(QThread):
    ok = pyqtSignal(object)
    bad = pyqtSignal(str)

    def run(self):
        try:
            self.ok.emit(core.fetch_profiles())
        except Exception as exc:
            self.bad.emit(str(exc))


class DownloadWorker(QThread):
    ok = pyqtSignal(object)
    bad = pyqtSignal(str)

    def __init__(self, record, parent=None):
        super().__init__(parent)
        self.record = record

    def run(self):
        try:
            self.ok.emit(core.download_package(self.record))
        except Exception as exc:
            self.bad.emit(str(exc))


def startup_check(controller):
    """Check the remote profile catalog once after MO2 has finished starting."""
    worker = getattr(controller, "_xingli_profile_check_worker", None)
    if worker is not None and worker.isRunning():
        return
    worker = CheckWorker()
    controller._xingli_profile_check_worker = worker
    result = {"records": None, "error": None}
    worker.ok.connect(lambda data: result.__setitem__("records", data))
    worker.bad.connect(lambda error: result.__setitem__("error", error))

    def done():
        controller._xingli_profile_check_worker = None
        if result["error"]:
            logger.warning("档案下载清单检查失败：%s", result["error"])
            return
        try:
            receipts = core.read_receipts(core.receipt_path(controller.organizer))
            pending = core.unseen(result["records"] or [], receipts)
            if not pending:
                return
            key = "|".join("{}:{}".format(item["id"], item["version"]) for item in pending)
            if getattr(controller, "_xingli_profile_notified", None) == key:
                return
            controller._xingli_profile_notified = key
            QTimer.singleShot(0, lambda: _notify(controller, pending))
        except Exception:
            logger.exception("处理档案下载通知失败")

    worker.finished.connect(done)
    worker.start()


def _notify(controller, records):
    box = QtWidgets.QMessageBox(_parent(controller))
    box.setWindowTitle("星黎 · 新档案")
    box.setText("发现 {} 个新的 MO2 档案可以下载。".format(len(records)))
    lines = [
        "{}  v{}".format(item["name"], item["version"])
        for item in records[:10]
    ]
    if len(records) > 10:
        lines.append("以及其他 {} 个档案".format(len(records) - 10))
    box.setInformativeText(
        "下载后会在 MO2 的 profiles 目录中创建一个新的档案文件夹。\n"
        "不会覆盖已有档案，不会切换当前档案，也不会修改 MOD 文件。\n\n"
        + "\n".join(lines)
    )
    go = box.addButton("查看并下载", _role("AcceptRole"))
    box.addButton("稍后提醒", _role("RejectRole"))
    _exec(box)
    if box.clickedButton() is go:
        show_center(controller)


class ProfileCenter(QtWidgets.QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.check_worker = None
        self.download_worker = None
        self.records = []
        self.setWindowTitle("星黎 · 档案下载")
        self.resize(760, 470)
        self._build()
        QTimer.singleShot(0, self.check)

    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)
        help_text = QtWidgets.QLabel(
            "这里提供作者发布的 MO2 Profile 档案。\n"
            "下载会在 profiles 目录中新增一个档案文件夹；不会覆盖现有档案，"
            "不会更改当前档案，也不会自动安装或启用任何 MOD。"
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        self.list = QtWidgets.QListWidget(self)
        self.list.currentRowChanged.connect(self._selection_changed)
        layout.addWidget(self.list, 1)

        self.description = QtWidgets.QLabel("选择一个档案查看说明。")
        self.description.setWordWrap(True)
        self.description.setMinimumHeight(48)
        layout.addWidget(self.description)

        self.status = QtWidgets.QLabel("准备检查新档案……")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        controls = QtWidgets.QHBoxLayout()
        self.check_button = QtWidgets.QPushButton("刷新档案列表")
        self.check_button.clicked.connect(self.check)
        controls.addWidget(self.check_button)
        controls.addStretch(1)
        self.download_button = QtWidgets.QPushButton("下载并添加为新档案")
        self.download_button.clicked.connect(self.download)
        controls.addWidget(self.download_button)
        close = QtWidgets.QPushButton("关闭")
        close.clicked.connect(self.close)
        controls.addWidget(close)
        layout.addLayout(controls)
        self.download_button.setEnabled(False)

    def _selection_changed(self, index):
        if not 0 <= index < len(self.records):
            self.description.setText("选择一个档案查看说明。")
            self.download_button.setEnabled(False)
            return
        record = self.records[index]
        self.description.setText(
            "{}  ·  版本 {}\n{}".format(
                record["name"], record["version"],
                record.get("description") or "作者未提供档案说明。"
            )
        )
        self.download_button.setEnabled(self.download_worker is None)

    def check(self):
        if self.check_worker is not None or self.download_worker is not None:
            return
        self.status.setText("正在检查档案发布清单……")
        self.check_button.setEnabled(False)
        worker = CheckWorker()
        self.check_worker = worker
        result = {"data": None, "error": None}
        worker.ok.connect(lambda data: result.__setitem__("data", data))
        worker.bad.connect(lambda error: result.__setitem__("error", error))

        def done():
            self.check_worker = None
            self.check_button.setEnabled(True)
            if result["error"]:
                self.status.setText("检查失败：" + result["error"])
                self.download_button.setEnabled(False)
                return
            self.records = result["data"] or []
            receipts = core.read_receipts(core.receipt_path(self.controller.organizer))
            self.list.clear()
            for record in self.records:
                installed = receipts.get(record["id"])
                if installed == record["version"]:
                    state = "已下载"
                elif installed:
                    state = "有新版"
                else:
                    state = "新档案"
                item = QtWidgets.QListWidgetItem(
                    "{}    v{}    [{}]".format(record["name"], record["version"], state)
                )
                self.list.addItem(item)
            pending = core.unseen(self.records, receipts)
            self.status.setText(
                "共找到 {} 个档案，其中 {} 个尚未下载或有新版本。".format(
                    len(self.records), len(pending)
                )
            )
            if self.records:
                self.list.setCurrentRow(0)
            else:
                self.description.setText("作者暂未发布可下载的档案。")
                self.download_button.setEnabled(False)

        worker.finished.connect(done)
        worker.start()

    def download(self):
        if self.download_worker is not None or self.check_worker is not None:
            return
        index = self.list.currentRow()
        if not 0 <= index < len(self.records):
            QtWidgets.QMessageBox.information(self, "选择档案", "请先选择一个档案。")
            return
        record = self.records[index]
        try:
            root, active_profile = core.profile_paths(self.controller.organizer)
            target_preview = core.suggested_target(root, record)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "MO2 Profile 无法确认", str(exc))
            return

        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("下载 MO2 档案")
        box.setText(
            "将下载「{}」并新增到：\n{}".format(record["name"], target_preview)
        )
        box.setInformativeText(
            "本操作只新增一个 Profile 文件夹，不会覆盖同名档案。\n"
            "不会修改当前 Profile，也不会自动切换档案或安装 MOD。\n"
            "完成后请在 MO2 顶部的档案选择框中手动选择新档案。"
        )
        yes = box.addButton("下载并安装档案", _role("AcceptRole"))
        box.addButton("取消", _role("RejectRole"))
        _exec(box)
        if box.clickedButton() is not yes:
            return

        self.download_button.setEnabled(False)
        self.check_button.setEnabled(False)
        self.status.setText("正在下载并校验档案文件……")
        worker = DownloadWorker(record, self)
        self.download_worker = worker
        result = {"data": None, "error": None}
        worker.ok.connect(lambda data: result.__setitem__("data", data))
        worker.bad.connect(lambda error: result.__setitem__("error", error))

        def done():
            self.download_worker = None
            self.check_button.setEnabled(True)
            self.download_button.setEnabled(bool(self.records))
            if result["error"]:
                self.status.setText("下载失败：" + result["error"])
                return
            try:
                imported = core.install_profile_archive(
                    self.controller.organizer, record, result["data"]
                )
                try:
                    core.save_receipt(
                        core.receipt_path(self.controller.organizer), record
                    )
                except Exception:
                    logger.exception("档案已安装，但下载回执保存失败")
                self.status.setText(
                    "档案已添加：{}".format(imported["path"])
                )
                QtWidgets.QMessageBox.information(
                    self,
                    "档案下载完成",
                    "已新增 MO2 档案：{}\n\n"
                    "文件数量：{}\n路径：{}\n\n"
                    "请在 MO2 顶部档案选择框中手动选择该档案。"
                    "如果列表尚未刷新，请重新打开档案选择框或重启 MO2。"
                    "\n当前正在使用的档案未被修改。".format(
                        imported["name"], imported["files"], imported["path"]
                    )
                )
                self.check()
            except Exception as exc:
                logger.exception("安装 MO2 档案失败")
                self.status.setText("安装失败：" + str(exc))
                QtWidgets.QMessageBox.critical(self, "档案安装失败", str(exc))

        worker.finished.connect(done)
        worker.start()

    def closeEvent(self, event):
        if (
            self.check_worker is not None and self.check_worker.isRunning()
        ) or (
            self.download_worker is not None and self.download_worker.isRunning()
        ):
            event.ignore()
            self.status.setText("任务尚未结束，请等待检查或下载完成。")
            return
        super().closeEvent(event)


def show_center(controller):
    dialog = getattr(controller, "_xingli_profile_center", None)
    if dialog is not None and dialog.isVisible():
        dialog.raise_()
        dialog.activateWindow()
        return
    dialog = ProfileCenter(controller, _parent(controller))
    controller._xingli_profile_center = dialog
    dialog.show()
