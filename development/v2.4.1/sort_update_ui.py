# coding: utf-8
"""MO2 sorting update notification and native-restore-compatible import UI."""
try:
    from PyQt6 import QtWidgets
    from PyQt6.QtCore import QThread, QTimer, pyqtSignal
except ImportError:
    from PyQt5 import QtWidgets
    from PyQt5.QtCore import QThread, QTimer, pyqtSignal

from . import sort_update_core as core
from . import stability

logger = stability.get_logger("sorting_updates")


def _exec(box):
    return box.exec() if hasattr(box,"exec") else box.exec_()


def _role(name):
    buttons = getattr(QtWidgets.QMessageBox, "ButtonRole", QtWidgets.QMessageBox)
    return getattr(buttons,name)


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
            if isinstance(window,QtWidgets.QMainWindow):
                return window
    return None


class CheckWorker(QThread):
    ok = pyqtSignal(object)
    bad = pyqtSignal(str)

    def run(self):
        try:
            self.ok.emit(core.fetch_releases())
        except Exception as exc:
            self.bad.emit(str(exc))


class DownloadWorker(QThread):
    ok = pyqtSignal(object)
    bad = pyqtSignal(str)

    def __init__(self,release,parent=None):
        super().__init__(parent)
        self.release=release

    def run(self):
        try:
            self.ok.emit(core.download_order(self.release))
        except Exception as exc:
            self.bad.emit(str(exc))


def startup_check(controller):
    """MO2 lifecycle, independent of Xingli helper main window."""
    if getattr(controller,"_xingli_sort_worker",None):
        return
    worker=CheckWorker()
    controller._xingli_sort_worker=worker
    result={"records":None,"error":None}
    worker.ok.connect(lambda data: result.__setitem__("records",data))
    worker.bad.connect(lambda err: result.__setitem__("error",err))

    def done():
        controller._xingli_sort_worker=None
        if result["error"]:
            logger.warning("排序推送检查失败：%s",result["error"])
            return
        try:
            receipts=core.read_receipts(core.receipt_path(controller.organizer))
            pending=core.unseen(result["records"] or [],receipts)
            if not pending:
                return
            key="|".join("%s:%s"%(x["id"],x["version"]) for x in pending)
            if getattr(controller,"_xingli_sort_notified",None)==key:
                return
            controller._xingli_sort_notified=key
            QTimer.singleShot(0,lambda: _notify(controller,pending))
        except Exception:
            logger.exception("处理排序更新推送失败")

    worker.finished.connect(done)
    worker.start()


def _notify(controller,updates):
    box=QtWidgets.QMessageBox(_parent(controller))
    box.setWindowTitle("星黎 · 排序备份更新")
    box.setText("发现 %d 份新的 MO2 排序备份。"%len(updates))
    box.setInformativeText(
        "下载后只会保存为 MO2 恢复列表中的新备份；不会立即改变当前排序。\n\n"+
        "\n".join("%s  v%s"%(item["title"],item["version"]) for item in updates[:9]))
    go=box.addButton("查看并下载",_role("AcceptRole"))
    box.addButton("稍后提醒",_role("RejectRole"))
    _exec(box)
    if box.clickedButton() is go:
        show_center(controller)


