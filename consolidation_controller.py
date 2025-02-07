# coding=utf-8

import os
import webbrowser
import json
import urllib.request
import urllib.error
import mobase

try:
    import PyQt6.QtWidgets as QtWidgets
    import PyQt6.QtGui as QtGui
    from PyQt6.QtCore import QCoreApplication, Qt
except ImportError:
    import PyQt5.QtWidgets as QtWidgets
    import PyQt5.QtGui as QtGui
    from PyQt5.QtCore import QCoreApplication, Qt

class ConsolidationController(mobase.IPluginTool):
    NAME = "星黎整合管理器"  # 修改为中文名称
    VERSION_URL = "https://your-server/modpack/version.json"  # 替换为你的版本查询接口
    ORDER_URL = "https://your-server/modpack/order.txt"        # 替换为你的排序文件接口
    PLUGIN_UPDATE_URL = "https://silent-waterfall-efd4.a306435856.workers.dev/download"  # 替换为插件更新文件的下载链接
    PLUGIN_VERSION_URL = "https://silent-waterfall-efd4.a306435856.workers.dev/version"
    TUTORIAL_CATEGORIES = {
        "分类一": [
            {"name": "教程1", "url": "https://www.bilibili.com/video/BV1234567890"},
            {"name": "教程2", "url": "https://www.bilibili.com/video/BV1234567891"},
            {"name": "教程3", "url": "https://www.bilibili.com/video/BV1234567892"}
        ],
        "分类二": [
            {"name": "教程4", "url": "https://www.bilibili.com/video/BV0987654321"},
            {"name": "教程5", "url": "https://www.bilibili.com/video/BV0987654322"},
            {"name": "教程6", "url": "https://www.bilibili.com/video/BV0987654323"}
        ],
        "分类三": [
            {"name": "教程7", "url": "https://www.bilibili.com/video/BV1112223334"},
            {"name": "教程8", "url": "https://www.bilibili.com/video/BV1112223335"},
            {"name": "教程9", "url": "https://www.bilibili.com/video/BV1112223336"}
        ]
    }

    def __init__(self):
        super().__init__()
        self.organizer = None

    def init(self, organizer: mobase.IOrganizer):
        self.organizer = organizer
        return True

    def name(self) -> str:
        return self.NAME

    def author(self) -> str:
        return "YourName"

    def description(self) -> str:
        return u"管理整合包更新和教程"

    def version(self) -> mobase.VersionInfo:
        return mobase.VersionInfo(1, 0, 0)

    def settings(self) -> list:
        return []

    def displayName(self) -> str:
        return "星黎整合管理器"  # 直接返回中文字符串，避免编码问题

    def tooltip(self) -> str:
        return "管理整合包更新和教程"  # 直接返回中文字符串，避免编码问题

    def icon(self) -> QtGui.QIcon:
        return QtGui.QIcon()

    def display(self) -> None:
        self.window = QtWidgets.QDialog()
        self.window.setWindowTitle("星黎整合管理器")  # 直接设置中文标题，避免编码问题
        self.window.setWindowFlags(self.window.windowFlags() | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)

        layout = QtWidgets.QVBoxLayout()

        # Version Check Button
        version_button = QtWidgets.QPushButton("检查整合包版本")
        version_button.clicked.connect(self.check_version)
        layout.addWidget(version_button)

        # Order Update Button
        order_button = QtWidgets.QPushButton("检查排序更新")
        order_button.clicked.connect(self.check_order_updates)
        layout.addWidget(order_button)

        # Tutorial Button
        tutorial_button = QtWidgets.QPushButton("打开教程")
        tutorial_button.clicked.connect(self.open_tutorial)
        layout.addWidget(tutorial_button)
        
        # Update Plugin Button
        update_button = QtWidgets.QPushButton("插件更新")
        update_button.clicked.connect(self.update_plugin)
        layout.addWidget(update_button)

        self.window.setLayout(layout)
        self.window.setMinimumSize(400, 300)
        self.window.exec()

    def check_version(self):
        try:
            with urllib.request.urlopen(self.VERSION_URL, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                latest_version = data.get("version", "Unknown")
                QMessageBox.information(
                    None,
                    "整合包版本",
                    "最新整合包版本: {}".format(latest_version)
                )
        except urllib.error.URLError as e:
            QMessageBox.critical(
                None,
                "错误",
                "检查版本失败: {}".format(str(e))
            )

    def check_order_updates(self):
        try:
            with urllib.request.urlopen(self.ORDER_URL, timeout=10) as response:
                order_content = response.read().decode('utf-8')
                local_order_path = os.path.join(self.organizer.overwritePath(), "mod_order.txt")
                
                # Save new order to local file
                with open(local_order_path, "w", encoding="utf-8") as file:
                    file.write(order_content)
                
                QMessageBox.information(
                    None,
                    "成功",
                    "新的模组排序已下载并保存。"
                )
        except urllib.error.URLError as e:
            QMessageBox.critical(
                None,
                "错误",
                "下载新的排序失败: {}".format(str(e))
            )

    def open_tutorial(self):
        try:
            self.show_tutorial_window()  # 修改为正确的方法名称
        except Exception as e:
            QMessageBox.critical(
                None,
                "错误",
                "打开教程失败: {}".format(str(e))
            )
    def show_tutorial_window(self):
        # 创建一个窗口，用于显示教程分类和教程列表
        tutorial_window = QtWidgets.QDialog()
        tutorial_window.setWindowTitle("教程列表")
        tutorial_window.setWindowFlags(tutorial_window.windowFlags() | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)

        layout = QtWidgets.QVBoxLayout()

        # 分类选择
        category_label = QtWidgets.QLabel("选择分类:")
        layout.addWidget(category_label)

        category_combo = QtWidgets.QComboBox()
        category_combo.addItems(self.TUTORIAL_CATEGORIES.keys())
        layout.addWidget(category_combo)

        # 教程列表
        tutorial_list = QtWidgets.QListWidget()
        tutorial_list.itemDoubleClicked.connect(lambda item: self.open_tutorial_url(item.text()))
        layout.addWidget(tutorial_list)

        # 更新教程列表
        def update_tutorials():
            selected_category = category_combo.currentText()
            tutorials = self.TUTORIAL_CATEGORIES[selected_category]
            tutorial_list.clear()
            for tutorial in tutorials:
                tutorial_list.addItem(tutorial["name"])

        category_combo.currentIndexChanged.connect(update_tutorials)

        # 初始更新
        update_tutorials()

        tutorial_window.setLayout(layout)
        tutorial_window.adjustSize()
        tutorial_window.setFixedSize(tutorial_window.size())
        tutorial_window.exec()

    def open_tutorial_url(self, tutorial_name):
        # 根据教程名称找到对应的 URL 并打开
        for category in self.TUTORIAL_CATEGORIES.values():
            for tutorial in category:
                if tutorial["name"] == tutorial_name:
                    webbrowser.open(tutorial["url"])
                    return

    def update_plugin(self):
        try:
            # 获取云端插件版本
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/85.0.4183.121 Safari/537.36"
            }
            req = urllib.request.Request(self.PLUGIN_VERSION_URL, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
            remote_version = data.get("version", "0.0.0")

            # 获取本地插件版本
            local_version = self.version().toString()

            # 比较版本
            if version.parse(remote_version) > version.parse(local_version):
                # 下载新插件
                req = urllib.request.Request(self.PLUGIN_UPDATE_URL, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as response:
                    with open(__file__, "wb") as plugin_file:
                        plugin_file.write(response.read())
                QMessageBox.information(
                    None,
                    "更新成功",
                    "插件已更新到最新版本: {}".format(remote_version)
                )
            else:
                QMessageBox.information(
                    None,
                    "无需更新",
                    "当前插件已是最新版本: {}".format(local_version)
                )
        except urllib.error.URLError as e:
            QMessageBox.critical(
                None,
                "错误",
                "检查插件更新失败: {}".format(str(e))
            )
        except Exception as e:
            QMessageBox.critical(
                None,
                "错误",
                "更新插件时发生错误: {}".format(str(e))
            )
def createPlugin():
    return ConsolidationController()
