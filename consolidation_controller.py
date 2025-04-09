# coding=utf-8

import os
import re
import webbrowser
import json
import urllib.request
import urllib.error
import logging # 添加 logging 模块
import mobase
import configparser
import shutil
import time
import tempfile
import threading
import winreg
import configparser # 确保导入
from urllib.parse import urlparse

try:
    import PyQt6.QtWidgets as QtWidgets
    import PyQt6.QtGui as QtGui
    from PyQt6.QtCore import QCoreApplication, Qt, QThread, pyqtSignal, QTimer
except ImportError:
    import PyQt5.QtWidgets as QtWidgets
    import PyQt5.QtGui as QtGui
    from PyQt5.QtCore import QCoreApplication, Qt, QThread, pyqtSignal, QTimer

from .tutorial_data import TUTORIAL_CATEGORIES
from .network import Network

class ConsolidationController(mobase.IPluginTool):
    NAME = "星黎整合管理器"  # 修改为中文名称
    PLUGIN_UPDATE_URL = "https://silent-waterfall-efd4.a306435856.workers.dev/download"  # 替换为插件更新文件的下载链接
    PLUGIN_VERSION_URL = "https://silent-waterfall-efd4.a306435856.workers.dev/version"
    PLUGIN_CHANGELOG_URL = "https://silent-waterfall-efd4.a306435856.workers.dev/changelog"
    CONFIG_FILE_NAME = "version.ini" # ini 文件名
    DEFAULT_VERSION = "1.0.0" # 默认版本号

    AUTO_RESOLUTION_MOD_NAME = "自动分辨率设置-Auto Resolution"


    def __init__(self):
        super().__init__()
        self.organizer = None
        self.network = None
        self.server_version = None # 用于存储服务器版本号
        self.version_label = None # 用于稍后更新标签
        self.plugin_path = os.path.dirname(__file__) # 获取插件目录
        self.config_path = os.path.join(self.plugin_path, self.CONFIG_FILE_NAME) # ini 文件路径
        self.local_version = self._read_local_version() # 在初始化时读取

    def _read_local_version(self) -> str:
        """从 version.ini 读取本地版本号"""
        config = configparser.ConfigParser()
        try:
            if os.path.exists(self.config_path):
                config.read(self.config_path, encoding='utf-8')
                if 'Version' in config and 'local' in config['Version']:
                    version_str = config['Version']['local']
                    # 验证版本格式 (可选但推荐)
                    if re.match(r"^\d+(\.\d+)*$", version_str):
                        print(f"从 {self.CONFIG_FILE_NAME} 读取到版本: {version_str}")
                        return version_str
                    else:
                        print(f"警告: {self.CONFIG_FILE_NAME} 中的版本号格式无效: {version_str}。使用默认版本。")
                else:
                    print(f"警告: {self.CONFIG_FILE_NAME} 中缺少 [Version] 或 'local' 键。使用默认版本。")
            else:
                print(f"信息: 未找到 {self.CONFIG_FILE_NAME}。使用默认版本。")
        except Exception as e:
            print(f"读取 {self.CONFIG_FILE_NAME} 时出错: {e}。使用默认版本。")

        # 如果读取失败或文件不存在，返回默认值并尝试创建文件
        self._write_local_version(self.DEFAULT_VERSION) # 写入默认值
        return self.DEFAULT_VERSION

    def _write_local_version(self, version_str: str):
        """将本地版本号写入 version.ini"""
        config = configparser.ConfigParser()
        config['Version'] = {'local': version_str}
        try:
            with open(self.config_path, 'w', encoding='utf-8') as configfile:
                config.write(configfile)
            print(f"已将版本 {version_str} 写入 {self.CONFIG_FILE_NAME}")
        except Exception as e:
            print(f"写入 {self.CONFIG_FILE_NAME} 时出错: {e}")


    def init(self, organizer: mobase.IOrganizer):
        self.organizer = organizer
        # 初始化网络模块，使用读取到的本地版本
        print(f"使用的本地版本进行初始化: {self.local_version}") # 调试信息
        # 确保 Network 类已定义或导入
        # from .network import Network # 可能需要取消注释或调整
        self.network = Network(self, self.local_version, self.PLUGIN_VERSION_URL, self.PLUGIN_CHANGELOG_URL) # 传递 self

        # 尝试在初始化时同步获取服务器版本
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/85.0.4183.121 Safari/537.36"
            }
            req = urllib.request.Request(self.PLUGIN_VERSION_URL, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response: # 使用5秒超时
                data = json.loads(response.read().decode('utf-8'))
                self.server_version = data.get("version")
                print(f"启动时获取服务器版本成功: {self.server_version}")
        except Exception as e:
            print(f"启动时检查服务器版本失败: {str(e)}")
            self.server_version = None # 确保失败时为 None

        QTimer.singleShot(2000, self.show_welcome_dialog)
        return True

    def name(self) -> str:
        return self.NAME

    def author(self) -> str:
        return "YourName"

    def description(self) -> str:
        return u"管理整合包更新和教程"

    def version(self) -> mobase.VersionInfo:
        # 从读取到的 self.local_version 创建 VersionInfo 对象
        try:
            # 尝试解析版本字符串
            parts = list(map(int, self.local_version.split('.')))
            # 根据 mobase.VersionInfo 的构造函数填充参数
            major = parts[0] if len(parts) > 0 else 0
            minor = parts[1] if len(parts) > 1 else 0
            subminor = parts[2] if len(parts) > 2 else 0
            # MO2 的 VersionInfo 可能还有第四个参数 (revision/release type)，这里简化处理
            # return mobase.VersionInfo(major, minor, subminor, 0) # 假设第四个参数是 0
            # 根据 mobase 文档，通常是 major, minor, subminor
            return mobase.VersionInfo(major, minor, subminor)
        except ValueError:
            print(f"错误: 无法解析本地版本号 '{self.local_version}'。返回默认版本 0.0.0。")
            # 如果解析失败，返回一个默认的 VersionInfo 对象
            return mobase.VersionInfo(0, 0, 0)

    def settings(self) -> list:
        return []

    def displayName(self) -> str:
        return "星黎MO2小助手"  

    def tooltip(self) -> str:
        return "管理整合包更新和教程"  

    def icon(self) -> QtGui.QIcon:
        try:
            # 尝试加载图标
            return QtGui.QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        except Exception:
            return QtGui.QIcon()

    # +++ 添加新的欢迎对话框方法 +++
    def show_welcome_dialog(self):
        welcome_dialog = QtWidgets.QDialog()
        welcome_dialog.setWindowTitle("欢迎使用星黎 MO2 小助手")
        welcome_dialog.setWindowFlags(welcome_dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint) # 移除帮助按钮
        welcome_dialog.setMinimumWidth(350)

        layout = QtWidgets.QVBoxLayout(welcome_dialog)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # 欢迎标题
        title_label = QtWidgets.QLabel("✨ 欢迎使用星黎 MO2 小助手 ✨")
        title_font = QtGui.QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        # 欢迎信息
        info_label = QtWidgets.QLabel("感谢您使用星黎整合！\n您可以选择打开小助手管理整合包，或直接跳过。")
        info_label.setAlignment(Qt.AlignCenter)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # 按钮布局
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(10)

        # 按钮样式 (可以复用 display 中的样式)
        button_style = """
        QPushButton {
            background-color: #4a86e8; color: white; border: none;
            padding: 10px 15px; border-radius: 5px; font-size: 12px;
        }
        QPushButton:hover { background-color: #3a76d8; }
        QPushButton:pressed { background-color: #2a66c8; }
        """

        # 打开助手按钮
        open_button = QtWidgets.QPushButton("🚀 打开小助手")
        open_button.setStyleSheet(button_style)
        # 尝试获取插件图标，如果失败则不设置图标
        try:
            icon = self.icon()
            if not icon.isNull():
                 open_button.setIcon(icon)
        except Exception:
            pass # 忽略图标加载错误
        def open_and_close():
            self.display()
            welcome_dialog.accept()
        open_button.clicked.connect(open_and_close)
        button_layout.addWidget(open_button)

        # 跳过按钮
        skip_button = QtWidgets.QPushButton("➡️ 跳过")
        skip_button.setStyleSheet(button_style.replace("#4a86e8", "#777777").replace("#3a76d8", "#666666").replace("#2a66c8", "#555555")) # 灰色样式
        skip_button.clicked.connect(welcome_dialog.reject)
        button_layout.addWidget(skip_button)

        layout.addLayout(button_layout)
        welcome_dialog.setLayout(layout)

        # 显示对话框
        welcome_dialog.exec()
    # +++ 结束添加 +++


    def display(self) -> None:
        # 每次调用都重置窗口和标签引用，确保使用新对象
        self.window = QtWidgets.QDialog()
        self.version_label = None # <-- 重置 version_label
        self.window.setWindowTitle("星黎MO2小助手")
        self.window.setWindowFlags(self.window.windowFlags() | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)

        # 创建主布局
        main_layout = QtWidgets.QVBoxLayout()
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        # 添加标题标签
        title_label = QtWidgets.QLabel("星黎MO2小助手")
        title_font = QtGui.QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)
        
        # 添加分隔线
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        main_layout.addWidget(line)
        
        # 创建按钮网格布局
        button_layout = QtWidgets.QGridLayout()
        button_layout.setSpacing(10)
        
        # 创建统一的按钮样式
        button_style = """
        QPushButton {
            background-color: #4a86e8;
            color: white;
            border: none;
            padding: 10px;
            border-radius: 5px;
            font-size: 12px;
            min-height: 40px;
        }
        QPushButton:hover {
            background-color: #3a76d8;
        }
        QPushButton:pressed {
            background-color: #2a66c8;
        }
        """
        
        # 分辨率设置按钮
        resolution_button = QtWidgets.QPushButton("分辨率设置")
        resolution_button.setStyleSheet(button_style)
        resolution_button.setIcon(QtGui.QIcon.fromTheme("preferences-desktop-display"))
        #resolution_button.clicked.connect(self.show_resolution_settings)
        button_layout.addWidget(resolution_button, 0, 0)

        # ENB管理按钮
        enb_button = QtWidgets.QPushButton("ENB管理")
        enb_button.setStyleSheet(button_style)
        enb_button.setIcon(QtGui.QIcon.fromTheme("applications-graphics"))
        enb_button.clicked.connect(self.manage_enb)
        button_layout.addWidget(enb_button, 0, 1)

        # 教程按钮
        tutorial_button = QtWidgets.QPushButton("查看教程")
        tutorial_button.setStyleSheet(button_style)
        tutorial_button.setIcon(QtGui.QIcon.fromTheme("help-contents"))
        tutorial_button.clicked.connect(self.open_tutorial)
        button_layout.addWidget(tutorial_button, 1, 0)
        
        # 更新按钮
        update_button = QtWidgets.QPushButton("检查更新")
        update_button.setStyleSheet(button_style)
        update_button.setIcon(QtGui.QIcon.fromTheme("system-software-update"))
        update_button.clicked.connect(self.update_plugin)
        button_layout.addWidget(update_button, 1, 1)
        
        # 添加按钮布局到主布局
        main_layout.addLayout(button_layout)
        
        # 添加说明文本
        info_text = QtWidgets.QLabel("提示: 分辨率设置由MOD自动调节插件功能暂不可用，ENB管理可以快速切换不同的ENB预设。")
        info_text.setWordWrap(True)
        info_text.setStyleSheet("color: #666; font-style: italic;")
        main_layout.addWidget(info_text)
        logging.debug("Entering display method") # 添加日志
        
        # 添加版本信息 (本地和服务器)
        local_version_str = self.local_version # 使用从 ini 读取的版本
        version_text = f"本地版本: {local_version_str}"
        label_style = "color: #999; font-size: 10px;" # 默认样式

        if self.server_version:
            version_text += f" / 服务器: {self.server_version}"
            # 比较版本
            if self._compare_versions(self.server_version, local_version_str) > 0:
                # 服务器版本更新，突出显示
                label_style = "color: red; font-size: 10px; font-weight: bold;"

        # 创建或更新标签
        logging.debug(f"Checking version_label. Is None: {self.version_label is None}") # 添加日志
        if self.version_label is None:
            self.version_label = QtWidgets.QLabel(version_text)
            self.version_label.setAlignment(Qt.AlignRight)
            main_layout.addWidget(self.version_label)
            self.version_label.setStyleSheet(label_style) # <-- 设置样式
        else:
            # 如果标签已存在，先检查窗口是否还存活
            # (假设 self.window 是包含 self.version_label 的窗口)
            if not self.window or not self.window.isVisible():
                 logging.warning("Window or version_label's parent is no longer visible/valid. Skipping update.")
                 return # 窗口无效，直接返回

            # 窗口有效，继续更新
            logging.debug("version_label exists and window is visible. Attempting to update text.") # 更新日志信息
            try:
                # 调试日志：访问 winId (可以保留或移除)
                logging.debug(f"Accessing version_label.winId(): {self.version_label.winId()}")
            except RuntimeError as e:
                # 如果在这里仍然出错，说明 isVisible 检查可能不够，或者对象在检查后瞬间失效
                logging.error(f"RuntimeError accessing version_label even after visibility check: {e}")
                return # 出错则直接返回

            logging.debug(f"Calling setText with: '{version_text}'")
            self.version_label.setText(version_text)
            self.version_label.setStyleSheet(label_style) # <-- 同样设置样式

        self.window.setLayout(main_layout)
        self.window.setMinimumSize(400, 300)
        logging.debug("Before window.exec()") # 添加日志
        self.window.exec()
        logging.debug("After window.exec()") # 添加日志

    def check_version(self):
        try:
            with urllib.request.urlopen(self.VERSION_URL, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                latest_version = data.get("version", "Unknown")
                QtWidgets.QMessageBox.information(
                    None,
                    "整合包版本",
                    "最新整合包版本: {}".format(latest_version)
                )
        except urllib.error.URLError as e:
            QtWidgets.QMessageBox.critical(
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
                
                QtWidgets.QMessageBox.information(
                    None,
                    "成功",
                    "新的模组排序已下载并保存。"
                )
        except urllib.error.URLError as e:
            QtWidgets.QMessageBox.critical(
                None,
                "错误",
                "下载新的排序失败: {}".format(str(e))
            )
    

    def _get_game_path_from_registry(self):
        """从注册表获取游戏安装路径"""
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition"
            )
            return winreg.QueryValueEx(key, "Installed Path")[0]
        except Exception:
            return None

    def _prompt_for_game_path(self):
        """弹出文件夹选择对话框让用户手动选择游戏路径"""
        QtWidgets.QMessageBox.information(
            None,
            "未找到游戏路径",
            "未能自动找到有效的游戏安装路径。\n请手动选择您的《上古卷轴V：天际特别版》游戏根目录。"
        )
        selected_path = QtWidgets.QFileDialog.getExistingDirectory(
            None,
            "请选择游戏根目录 (包含 SkyrimSE.exe 或 skse64_loader.exe 的文件夹)",
            # 可以尝试提供一个默认起始路径，例如用户的主目录或 C 盘根目录
            os.path.expanduser("~")
        )

        if selected_path:
            # 检查选择的路径下是否存在 skse64_loader.exe
            skse_path = os.path.join(selected_path, "skse64_loader.exe")
            if os.path.exists(skse_path):
                QtWidgets.QMessageBox.information(
                    None,
                    "路径确认",
                    f"已选择路径：{selected_path}\n找到 skse64_loader.exe。"
                )
                return skse_path
            else:
                # 也检查 SkyrimSE.exe 作为备选
                skyrim_exe_path = os.path.join(selected_path, "SkyrimSE.exe")
                if os.path.exists(skyrim_exe_path):
                     QtWidgets.QMessageBox.warning(
                        None,
                        "路径警告",
                        f"在 {selected_path} 中找到了 SkyrimSE.exe，但未找到 skse64_loader.exe。\n请确保 SKSE 已正确安装。"
                     )
                     # 严格要求 skse_loader.exe 存在
                     QtWidgets.QMessageBox.critical(
                         None,
                         "错误",
                         f"在选择的目录 {selected_path} 中未找到 skse64_loader.exe。\n请确保选择了正确的游戏根目录并且 SKSE 已安装。"
                     )
                     return None
                else:
                    QtWidgets.QMessageBox.critical(
                        None,
                        "错误",
                        f"选择的目录 {selected_path} 似乎不是有效的游戏根目录（未找到 SkyrimSE.exe 或 skse64_loader.exe）。"
                    )
                    return None
        else:
            # 用户取消了选择
            QtWidgets.QMessageBox.warning(
                None,
                "操作取消",
                "用户取消了路径选择。"
            )
            return None
    
    def open_tutorial(self):
        try:
            self.show_tutorial_window()  # 修改为正确的方法名称
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                None,
                "错误",
                "打开教程失败: {}".format(str(e))
            )
    def show_tutorial_window(self):
        # 创建一个窗口，用于显示教程分类和教程列表
        tutorial_window = QtWidgets.QDialog()
        tutorial_window.setWindowTitle("星黎整合包教程中心")
        tutorial_window.setWindowFlags(tutorial_window.windowFlags() | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)

        # 创建主布局
        main_layout = QtWidgets.QVBoxLayout()
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        # 添加标题
        title_label = QtWidgets.QLabel("教程资源中心")
        title_font = QtGui.QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)
        
        # 添加说明文本
        description_label = QtWidgets.QLabel("选择分类并双击教程项目打开相关教程")
        description_label.setStyleSheet("color: #606060;")
        description_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(description_label)
        
        # 添加分隔线
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        main_layout.addWidget(line)

        # 创建水平布局用于分类选择
        category_layout = QtWidgets.QHBoxLayout()
        
        # 分类选择
        category_label = QtWidgets.QLabel("选择分类:")
        category_label.setStyleSheet("font-weight: bold;")
        category_layout.addWidget(category_label)

        category_combo = QtWidgets.QComboBox()
        category_combo.setStyleSheet("""
            QComboBox {
                border: 1px solid #c0c0c0;
                border-radius: 3px;
                padding: 5px;
                min-width: 200px;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 20px;
                border-left: 1px solid #c0c0c0;
            }
        """)
        category_combo.addItems(TUTORIAL_CATEGORIES.keys())
        category_layout.addWidget(category_combo)
        
        # 添加搜索框
        search_layout = QtWidgets.QHBoxLayout()
        search_label = QtWidgets.QLabel("搜索:")
        search_label.setStyleSheet("font-weight: bold;")
        search_layout.addWidget(search_label)
        
        search_input = QtWidgets.QLineEdit()
        search_input.setPlaceholderText("输入关键词搜索教程...")
        search_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #c0c0c0;
                border-radius: 3px;
                padding: 5px;
            }
        """)
        search_layout.addWidget(search_input)
        
        # 将分类和搜索布局添加到主布局
        main_layout.addLayout(category_layout)
        main_layout.addLayout(search_layout)

        # 教程列表
        tutorial_list = QtWidgets.QListWidget()
        tutorial_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #c0c0c0;
                border-radius: 3px;
                padding: 5px;
                background-color: #ffffff;
                alternate-background-color: #f7f7f7;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #e0e0e0;
            }
            QListWidget::item:hover {
                background-color: #e6f0ff;
            }
            QListWidget::item:selected {
                background-color: #cce0ff;
                color: black;
            }
        """)
        tutorial_list.setAlternatingRowColors(True)
        tutorial_list.itemDoubleClicked.connect(lambda item: self.open_tutorial_url(item.text()))
        main_layout.addWidget(tutorial_list)
        
        # 添加提示标签
        hint_label = QtWidgets.QLabel("提示: 双击列表项打开对应教程")
        hint_label.setStyleSheet("color: #808080; font-style: italic;")
        hint_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(hint_label)
        
        # 添加按钮布局
        button_layout = QtWidgets.QHBoxLayout()
        
        # 打开按钮
        open_button = QtWidgets.QPushButton("打开选中教程")
        open_button.setStyleSheet("""
            QPushButton {
                background-color: #4a86e8;
                color: white;
                border: none;
                border-radius: 3px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #3a76d8;
            }
            QPushButton:pressed {
                background-color: #2a66c8;
            }
        """)
        open_button.clicked.connect(lambda: self.open_tutorial_url(tutorial_list.currentItem().text()) if tutorial_list.currentItem() else None)
        button_layout.addWidget(open_button)
        
        # 关闭按钮
        close_button = QtWidgets.QPushButton("关闭")
        close_button.setStyleSheet("""
            QPushButton {
                background-color: #f0f0f0;
                border: 1px solid #c0c0c0;
                border-radius: 3px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
            QPushButton:pressed {
                background-color: #d0d0d0;
            }
        """)
        close_button.clicked.connect(tutorial_window.close)
        button_layout.addWidget(close_button)
        
        main_layout.addLayout(button_layout)

        # 更新教程列表函数
        def update_tutorials():
            selected_category = category_combo.currentText()
            search_text = search_input.text().lower()
            tutorials = TUTORIAL_CATEGORIES[selected_category]
            tutorial_list.clear()
            
            for tutorial in tutorials:
                # 如果搜索框有内容，则过滤
                if search_text and search_text not in tutorial["name"].lower():
                    continue
                tutorial_list.addItem(tutorial["name"])

        # 连接信号
        category_combo.currentIndexChanged.connect(update_tutorials)
        search_input.textChanged.connect(update_tutorials)

        # 初始更新
        update_tutorials()

        tutorial_window.setLayout(main_layout)
        tutorial_window.resize(500, 500)
        tutorial_window.exec()
    def open_tutorial_url(self, tutorial_name):
         # 根据教程名称找到对应的 URL 并打开
         for category in TUTORIAL_CATEGORIES.values():
             for tutorial in category:
               if tutorial["name"] == tutorial_name:
                    webbrowser.open(tutorial["url"])
                    return

    def update_plugin(self):
        """
        检查并更新插件
        """
        # 使用Network类检查更新
        if self.network:
            self.network.check_for_updates(self.window)
        else:
            # 如果Network类未初始化，则初始化并检查更新
            version_str = str(self.version())
            self.network = Network(version_str, self.PLUGIN_VERSION_URL)
            self.network.check_for_updates(self.window)
    
    def manage_enb(self):
        # 创建 ENB 管理窗口
        enb_window = QtWidgets.QDialog()
        enb_window.setWindowTitle("ENB 管理")
        enb_window.setWindowFlags(enb_window.windowFlags() | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)

        # 创建主布局
        main_layout = QtWidgets.QVBoxLayout()
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        # 添加标题标签
        title_label = QtWidgets.QLabel("ENB 预设管理")
        title_font = QtGui.QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)
        
        # 添加说明文本
        info_text = QtWidgets.QLabel("选择一个ENB预设，然后点击「导入ENB」按钮将其应用到游戏中。")
        info_text.setWordWrap(True)
        info_text.setStyleSheet("color: #666; margin-bottom: 10px;")
        main_layout.addWidget(info_text)

        # 创建列表和按钮的水平布局
        content_layout = QtWidgets.QHBoxLayout()
        
        # 左侧：ENB列表部分
        list_layout = QtWidgets.QVBoxLayout()
        
        # 初始化实例变量 self.enb_list
        enb_list_label = QtWidgets.QLabel("可用ENB预设:")
        enb_list_label.setStyleSheet("font-weight: bold;")
        list_layout.addWidget(enb_list_label)
        
        self.enb_list = QtWidgets.QListWidget()
        self.enb_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ccc;
                border-radius: 4px;
                background-color: #f8f8f8;
                padding: 5px;
            }
            QListWidget::item {
                padding: 5px;
                border-bottom: 1px solid #eee;
            }
            QListWidget::item:selected {
                background-color: #4a86e8;
                color: white;
            }
        """)
        list_layout.addWidget(self.enb_list)
        
        content_layout.addLayout(list_layout)
        
        # 右侧：按钮部分
        button_layout = QtWidgets.QVBoxLayout()
        button_layout.setAlignment(Qt.AlignTop)
        button_layout.setSpacing(10)
        
        # 按钮样式
        button_style = """
        QPushButton {
            background-color: #4a86e8;
            color: white;
            border: none;
            padding: 10px;
            border-radius: 5px;
            font-size: 12px;
            min-height: 40px;
            min-width: 120px;
        }
        QPushButton:hover {
            background-color: #3a76d8;
        }
        QPushButton:pressed {
            background-color: #2a66c8;
        }
        """
        
        # 安装、导入和禁用 ENB 按钮
        install_button = QtWidgets.QPushButton("安装 ENB")
        install_button.setStyleSheet(button_style.replace("#4a86e8", "#4CAF50").replace("#3a76d8", "#45a049").replace("#2a66c8", "#367c39")) # Green color
        install_button.setIcon(QtGui.QIcon.fromTheme("list-add"))
        install_button.setToolTip("从文件夹安装新的ENB预设") 

        start_button = QtWidgets.QPushButton("应用 ENB")
        start_button.setStyleSheet(button_style)
        start_button.setIcon(QtGui.QIcon.fromTheme("document-import"))
        start_button.setToolTip("将选中的ENB预设应用到游戏")

        stop_button = QtWidgets.QPushButton("禁用 ENB")
        stop_button.setStyleSheet(button_style.replace("#4a86e8", "#e84a4a").replace("#3a76d8", "#d83a3a").replace("#2a66c8", "#c82a2a")) # Red color
        stop_button.setIcon(QtGui.QIcon.fromTheme("process-stop"))
        stop_button.setToolTip("移除当前应用的ENB文件")

        button_layout.addWidget(install_button) 
        button_layout.addWidget(start_button)
        button_layout.addWidget(stop_button)
        button_layout.addStretch()
        
        content_layout.addLayout(button_layout)
        main_layout.addLayout(content_layout)
        
        # 添加状态标签
        self.enb_status_label = QtWidgets.QLabel("")
        self.enb_status_label.setStyleSheet("color: #666; font-style: italic;")
        self.enb_status_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self.enb_status_label)

        # 读取 Mod Organizer 2 的配置文件
        mo_config = configparser.ConfigParser()
        mo_config_path = os.path.join(self.organizer.basePath(), "ModOrganizer.ini")
        if not os.path.exists(mo_config_path):
            # 提示用户手动选择路径
            QtWidgets.QMessageBox.critical(
                None,
                "错误",
                "未找到 ModOrganizer.ini 文件: {}".format(mo_config_path)
            )
            # 弹出文件夹选择对话框
            game_path_selected = QtWidgets.QFileDialog.getExistingDirectory(
                caption="选择游戏路径",
                directory=QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.DocumentsLocation)
            )
            if game_path_selected:  # 用户选择了路径
                self.game_path = game_path_selected
            else:  # 用户取消选择，退出
                return
        else:
            mo_config.read(mo_config_path)
            try:
                game_path_str = mo_config.get("General", "gamePath")
                if game_path_str.startswith("@ByteArray("):
                    game_path_str = game_path_str[len("@ByteArray("):-1]
                self.game_path = os.path.normpath(game_path_str)
            except (configparser.NoOptionError, configparser.NoSectionError):
                QtWidgets.QMessageBox.critical(
                    None,
                    "错误",
                    "无法从 ModOrganizer.ini 中读取 gamePath。"
                )
                # 弹出文件夹选择对话框
                game_path_selected = QtWidgets.QFileDialog.getExistingDirectory(
                    caption="选择游戏路径",
                    directory=QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.DocumentsLocation)
                )
                if game_path_selected:  # 用户选择了路径
                    self.game_path = game_path_selected
                else:  # 用户取消选择，退出
                    return

        # 检查游戏路径是否存在
        if not os.path.exists(self.game_path):
            QtWidgets.QMessageBox.critical(
                None,
                "错误",
                "游戏路径不存在: {}".format(self.game_path)
            )
            return

        # 检查 ENB 备份路径是否存在
        self.enb_backup_path = os.path.join(self.game_path, "ENB备份")
        if not os.path.exists(self.enb_backup_path):
            try:
                os.makedirs(self.enb_backup_path)
                QtWidgets.QMessageBox.information(
                    None,
                    "提示",
                    f"ENB备份文件夹不存在，已自动创建:\n{self.enb_backup_path}"
                )
            except OSError as e:
                QtWidgets.QMessageBox.critical(
                    None,
                    "错误",
                    f"创建ENB备份文件夹失败: {e}"
                )
                return

        # 填充 ENB 列表 (调用新的辅助方法)
        self.refresh_enb_list()

        # 定义需要移动的文件和文件夹列表
        self.enb_files_and_folders = [
            "enbseries",
            "reshade-shaders",
            "d3d11.dll",
            "d3dcompiler_46e.dll",
            "enblocal.ini",
            "enbseries.ini",
            "dxgi.dll"
        ]

        # 连接按钮信号
        install_button.clicked.connect(self.install_enb) # 连接安装按钮
        start_button.clicked.connect(self.start_enb)
        stop_button.clicked.connect(self.stop_enb)
        
        # 设置窗口布局
        enb_window.setLayout(main_layout)
        enb_window.setMinimumSize(500, 400)
        enb_window.exec()
        # 启动 ENB 功能
    def start_enb(self):
        try:
            # 检查 ENB 列表是否初始化
            if not hasattr(self, 'enb_list') or self.enb_list is None:
                QtWidgets.QMessageBox.critical(
                    None,
                    "错误",
                    "ENB 列表未初始化，请先打开 ENB 管理窗口。"
                )
                return

            selected_item = self.enb_list.currentItem()
            if not selected_item:
                QtWidgets.QMessageBox.warning(
                    None,
                    "警告",
                    "请先选择一个 ENB！"
                )
                return

            enb_name = selected_item.text()
            enb_source_path = os.path.join(self.enb_backup_path, enb_name)
            game_path = self.game_path

            # 验证源路径是否存在
            if not os.path.exists(enb_source_path):
                QtWidgets.QMessageBox.critical(
                    None,
                    "错误",
                    f"ENB 源文件夹不存在: {enb_source_path}"
                )
                return

            # 步骤 1: 删除游戏目录中的旧 ENB 文件
            for item in self.enb_files_and_folders:
                target_path = os.path.join(game_path, item)
                if os.path.exists(target_path):
                    try:
                        if os.path.isfile(target_path):
                            os.remove(target_path)
                        elif os.path.isdir(target_path):
                            shutil.rmtree(target_path)
                    except Exception as e:
                        QtWidgets.QMessageBox.critical(
                            None,
                            "错误",
                            f"删除文件失败: {target_path}\n错误信息: {str(e)}"
                        )
                        return

            # 步骤 2: 复制新 ENB 到游戏目录
            for item in self.enb_files_and_folders:
                source_item = os.path.join(enb_source_path, item)
                target_item = os.path.join(game_path, item)

                try:
                    if os.path.isfile(source_item):
                        shutil.copy2(source_item, target_item)  # 复制文件并保留元数据
                    elif os.path.isdir(source_item):
                        shutil.copytree(
                            source_item,
                            target_item,
                            dirs_exist_ok=True  # 允许覆盖目录
                        )
                except Exception as e:
                    QtWidgets.QMessageBox.critical(
                        None,
                        "错误",
                        f"复制文件失败: {source_item} → {target_item}\n错误信息: {str(e)}"
                    )
                    return

            QtWidgets.QMessageBox.information(
                None,
                "成功",
                f"ENB [{enb_name}] 已成功部署到游戏目录！"
            )

        except Exception as e:
            QtWidgets.QMessageBox.critical(
                None,
                "未知错误",
                f"操作失败: {str(e)}"
            )


    # 关闭 ENB 功能
    def stop_enb(self):
        game_path = self.game_path
        for item in self.enb_files_and_folders:
                target_path = os.path.join(game_path, item)
                if os.path.exists(target_path):
                    try:
                        if os.path.isfile(target_path):
                            os.remove(target_path)
                        elif os.path.isdir(target_path):
                            shutil.rmtree(target_path)
                    except Exception as e:
                        QtWidgets.QMessageBox.critical(
                            None,
                            "错误",
                            f"删除文件失败: {target_path}\n错误信息: {str(e)}"
                        )
                        return
        QtWidgets.QMessageBox.information(
        None,
        "成功",
        f"ENB已成功禁用！"
        )
    # 新增：安装 ENB 的方法
    def install_enb(self):
        try:
            # 1. 选择源文件夹
            source_dir = QtWidgets.QFileDialog.getExistingDirectory(
                None,
                "选择包含 ENB 预设文件的文件夹",
                self.game_path # 从游戏路径开始浏览，方便用户
            )
            if not source_dir:
                return # 用户取消

            # 2. 获取预设名称
            preset_name, ok = QtWidgets.QInputDialog.getText(
                None,
                "输入 ENB 预设名称",
                "为这个 ENB 预设命名:",
                QtWidgets.QLineEdit.Normal,
                os.path.basename(source_dir) # 建议使用源文件夹名称作为默认值
            )
            if not ok or not preset_name.strip():
                QtWidgets.QMessageBox.warning(None, "取消", "未提供有效的预设名称。")
                return

            preset_name = preset_name.strip()
            target_path = os.path.join(self.enb_backup_path, preset_name)

            # 3. 检查是否已存在并处理覆盖
            if os.path.exists(target_path):
                # 使用 PyQt6/PyQt5 兼容的方式引用 StandardButton
                try:
                    # 尝试 PyQt6
                    msg_box_buttons = QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
                    no_button = QtWidgets.QMessageBox.StandardButton.No
                except AttributeError:
                    # 回退到 PyQt5
                    msg_box_buttons = QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
                    no_button = QtWidgets.QMessageBox.No

                reply = QtWidgets.QMessageBox.question(
                    None,
                    "确认覆盖",
                    f"名为 '{preset_name}' 的 ENB 预设已存在。\n是否要覆盖它？",
                    msg_box_buttons,
                    no_button
                )

                # 同样需要兼容 PyQt6/PyQt5 的返回值比较
                try:
                    # 尝试 PyQt6
                    should_continue = (reply == QtWidgets.QMessageBox.StandardButton.Yes)
                except AttributeError:
                     # 回退到 PyQt5
                    should_continue = (reply == QtWidgets.QMessageBox.Yes)

                if not should_continue:
                    QtWidgets.QMessageBox.information(None, "操作取消", "安装已取消。")
                    return
                else:
                    try:
                        shutil.rmtree(target_path) # 覆盖前先删除旧的
                    except Exception as e:
                         QtWidgets.QMessageBox.critical(None, "错误", f"无法删除旧的预设文件夹: {target_path}\n错误: {str(e)}")
                         return

            # 4. 创建目录并复制文件
            try:
                shutil.copytree(source_dir, target_path)

                # 5. 刷新列表
                self.refresh_enb_list()
                QtWidgets.QMessageBox.information(None, "成功", f"ENB 预设 '{preset_name}' 已成功安装！")

            except Exception as e:
                QtWidgets.QMessageBox.critical(None, "安装失败", f"复制 ENB 文件时出错: {str(e)}")
                # 清理可能部分创建的文件夹
                if os.path.exists(target_path):
                    try:
                        shutil.rmtree(target_path)
                    except Exception:
                        pass # 忽略清理错误

        except Exception as e:
            QtWidgets.QMessageBox.critical(None, "未知错误", f"安装 ENB 时发生错误: {str(e)}")


    # 新增：刷新 ENB 列表的方法
    def refresh_enb_list(self):
        # 检查 ENB 列表控件和备份路径是否存在
        if not hasattr(self, 'enb_list') or self.enb_list is None:
            print("错误: ENB 列表控件未初始化。") # 或者记录日志
            return
        if not hasattr(self, 'enb_backup_path') or not self.enb_backup_path or not os.path.exists(self.enb_backup_path):
             if hasattr(self, 'enb_status_label'):
                 self.enb_status_label.setText("错误: ENB备份路径无效或不存在。")
             print(f"错误: ENB 备份路径无效或不存在: {getattr(self, 'enb_backup_path', '未设置')}")
             return # 如果路径问题无法解决，则退出

        self.enb_list.clear()
        try:
            presets = [d for d in os.listdir(self.enb_backup_path) if os.path.isdir(os.path.join(self.enb_backup_path, d))]
            if presets:
                self.enb_list.addItems(presets)
                if hasattr(self, 'enb_status_label'):
                    self.enb_status_label.setText(f"找到 {len(presets)} 个 ENB 预设。")
            else:
                if hasattr(self, 'enb_status_label'):
                    self.enb_status_label.setText("在 ENB备份 文件夹中未找到任何预设。")

        except Exception as e:
            QtWidgets.QMessageBox.critical(None, "列表刷新错误", f"刷新 ENB 列表时出错: {str(e)}")
            if hasattr(self, 'enb_status_label'):
                self.enb_status_label.setText("刷新列表时出错。")


    def show_resolution_settings(self):
        try:
            # 构建配置文件路径
            config_path = os.path.join(
                self.organizer.modsPath(),
                "显示修复-SSE Display Tweaks",
                "SKSE",
                "Plugins",
                "SSEDisplayTweaks.ini"
            )

            # 预处理INI文件（关键修复）
            if os.path.exists(config_path):
                # 使用utf-8-sig自动处理BOM
                with open(config_path, 'r', encoding='utf-8-sig') as file:
                    content = file.read()
                    
                    # 强制转换为标准INI格式
                    content = content.lstrip('\ufeff')  # 确保移除BOM
                    content = content.replace('\r\n', '\n').replace('\r', '\n')  # 统一换行符
                    
                    # 确保有[Render]节
                    if '[Render]' not in content:
                        content = '[Render]\n' + content
                        
                    # 临时文件路径
                    temp_path = config_path + ".tmp"
                    with open(temp_path, 'w', encoding='utf-8') as cleaned_file:
                        cleaned_file.write(content)
                        
                # 替换原文件
                shutil.move(temp_path, config_path)
            
            # 读取配置（使用预处理后的文件）
            config = configparser.ConfigParser()
            try:
                config.read(config_path, encoding='utf-8')
            except Exception as e:
                QtWidgets.QMessageBox.critical(None, "配置文件错误",
                    f"无法解析配置文件:\n{str(e)}\n"
                    "请确保SSEDisplayTweaks.ini格式正确！")
                return
                
            # 创建分辨率设置窗口
            resolution_window = QtWidgets.QDialog()
            resolution_window.setWindowTitle("分辨率设置")
            resolution_window.setWindowFlags(resolution_window.windowFlags() | Qt.WindowCloseButtonHint)
            
            # 创建主布局
            main_layout = QtWidgets.QVBoxLayout()
            main_layout.setSpacing(10)
            main_layout.setContentsMargins(15, 15, 15, 15)
            
            # 添加标题标签
            title_label = QtWidgets.QLabel("游戏分辨率设置")
            title_font = QtGui.QFont()
            title_font.setPointSize(14)
            title_font.setBold(True)
            title_label.setFont(title_font)
            title_label.setAlignment(Qt.AlignCenter)
            main_layout.addWidget(title_label)
            
            # 添加说明文本
            info_text = QtWidgets.QLabel("在这里可以设置游戏的分辨率和窗口模式，设置将在重启游戏后生效。")
            info_text.setWordWrap(True)
            info_text.setStyleSheet("color: #666; margin-bottom: 10px;")
            main_layout.addWidget(info_text)
            
            # 创建表单布局
            form_layout = QtWidgets.QFormLayout()
            form_layout.setVerticalSpacing(10)
            form_layout.setHorizontalSpacing(15)
            
            # 当前分辨率显示
            try:
                current_res = config.get("Render", "Resolution")
            except (configparser.NoOptionError, configparser.NoSectionError):
                current_res = "未设置"
                
            current_res_value = QtWidgets.QLabel(current_res)
            current_res_value.setStyleSheet("font-weight: bold; color: #4a86e8;")
            form_layout.addRow("当前分辨率:", current_res_value)
            
            # 分辨率输入框
            self.res_input = QtWidgets.QLineEdit()
            self.res_input.setPlaceholderText("例如：1920x1080")
            self.res_input.setStyleSheet("""
                QLineEdit {
                    padding: 8px;
                    border: 1px solid #ccc;
                    border-radius: 4px;
                    background-color: #f8f8f8;
                }
                QLineEdit:focus {
                    border: 1px solid #4a86e8;
                }
            """)
            form_layout.addRow("新分辨率:", self.res_input)
            
            # 窗口模式选择
            mode_group = QtWidgets.QGroupBox("窗口模式")
            mode_group.setStyleSheet("""
                QGroupBox {
                    font-weight: bold;
                    border: 1px solid #ccc;
                    border-radius: 4px;
                    margin-top: 10px;
                    padding-top: 10px;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 10px;
                    padding: 0 5px;
                }
            """)
            
            mode_layout = QtWidgets.QVBoxLayout()
            
            self.fullscreen_check = QtWidgets.QCheckBox("全屏模式")
            self.borderless_check = QtWidgets.QCheckBox("无边框窗口")
            
            checkbox_style = """
                QCheckBox {
                    spacing: 8px;
                }
                QCheckBox::indicator {
                    width: 18px;
                    height: 18px;
                }
                QCheckBox::indicator:unchecked {
                    border: 1px solid #ccc;
                    background-color: white;
                    border-radius: 3px;
                }
                QCheckBox::indicator:checked {
                    background-color: #4a86e8;
                    border: 1px solid #4a86e8;
                    border-radius: 3px;
                }
            """
            
            self.fullscreen_check.setStyleSheet(checkbox_style)
            self.borderless_check.setStyleSheet(checkbox_style)
            
            self.fullscreen_check.clicked.connect(self.update_window_mode)
            self.borderless_check.clicked.connect(self.update_window_mode)
            
            # 更新初始状态
            try:
                fullscreen = config.getboolean("Render", "Fullscreen", fallback=False)
                borderless = config.getboolean("Render", "Borderless", fallback=False)
                self.fullscreen_check.setChecked(fullscreen)
                self.borderless_check.setChecked(borderless)
            except Exception:
                pass
                
            mode_layout.addWidget(self.fullscreen_check)
            mode_layout.addWidget(self.borderless_check)
            mode_group.setLayout(mode_layout)
            
            # 自动分辨率MOD开关
            self.auto_res_check = QtWidgets.QCheckBox("启用自动分辨率调整")
            self.auto_res_check.setStyleSheet(checkbox_style)
            auto_res_state = self.organizer.modList().state("自动分辨率设置-Auto Resolution")
            auto_res_enabled = auto_res_state == mobase.ModState.ACTIVE
            self.auto_res_check.setChecked(auto_res_enabled)
            
            # 添加表单布局到主布局
            main_layout.addLayout(form_layout)
            main_layout.addWidget(mode_group)
            main_layout.addWidget(self.auto_res_check)
            
            # 添加按钮
            button_layout = QtWidgets.QHBoxLayout()
            button_layout.setSpacing(10)
            
            # 按钮样式
            button_style = """
            QPushButton {
                background-color: #4a86e8;
                color: white;
                border: none;
                padding: 10px;
                border-radius: 5px;
                font-size: 12px;
                min-height: 40px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #3a76d8;
            }
            QPushButton:pressed {
                background-color: #2a66c8;
            }
            """
            
            cancel_btn = QtWidgets.QPushButton("取消")
            cancel_btn.setStyleSheet(button_style.replace("#4a86e8", "#999").replace("#3a76d8", "#888").replace("#2a66c8", "#777"))
            cancel_btn.clicked.connect(resolution_window.reject)
            
            confirm_btn = QtWidgets.QPushButton("确认设置")
            confirm_btn.setStyleSheet(button_style)
            confirm_btn.clicked.connect(lambda: self.apply_resolution_settings(config_path) or resolution_window.accept())
            
            button_layout.addWidget(cancel_btn)
            button_layout.addWidget(confirm_btn)
            
            main_layout.addLayout(button_layout)
            
            # 设置窗口布局
            resolution_window.setLayout(main_layout)
            resolution_window.setMinimumWidth(350)
            resolution_window.exec()
        except FileNotFoundError:
            QtWidgets.QMessageBox.critical(None, "文件未找到",
                "SSEDisplayTweaks.ini文件不存在！\n"
                "请确认已安装SSE Display Tweaks模组")
            return
        except Exception as e:
            QtWidgets.QMessageBox.critical(None, "未知错误",
                f"初始化分辨率设置失败:\n{str(e)}")
            return

    def apply_resolution_settings(self, config_path):
        try:
            # 创建必要目录结构
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            
            # 读取时使用utf-8-sig
            config = configparser.ConfigParser()
            config.read(config_path, encoding='utf-8-sig')
            # 验证分辨率格式
            resolution = self.res_input.text().strip()
            if resolution:
                if not re.match(r"^\d+x\d+$", resolution):
                    QtWidgets.QMessageBox.warning(None, "格式错误", "分辨率格式不正确，请使用 宽x高 格式（例如：1920x1080）")
                    return

            # 确保文件存在并符合格式
            try:
                # 添加默认节头
                default_section = '[Render]\n'
                with open(config_path, 'r+', encoding='utf-8') as file:
                    content = file.read()
                    if not content.startswith('['):
                        content = default_section + content
                        file.seek(0)
                        file.write(content)
                        file.truncate()
            except FileNotFoundError:
                # 如果文件不存在，创建一个空文件
                with open(config_path, 'w', encoding='utf-8') as file:
                    file.write('[Render]\n')

            # 写入SSEDisplayTweaks.ini
            config = configparser.ConfigParser()
            config.read(config_path, encoding='utf-8')
            
            if not config.has_section("Render"):
                config.add_section("Render")
            
            # 获取当前配置
            current_res = config.get("Render", "Resolution", fallback="未设置")
            current_fullscreen = config.getboolean("Render", "Fullscreen", fallback=False)
            current_borderless = config.getboolean("Render", "Borderless", fallback=False)
            
            # 更新配置
            if resolution:
                config.set("Render", "Resolution", resolution)
            config.set("Render", "Fullscreen", str(self.fullscreen_check.isChecked()).lower())
            config.set("Render", "Borderless", str(self.borderless_check.isChecked()).lower())
            
            # 保存配置到原始路径
            with open(config_path, 'w', encoding='utf-8') as configfile:
                config.write(configfile)
            
            # 确定目标路径（overwrite目录）
            overwrite_path = os.path.join(self.organizer.overwritePath(), "SKSE", "Plugins", "SSEDisplayTweaks.ini")
            
            # 创建目标目录（如果不存在）
            os.makedirs(os.path.dirname(overwrite_path), exist_ok=True)
            
            try:
                # 备份目标文件
                if os.path.exists(overwrite_path):
                    shutil.copy(overwrite_path, overwrite_path + ".bak")
                
                # 复制到overwrite目录
                shutil.copy(config_path, overwrite_path)
                
                # 处理自动分辨率MOD
                try:
                    mod_list = self.organizer.modList()
                    current_state = mod_list.state("自动分辨率设置-Auto Resolution")
                    target_state = mobase.ModState.ACTIVE if self.auto_res_check.isChecked() else mobase.ModState.INACTIVE
                    
                    if current_state != target_state:
                        mod_list.setState("自动分辨率设置-Auto Resolution", target_state)
                        self.organizer.refresh()
                except Exception as e:
                    QtWidgets.QMessageBox.warning(None, "MOD状态错误", f"无法修改自动分辨率MOD状态: {str(e)}")
                
                QtWidgets.QMessageBox.information(
                    None,
                    "设置成功",
                    "分辨率设置已保存，部分修改需要重启游戏生效！"
                )
            except Exception as e:
                QtWidgets.QMessageBox.critical(None, "写入失败", f"无法保存设置: {str(e)}")
            with open(config_path, 'w', encoding='utf-8') as configfile:
                config.write(configfile)
                
        except Exception as e:
            QtWidgets.QMessageBox.critical(None, "保存失败",
                f"无法保存设置:\n{str(e)}\n"
                "请检查文件权限和防病毒软件设置！")
            return

    def update_window_mode(self):
        pass # 添加 pass 语句以修复空函数体错误
    # +++ 添加版本比较函数 (带缩进修复和健壮性改进) +++
    def _compare_versions(self, version1, version2):
        """比较两个版本号。version1 > version2 返回 1, 相等返回 0, 小于返回 -1。"""
        # 确保输入是字符串
        if not isinstance(version1, str) or not isinstance(version2, str):
            print(f"版本比较错误：输入不是字符串 ({type(version1)}, {type(version2)})")
            return 0

        try:
            # 使用正则表达式提取版本号中的数字部分
            v1_parts_str = re.findall(r'\d+', version1)
            v2_parts_str = re.findall(r'\d+', version2)

            # 转换为整数列表
            v1_parts = [int(p) for p in v1_parts_str]
            v2_parts = [int(p) for p in v2_parts_str]

            # 处理无法提取数字的情况 (例如 "Unknown" 或空字符串)
            if not v1_parts: v1_parts = [0]
            if not v2_parts: v2_parts = [0]

            # 逐部分比较
            for i in range(max(len(v1_parts), len(v2_parts))):
                v1_num = v1_parts[i] if i < len(v1_parts) else 0
                v2_num = v2_parts[i] if i < len(v2_parts) else 0

                if v1_num > v2_num:
                    return 1
                elif v1_num < v2_num:
                    return -1

            # 所有部分都相等
            return 0
        except ValueError as e:
            # 处理转换整数时的错误
            print(f"比较版本时转换数字出错 ('{version1}' vs '{version2}'): {e}")
            return 0
        except Exception as e:
            # 捕获其他潜在错误
            print(f"比较版本时发生未知错误 ('{version1}' vs '{version2}'): {e}")
            return 0
    # +++ 结束添加 +++

    # 添加一个方法来更新 ini 文件，供 network 模块调用
    def update_local_version_in_config(self, new_version: str):
        """由外部调用以更新配置文件中的版本号"""
        # 验证版本格式 (可选)
        if not re.match(r"^\d+(\.\d+)*$", new_version):
            print(f"错误: 尝试写入无效的版本号格式: {new_version}")
            return

        self._write_local_version(new_version)
        old_version = self.local_version
        self.local_version = new_version # 同时更新内存中的版本
        print(f"本地版本已从 {old_version} 更新为 {self.local_version}")

        # 如果 UI 正在显示，尝试更新 UI 上的标签
        if hasattr(self, 'version_label') and self.version_label and hasattr(self, 'window') and self.window and self.window.isVisible():
             # 更新显示的版本文本
             version_text = f"本地版本: {self.local_version}"
             if self.server_version:
                 version_text += f" / 服务器: {self.server_version}"
             # 检查是否还需要红色高亮 (更新后本地应该等于或高于服务器)
             label_style = "color: #999; font-size: 10px;" # 恢复默认样式
             # 重新比较更新后的本地版本和服务器版本
             if self.server_version and self._compare_versions(self.server_version, self.local_version) > 0:
                 label_style = "color: red; font-size: 10px; font-weight: bold;"

             # 确保在 Qt 主线程中更新 UI
             # (如果此方法可能从非主线程调用，需要使用信号槽机制)
             # 假设目前是从主线程调用的
             try:
                 self.version_label.setText(version_text)
                 self.version_label.setStyleSheet(label_style)
                 print("UI 版本标签已更新")
             except Exception as e:
                 print(f"更新 UI 版本标签时出错: {e}")
        else:
            print("UI 未显示或 version_label 不可用，跳过 UI 更新")

    # --- 以下是 show_resolution_settings 中遗留的代码，需要移回或删除 ---
    # if self.fullscreen_check.isChecked():
    #     self.borderless_check.setChecked(False)
    # if self.borderless_check.isChecked():
    #     self.fullscreen_check.setChecked(False)
    # --- 结束遗留代码 ---
def createPlugin():
    return ConsolidationController()