class SortCenter(QtWidgets.QDialog):
    def __init__(self,controller,parent=None):
        super().__init__(parent)
        self.controller=controller
        self.check_worker=None
        self.download_worker=None
        self.records=[]
        self.setWindowTitle("星黎 · 排序备份下载")
        self.resize(730,430)
        self._build()
        QTimer.singleShot(0,self.check)

    def _build(self):
        layout=QtWidgets.QVBoxLayout(self)
        help_text=QtWidgets.QLabel(
            "左侧排序：modlist.txt 备份；右侧排序：plugins.txt / loadorder.txt / lockedorder.txt 成套备份。\n"
            "仅下载到当前 MO2 Profile，不会自动还原、覆盖或更改当前排序。"
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.list=QtWidgets.QListWidget(self)
        layout.addWidget(self.list,1)
        self.status=QtWidgets.QLabel("准备检查排序更新……")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        controls=QtWidgets.QHBoxLayout()
        self.check_button=QtWidgets.QPushButton("检查更新")
        self.check_button.clicked.connect(self.check)
        controls.addWidget(self.check_button)
        controls.addStretch(1)
        self.download_button=QtWidgets.QPushButton("下载选中排序备份")
        self.download_button.clicked.connect(self.download)
        controls.addWidget(self.download_button)
        close=QtWidgets.QPushButton("关闭")
        close.clicked.connect(self.close)
        controls.addWidget(close)
        layout.addLayout(controls)
        self.download_button.setEnabled(False)

    def check(self):
        if self.check_worker is not None or self.download_worker is not None:
            return
        self.status.setText("正在通过国内镜像检查排序清单……")
        self.check_button.setEnabled(False)
        worker=CheckWorker(self)
        self.check_worker=worker
        result={"data":None,"error":None}
        worker.ok.connect(lambda x: result.__setitem__("data",x))
        worker.bad.connect(lambda x: result.__setitem__("error",x))

        def done():
            self.check_worker=None
            self.check_button.setEnabled(True)
            if result["error"]:
                self.status.setText("检查失败："+result["error"])
                self.download_button.setEnabled(False)
                return
            self.records=result["data"] or []
            self.list.clear()
            for rec in self.records:
                typename="左侧 MOD 排序" if rec["kind"]=="mods" else "右侧插件排序"
                self.list.addItem("%s   %s   v%s\n%s" % (
                    typename,rec["title"],rec["version"],rec["changelog"][:145]))
            self.download_button.setEnabled(bool(self.records))
            self.status.setText("找到 %d 份排序备份。下载后请在 MO2 的对应排序列表点击“还原”。"%len(self.records))
        worker.finished.connect(done)
        worker.start()

    def download(self):
        if self.download_worker is not None or self.check_worker is not None:
            return
        idx=self.list.currentRow()
        if not 0<=idx<len(self.records):
            QtWidgets.QMessageBox.information(self,"选择排序","先选择一条要下载的排序记录。")
            return
        try:
            profile=core.profile_folder(self.controller.organizer)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self,"Profile 无法确认",str(exc))
            return
        record=self.records[idx]
        box=QtWidgets.QMessageBox(self)
        box.setWindowTitle("下载排序备份")
        box.setText("将下载 %s 到当前 Profile：%s" % (record["title"],profile))
        box.setInformativeText(
            "安装结果仅是新增 MO2 可还原的备份，绝不覆盖 modlist.txt / plugins.txt 等当前排序文件。\n"
            "点击 MO2 对应列表的“还原”后，MO2 才会应用所选排序。\n"
            "请确保此备份与你目前的 MOD 配置相匹配。")
        yes=box.addButton("下载备份",_role("AcceptRole"))
        box.addButton("取消",_role("RejectRole"))
        _exec(box)
        if box.clickedButton() is not yes:
            return
        self.download_button.setEnabled(False)
        self.check_button.setEnabled(False)
        self.status.setText("正在下载并校验排序 ZIP……")
        worker=DownloadWorker(record,self)
        self.download_worker=worker
        result={"data":None,"error":None}
        worker.ok.connect(lambda x:result.__setitem__("data",x))
        worker.bad.connect(lambda x:result.__setitem__("error",x))

        def done():
            self.download_worker=None
            self.download_button.setEnabled(bool(self.records))
            self.check_button.setEnabled(True)
            if result["error"]:
                self.status.setText("下载失败："+result["error"])
                return
            try:
                # Re-verify target profile after asynchronous download.
                target=core.profile_folder(self.controller.organizer)
                if target.resolve()!=profile.resolve():
                    raise core.SortError("下载期间用户切换了 Profile，拒绝跨 Profile 写入")
                imported=core.install_as_backups(target,result["data"],record["kind"])
                core.save_receipt(core.receipt_path(self.controller.organizer),record)
                self.status.setText("下载完成："+", ".join(str(x) for x in imported["paths"]))
                btn_name="左侧 MOD 列表" if record["kind"]=="mods" else "右侧插件列表"
                QtWidgets.QMessageBox.information(
                    self,"排序备份已保存",
                    "已生成 %s 个不冲突的 MO2 排序备份文件。\n\n"
                    "备份后缀：%s\n路径：%s\n\n"
                    "请前往 MO2 的【%s】点击【还原】并选择该后缀。\n"
                    "本次未修改当前排序；实际还原由 MO2 原生功能完成。" %
                    (len(imported["paths"]),imported["suffix"],str(target),btn_name))
            except Exception as exc:
                logger.exception("写入排序备份失败")
                QtWidgets.QMessageBox.critical(self,"备份导入失败",str(exc))
        worker.finished.connect(done)
        worker.start()

    def closeEvent(self,event):
        if (self.check_worker and self.check_worker.isRunning()) or (
                self.download_worker and self.download_worker.isRunning()):
            event.ignore()
            self.status.setText("任务尚未结束，请完成下载后关闭。")
            return
        super().closeEvent(event)


def show_center(controller):
    dialog=getattr(controller,"_xingli_sort_center",None)
    if dialog is not None and dialog.isVisible():
        dialog.raise_()
        dialog.activateWindow()
        return
    dialog=SortCenter(controller,_parent(controller))
    controller._xingli_sort_center=dialog
    dialog.show()
