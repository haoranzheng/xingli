# coding: utf-8
"""Non-intrusive, read-only Xingli mod/shader news UI."""
try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    from PyQt6.QtCore import QThread, QTimer, pyqtSignal
except ImportError:
    from PyQt5 import QtCore, QtGui, QtWidgets
    from PyQt5.QtCore import QThread, QTimer, pyqtSignal

from . import announcement_core as core
from . import stability

logger = stability.get_logger("announcements")


def _exec(dialog):
    return dialog.exec() if hasattr(dialog, "exec") else dialog.exec_()


def _role(name):
    roles = getattr(QtWidgets.QMessageBox, "ButtonRole", QtWidgets.QMessageBox)
    return getattr(roles, name)


def _parent(controller):
    try:
        parent = controller._parentWidget()
        if parent is not None:
            return parent
    except Exception:
        pass
    return getattr(controller, "window", None)


def _remember(controller, item):
    try:
        core.mark_seen(core.receipt_path(controller.organizer), item)
        return True
    except Exception:
        logger.exception("保存公告已读状态失败")
        return False


def _open_page(controller, item, link, parent=None):
    try:
        core._validate_download_page(link["url"])
        opened = QtGui.QDesktopServices.openUrl(QtCore.QUrl(link["url"]))
        if not opened:
            raise core.AnnouncementError("系统无法打开网盘页面，请检查默认浏览器。")
        _remember(controller, item)
        if link.get("code"):
            QtWidgets.QMessageBox.information(
                parent or _parent(controller), "网盘提取码",
                "{} 提取码：{}".format(link["name"], link["code"])
            )
        return True
    except Exception as exc:
        logger.exception("打开公告网盘页面失败")
        QtWidgets.QMessageBox.warning(
            parent or _parent(controller), "网盘页面无法打开", str(exc)
        )
        return False


class CheckWorker(QThread):
    ok = pyqtSignal(object)
    bad = pyqtSignal(str)

    def run(self):
        try:
            self.ok.emit(core.fetch_announcements())
        except Exception as exc:
            self.bad.emit(str(exc))


def startup_check(controller):
    if getattr(controller, "_xingli_announcement_worker", None) is not None:
        return
    worker = CheckWorker()
    controller._xingli_announcement_worker = worker
    result = {"items": None, "error": None}
    worker.ok.connect(lambda records: result.__setitem__("items", records))
    worker.bad.connect(lambda error: result.__setitem__("error", error))

    def finished():
        controller._xingli_announcement_worker = None
        if result["error"]:
            logger.warning("星黎更新公告检查失败：%s", result["error"])
            return
        try:
            receipts = core.read_receipts(core.receipt_path(controller.organizer))
            pending = core.unseen(result["items"] or [], receipts)
            if not pending:
                return
            key = "|".join("{}:{}".format(x["id"], x["revision"]) for x in pending)
            if getattr(controller, "_xingli_announcement_notified", None) == key:
                return
            controller._xingli_announcement_notified = key
            QTimer.singleShot(0, lambda: _notify(controller, pending))
        except Exception:
            logger.exception("处理星黎更新公告失败")

    worker.finished.connect(finished)
    worker.start()


def _notify(controller, pending):
    if not pending:
        return
    item = pending[0]
    parent = _parent(controller)
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle("星黎 · 模组更新公告")
    box.setIcon(QtWidgets.QMessageBox.Icon.Information if hasattr(QtWidgets.QMessageBox, "Icon") else QtWidgets.QMessageBox.Information)
    box.setText(item["title"])
    content = item["message"]
    if len(pending) > 1:
        content += "\n\n另有 {} 条新公告，可以在「更新公告」中查看。".format(len(pending) - 1)
    box.setInformativeText(content)
    choices = {}
    for link in item["links"]:
        title = "打开" + link["name"]
        choices[box.addButton(title, _role("ActionRole"))] = link
    later = box.addButton("稍后提醒", _role("RejectRole"))
    dismiss = box.addButton("不再提醒此公告", _role("AcceptRole"))
    _exec(box)
    clicked = box.clickedButton()
    if clicked in choices:
        _open_page(controller, item, choices[clicked], parent)
    elif clicked is dismiss:
        _remember(controller, item)
    # "稍后提醒" and window-close never update the receipt.


