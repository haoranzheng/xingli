# coding: utf-8
"""Qt5/Qt6-compatible, opt-in Nexus repair preview for Xingli."""
import logging

try:
    from PyQt6 import QtCore, QtWidgets
except ImportError:
    from PyQt5 import QtCore, QtWidgets

from . import nexus_repair_service

logger = logging.getLogger("xingli.nexus_repair")


def _enum(owner, member, scope):
    # PyQt5 uses e.g. Qt.Checked; PyQt6 uses Qt.CheckState.Checked.
    direct = getattr(owner, member, None)
    return direct if direct is not None else getattr(getattr(owner, scope), member)


def _check(checked):
    return _enum(QtCore.Qt, "Checked" if checked else "Unchecked", "CheckState")


def _dialog_accepted():
    return _enum(QtWidgets.QDialog, "Accepted", "DialogCode")


def _buttonbox(button):
    return _enum(QtWidgets.QDialogButtonBox, button, "StandardButton")


def _view_enum(member, scope):
    return _enum(QtWidgets.QAbstractItemView, member, scope)


def _set_checked(table, rows, checked):
    for row in rows:
        item = table.item(row, 0)
        if item is not None:
            item.setCheckState(_check(checked))


def _selected_rows(table):
    return sorted({index.row() for index in table.selectionModel().selectedRows()})


def _preview(parent, organizer, reload_callback):
    candidates = nexus_repair_service.find_candidates(organizer)
    if not candidates:
        QtWidgets.QMessageBox.information(
            parent, "Nexus 信息修复",
            "没有发现可安全修复的 MOD。请确认 MO2 记录了原始安装压缩包名称；"
            "无法识别的文件不会猜测 ID。")
        return 0

    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Nexus 信息修复预览（确认前不会修改）")
    dialog.resize(980, 560)
    layout = QtWidgets.QVBoxLayout(dialog)
    hint = QtWidgets.QLabel(
        "支持 Ctrl/Shift 多选行，并用下方按钮批量勾选；也可以全选。"
        "默认全部未勾选，只会修改选择项的 Nexus ID 和可安全替换的网址。", dialog)
    hint.setWordWrap(True)
    layout.addWidget(hint)

    table = QtWidgets.QTableWidget(len(candidates), 4, dialog)
    table.setObjectName("nexusRepairTable")
    table.setHorizontalHeaderLabels(["选择 / MOD", "原 MOD ID", "识别 MOD ID", "Nexus 链接"])
    table.setSelectionBehavior(_view_enum("SelectRows", "SelectionBehavior"))
    table.setSelectionMode(_view_enum("ExtendedSelection", "SelectionMode"))
    table.setEditTriggers(_view_enum("NoEditTriggers", "EditTrigger"))
    table.verticalHeader().setVisible(False)
    checkable = _enum(QtCore.Qt, "ItemIsUserCheckable", "ItemFlag")
    for row, candidate in enumerate(candidates):
        item = QtWidgets.QTableWidgetItem(candidate["name"])
        item.setFlags(item.flags() | checkable)
        item.setCheckState(_check(False))
        item.setToolTip(candidate["archive"])
        table.setItem(row, 0, item)
        table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(candidate["old_id"])))
        table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(candidate["mod_id"])))
        table.setItem(row, 3, QtWidgets.QTableWidgetItem(
            "自动修正" if candidate["change_url"] else "保留原网址"))
    table.horizontalHeader().setStretchLastSection(True)
    resize_mode = _enum(QtWidgets.QHeaderView, "Stretch", "ResizeMode")
    table.horizontalHeader().setSectionResizeMode(0, resize_mode)
    layout.addWidget(table, 1)

    controls = QtWidgets.QHBoxLayout()
    all_button = QtWidgets.QPushButton("全选可修复项", dialog)
    all_button.setObjectName("nexusSelectAll")
    none_button = QtWidgets.QPushButton("取消全选", dialog)
    none_button.setObjectName("nexusSelectNone")
    selected_button = QtWidgets.QPushButton("勾选选中行", dialog)
    selected_button.setObjectName("nexusCheckSelected")
    unselect_button = QtWidgets.QPushButton("取消选中行勾选", dialog)
    unselect_button.setObjectName("nexusUncheckSelected")
    controls.addWidget(all_button)
    controls.addWidget(none_button)
    controls.addWidget(selected_button)
    controls.addWidget(unselect_button)
    layout.addLayout(controls)

    all_button.clicked.connect(lambda: _set_checked(table, range(table.rowCount()), True))
    none_button.clicked.connect(lambda: _set_checked(table, range(table.rowCount()), False))
    selected_button.clicked.connect(lambda: _set_checked(table, _selected_rows(table), True))
    unselect_button.clicked.connect(lambda: _set_checked(table, _selected_rows(table), False))

    buttons = QtWidgets.QDialogButtonBox(_buttonbox("Ok") | _buttonbox("Cancel"), dialog)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    result = dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
    if result != _dialog_accepted():
        return 0
    chosen = [dict(candidates[i]) for i in range(table.rowCount())
              if table.item(i, 0).checkState() == _check(True)]
    if not chosen:
        QtWidgets.QMessageBox.information(parent, "Nexus 信息修复", "未勾选 MOD，未做修改。")
        return 0

    completed, failed = 0, []
    for candidate in chosen:
        try:
            if nexus_repair_service.apply_by_name(
                    organizer, candidate["name"], candidate):
                completed += 1
            else:
                failed.append(candidate["name"] + "：安装元数据已变化，跳过")
        except Exception as exc:
            logger.exception("Nexus metadata write failed for %s", candidate["name"])
            failed.append("{}：{}".format(candidate["name"], exc))
    if completed and callable(reload_callback):
        try:
            reload_callback()
        except Exception:
            logger.exception("MOD list refresh failed after metadata fix")
    info = "已修复 {} 项，未处理/失败 {} 项。".format(completed, len(failed))
    if failed:
        info += "\n" + "\n".join(failed[:12])
    QtWidgets.QMessageBox.information(parent, "Nexus 信息修复", info)
    return completed


def show_preview(parent, organizer, reload_callback=None):
    """Never allow a Python exception to escape a Qt slot into MO2's process."""
    try:
        return _preview(parent, organizer, reload_callback)
    except Exception as exc:
        logger.exception("Nexus repair preview failed")
        try:
            QtWidgets.QMessageBox.warning(
                parent, "Nexus 信息修复",
                "预览发生异常，未继续执行修复：\n{}".format(exc))
        except Exception:
            logger.exception("Unable to display repair error")
        return 0
