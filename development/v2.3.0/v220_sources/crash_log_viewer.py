# coding=utf-8

import os
from datetime import datetime
import webbrowser
from typing import Optional

# 导入PyQt库，尝试PyQt6，如果失败则使用PyQt5
try:
    import PyQt6.QtWidgets as QtWidgets
    import PyQt6.QtGui as QtGui
    from PyQt6.QtCore import Qt
except ImportError as e:
    print(f"无法导入PyQt6，使用PyQt5作为备用: {e}")
    try:
        import PyQt5.QtWidgets as QtWidgets
        import PyQt5.QtGui as QtGui
        from PyQt5.QtCore import Qt
    except ImportError as e2:
        print(f"无法导入PyQt5: {e2}")
        raise ImportError("无法导入PyQt5或PyQt6库")

def show_crash_log_viewer(window, organizer):
    """查找并显示 NetScriptFramework 崩溃日志"""
    if not organizer:
        QtWidgets.QMessageBox.critical(
            None, "错误",
            "无法访问 Mod Organizer 实例，无法确定覆盖路径。"
        )
        return

        overwrite_path = organizer.overwritePath() if hasattr(organizer, 'overwritePath') else None
        if not overwrite_path:
            QtWidgets.QMessageBox.critical(
                None, "错误",
                "无法确定覆盖路径。请检查 Mod Organizer 设置。"
            )
            return

        crash_log_dir = os.path.join(overwrite_path, "NetScriptFramework", "Crash")
        print(f"调试日志：尝试访问崩溃日志目录: {crash_log_dir}")

        if not os.path.isdir(crash_log_dir):
            print(f"调试日志：崩溃日志目录不存在: {crash_log_dir}")
            QtWidgets.QMessageBox.warning(
                window, "未找到目录",
                f"未找到 NetScriptFramework 崩溃日志目录:\n{crash_log_dir}\n\n请确保 NetScriptFramework 已安装并生成过日志。"
            )
            return

        log_files = []
        try:
            # 获取目录下所有文件及其修改时间
            entries = [os.path.join(crash_log_dir, f) for f in os.listdir(crash_log_dir)]
            # 过滤出文件并按修改时间降序排序
            log_files = sorted(
                [f for f in entries if os.path.isfile(f) and f.lower().endswith('.txt')],
                key=os.path.getmtime, reverse=True
            )[:50]  # 限制为最近的 50 个文件以提高性能
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                window, "读取错误",
                f"读取日志目录时出错:\n{crash_log_dir}\n\n错误: {e}"
            )
            return

        if not log_files:
            QtWidgets.QMessageBox.information(
                window, "无日志文件",
                f"在以下目录中未找到 .txt 崩溃日志文件:\n{crash_log_dir}"
            )
            return

        # 创建对话框
        dialog = QtWidgets.QDialog(window)  # 父窗口设为 window
        dialog.setWindowTitle("NetScriptFramework 崩溃日志查看器")
        dialog.setMinimumWidth(600)  # 增加宽度以显示长文件名
        dialog.setMinimumHeight(400)
        # 设置为模态对话框，阻止与主窗口交互直到关闭
        dialog.setWindowModality(Qt.WindowModal)

        layout = QtWidgets.QVBoxLayout(dialog)

        # 标签
        label = QtWidgets.QLabel("以下是最近的崩溃日志文件（按修改时间排序）：")
        layout.addWidget(label)

        # 列表控件
        list_widget = QtWidgets.QListWidget()
        layout.addWidget(list_widget)

        # 存储完整路径，显示文件名和修改时间
        for log_path in log_files:
            try:
                mod_time = datetime.fromtimestamp(os.path.getmtime(log_path)).strftime('%Y-%m-%d %H:%M:%S')
                display_text = f"{os.path.basename(log_path)} ({mod_time})"
            except Exception:  # 处理获取时间可能发生的错误
                display_text = os.path.basename(log_path)

            item = QtWidgets.QListWidgetItem(display_text)
            item.setData(Qt.UserRole, log_path)  # 将完整路径存储在 UserRole 中
            item.setToolTip(log_path)  # 鼠标悬停显示完整路径
            list_widget.addItem(item)

        # 按钮
        button_box = QtWidgets.QDialogButtonBox()
        open_button = button_box.addButton("打开分析网站并复制内容", QtWidgets.QDialogButtonBox.AcceptRole)
        cancel_button = button_box.addButton(QtWidgets.QDialogButtonBox.Cancel)

        def on_open():
            selected_items = list_widget.selectedItems()
            if selected_items:
                selected_log_path = selected_items[0].data(Qt.UserRole)
                open_log_in_browser(window, selected_log_path)
                dialog.accept()  # 打开后关闭对话框
            else:
                QtWidgets.QMessageBox.warning(dialog, "未选择", "请先选择一个日志文件。")

        open_button.clicked.connect(on_open)  # 连接 accept 信号到 on_open
        cancel_button.clicked.connect(dialog.reject)  # 连接 reject 信号到 dialog.reject
        list_widget.itemDoubleClicked.connect(on_open)  # 双击也打开

        layout.addWidget(button_box)
        dialog.setLayout(layout)
        dialog.exec()  # 使用 exec() 显示模态对话框

def open_log_in_browser(window, log_path: str):
    """打开指定网页，将日志内容复制到剪贴板，并提示用户"""
    target_url = "https://phostwood.github.io/crash-analyzer/skyrim.html"
    log_content = ""

    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            log_content = f.read()
    except OSError as e_os:
        QtWidgets.QMessageBox.critical(
            window, "文件错误",
            f"打开日志文件时出错:\n{log_path}\n\n错误: {e_os}"
        )
        return

    try:
        webbrowser.open(target_url)
    except Exception as e_web:
        print(f"无法在浏览器中打开 {target_url}: {e_web}")

    try:
        # 尝试获取系统剪贴板
        clipboard = QtWidgets.QApplication.clipboard()
        if not clipboard:
            raise RuntimeError("无法获取系统剪贴板。")
        clipboard.setText(log_content)
        print(f"已将 '{os.path.basename(log_path)}' 的内容复制到剪贴板。")

        QtWidgets.QMessageBox.information(
            window, "操作提示",
            f"已尝试在浏览器中打开日志分析网站。\n\n'{os.path.basename(log_path)}' 的内容已复制到剪贴板。\n\n请在浏览器中访问 {target_url} 并粘贴内容进行分析。"
        )
    except Exception as e_clip:
        QtWidgets.QMessageBox.critical(
            window, "剪贴板错误",
            f"无法将日志内容复制到剪贴板:\n{e_clip}\n\n您可以尝试手动复制文件内容。\n文件路径: {log_path}"
        )
        QtWidgets.QMessageBox.information(
            window, "操作提示",
            f"已尝试在浏览器中打开日志分析网站。\n\n无法自动复制日志内容到剪贴板。\n\n请手动复制以下文件的内容:\n{log_path}\n\n然后在浏览器中访问 {target_url} 并粘贴内容进行分析。"
        )