class AnnouncementCenter(QtWidgets.QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.worker = None
        self.items = []
        self.setWindowTitle("星黎 · 更新公告")
        self.resize(760, 460)
        self._build()
        QTimer.singleShot(0, self.refresh)

    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel(
            "作者发布的着色器及其他模组下载公告。"
            "仅打开外部网盘，不会自动下载或覆盖当前 MOD。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.list_widget = QtWidgets.QListWidget(self)
        self.list_widget.currentRowChanged.connect(self._select)
        layout.addWidget(self.list_widget, 1)

        self.info = QtWidgets.QLabel("选择公告查看具体说明。")
        self.info.setWordWrap(True)
        self.info.setMinimumHeight(65)
        layout.addWidget(self.info)

        self.status = QtWidgets.QLabel("准备检查公告……")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton("刷新公告")
        self.refresh_button.clicked.connect(self.refresh)
        buttons.addWidget(self.refresh_button)
        self.link_buttons = []
        for number in range(3):
            button = QtWidgets.QPushButton("打开下载页面")
            button.clicked.connect(lambda _checked=False, index=number: self.open_link(index))
            button.setEnabled(False)
            button.hide()
            buttons.addWidget(button)
            self.link_buttons.append(button)
        self.read_button = QtWidgets.QPushButton("标记此公告已读")
        self.read_button.clicked.connect(self.mark_selected_read)
        self.read_button.setEnabled(False)
        buttons.addWidget(self.read_button)
        close = QtWidgets.QPushButton("关闭")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def refresh(self):
        if self.worker is not None:
            return
        self.refresh_button.setEnabled(False)
        self.status.setText("正在从公告源检查更新……")
        worker = CheckWorker()
        self.worker = worker
        result = {"items": None, "error": None}
        worker.ok.connect(lambda items: result.__setitem__("items", items))
        worker.bad.connect(lambda err: result.__setitem__("error", err))

        def done():
            self.worker = None
            self.refresh_button.setEnabled(True)
            if result["error"]:
                self.status.setText("公告检查失败：" + result["error"])
                return
            self.items = result["items"] or []
            self.list_widget.clear()
            receipts = {}
            try:
                receipts = core.read_receipts(core.receipt_path(self.controller.organizer))
            except Exception:
                logger.exception("无法读取公告已读回执")
            for item in self.items:
                status = "新公告" if item in core.unseen([item], receipts) else "已读"
                self.list_widget.addItem(
                    "{}  [{}]  {}".format(item["published_at"], status, item["title"])
                )
            self.status.setText("共 {} 条公告。".format(len(self.items)))
            if self.items:
                self.list_widget.setCurrentRow(0)
            else:
                self._select(-1)

        worker.finished.connect(done)
        worker.start()

    def _selected(self):
        index = self.list_widget.currentRow()
        return self.items[index] if 0 <= index < len(self.items) else None

    def _select(self, _index):
        item = self._selected()
        self.read_button.setEnabled(item is not None)
        if item is None:
            self.info.setText("没有可显示的公告。")
        else:
            self.info.setText("{}\n{}".format(item["title"], item["message"]))
        for index, button in enumerate(self.link_buttons):
            link = item["links"][index] if item is not None and index < len(item["links"]) else None
            button.setVisible(link is not None)
            button.setEnabled(link is not None)
            if link is not None:
                button.setText("打开" + link["name"])

    def open_link(self, index):
        item = self._selected()
        if item is None or not 0 <= index < len(item["links"]):
            return
        if _open_page(self.controller, item, item["links"][index], self):
            self.status.setText("已打开网盘页面，请根据页面提示下载。")

    def mark_selected_read(self):
        item = self._selected()
        if item is None:
            return
        if _remember(self.controller, item):
            index = self.list_widget.currentRow()
            self.list_widget.item(index).setText(
                "{}  [已读]  {}".format(item["published_at"], item["title"])
            )
            self.status.setText("此公告已标记为已读。")
        else:
            self.status.setText("已读记录保存失败，请检查 MO2 插件数据目录权限。")

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            event.ignore()
            self.status.setText("正在检查公告，请完成后关闭。")
            return
        super().closeEvent(event)


def show_center(controller):
    dialog = getattr(controller, "_xingli_announcement_center", None)
    if dialog is not None and dialog.isVisible():
        dialog.raise_()
        dialog.activateWindow()
        return
    dialog = AnnouncementCenter(controller, _parent(controller))
    controller._xingli_announcement_center = dialog
    dialog.show()
