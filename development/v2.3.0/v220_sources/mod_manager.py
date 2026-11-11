# -*- coding: utf-8 -*-
"""
XingLi Mod Manager Center v1.9.1
Target: Mod Organizer 2.4.4 / Python 3.8 / PyQt5.

Provides:
- A reusable installed-mod browser/selector shared by the LOD pipeline.
- Persistent nested MOD groups shared by the manager and LOD pipeline.
- Transactional enable / disable with preview, rollback journal and undo.
- Batch uninstall through IModList.removeMod(IModInterface*), with essential-mod protection.
- Batch archive installation through IOrganizer.installMod().

Important compatibility rule:
This module deliberately does not use IPluginList or map/QFlags state-change callbacks.
All MO2 API calls are made from the GUI thread.
"""

import os
import logging
from typing import Dict, List, Optional, Set

import mobase

from .mod_state_service import ModRecord, ModStateService
from .mod_groups import ModGroupStore
from .mod_transaction import ModTransactionService, StateTransactionPlan
from .stability import get_logger, log_exception

logger = get_logger("mod_manager")
try:
    from PyQt5 import QtCore, QtWidgets
    from PyQt5.QtCore import Qt
except ImportError:
    from PyQt6 import QtCore, QtWidgets
    from PyQt6.QtCore import Qt


ARCHIVE_EXTENSIONS = (".7z", ".zip", ".rar")


def _checked_value():
    return Qt.Checked


def _unchecked_value():
    return Qt.Unchecked


def _user_role():
    return Qt.UserRole


def _state_has(state, flag_name: str) -> bool:
    # Backward-compatible wrapper.  All flag semantics now live in ModStateService.
    return ModStateService.state_has(state, flag_name)


ModRow = ModRecord


def collect_mod_rows(organizer) -> List[ModRow]:
    """Read installed MODs through the central MO2 2.4.4 state service."""
    return ModStateService(organizer).records(profile_priority=True)


class ModListDialog(QtWidgets.QDialog):
    """Reusable installed-MOD selector.

    mode='select' returns checked mods to callers such as the LOD pipeline.
    mode='manage' additionally exposes enable/disable/uninstall actions.
    """

    def __init__(self, parent, organizer, initial_names=None, title="选择模组", mode="select"):
        super().__init__(parent)
        self.organizer = organizer
        self.mod_state = ModStateService(organizer)
        self.transactions = ModTransactionService(organizer, self.mod_state)
        self.mode = mode
        self.initial_names = set(str(x) for x in (initial_names or []) if str(x).strip())
        self._rows = []
        self._selected = []
        self.setWindowTitle(title)
        self.resize(880, 650)
        self._build_ui()
        self.reload()

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)

        top = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("搜索 MOD 名称或路径…")
        self.filter_combo = QtWidgets.QComboBox()
        self.filter_combo.addItem("全部", "all")
        self.filter_combo.addItem("已启用", "active")
        self.filter_combo.addItem("已禁用", "inactive")
        self.filter_combo.addItem("受保护", "essential")
        self.btn_reload = QtWidgets.QPushButton("刷新")
        top.addWidget(self.search, 1)
        top.addWidget(self.filter_combo)
        top.addWidget(self.btn_reload)
        root.addLayout(top)

        self.info = QtWidgets.QLabel()
        self.info.setWordWrap(True)
        root.addWidget(self.info)

        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["选择", "状态", "优先级", "MOD", "路径"])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        try:
            header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        except Exception:
            pass
        root.addWidget(self.table, 1)

        batch = QtWidgets.QHBoxLayout()
        self.btn_select_visible = QtWidgets.QPushButton("勾选当前筛选")
        self.btn_clear_visible = QtWidgets.QPushButton("取消当前筛选")
        batch.addWidget(self.btn_select_visible)
        batch.addWidget(self.btn_clear_visible)
        batch.addStretch(1)

        if self.mode == "manage":
            self.btn_enable = QtWidgets.QPushButton("批量启用")
            self.btn_disable = QtWidgets.QPushButton("批量禁用")
            self.btn_undo = QtWidgets.QPushButton("撤销上次启停")
            self.btn_uninstall = QtWidgets.QPushButton("批量卸载…")
            batch.addWidget(self.btn_enable)
            batch.addWidget(self.btn_disable)
            batch.addWidget(self.btn_undo)
            batch.addWidget(self.btn_uninstall)
            self.btn_enable.clicked.connect(lambda: self._set_checked_active(True))
            self.btn_disable.clicked.connect(lambda: self._set_checked_active(False))
            self.btn_undo.clicked.connect(self._undo_last_state_transaction)
            self.btn_uninstall.clicked.connect(self._uninstall_checked)
        root.addLayout(batch)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        if self.mode == "manage":
            buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("关闭")
            buttons.button(QtWidgets.QDialogButtonBox.Cancel).hide()
        else:
            buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("使用所选 MOD")
        root.addWidget(buttons)

        self.search.textChanged.connect(self.apply_filter)
        self.filter_combo.currentIndexChanged.connect(self.apply_filter)
        self.btn_reload.clicked.connect(self.reload)
        self.btn_select_visible.clicked.connect(lambda: self._check_visible(True))
        self.btn_clear_visible.clicked.connect(lambda: self._check_visible(False))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

    def reload(self):
        try:
            rows = collect_mod_rows(self.organizer)
        except Exception as exc:
            log_exception("mod_manager", "collect mods", exc)
            QtWidgets.QMessageBox.critical(self, "模组管理", "无法读取 MO2 模组列表：\n{}".format(exc))
            return
        # Preserve current checks across refresh.
        checked = set(self.checked_names()) or set(self.initial_names)
        self._rows = rows
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            check = QtWidgets.QTableWidgetItem("")
            check.setFlags((check.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            check.setCheckState(_checked_value() if row.internal_name in checked else _unchecked_value())
            check.setData(_user_role(), row.internal_name)
            self.table.setItem(i, 0, check)
            state_text = "启用" if row.active else "禁用"
            if row.essential:
                state_text += " / 受保护"
            elif not row.managed:
                state_text += " / 受保护"
            self.table.setItem(i, 1, QtWidgets.QTableWidgetItem(state_text))
            self.table.setItem(i, 2, QtWidgets.QTableWidgetItem(str(row.priority)))
            name_item = QtWidgets.QTableWidgetItem(row.display_name)
            name_item.setData(_user_role(), row.internal_name)
            self.table.setItem(i, 3, name_item)
            self.table.setItem(i, 4, QtWidgets.QTableWidgetItem(row.path))
        self.apply_filter()

    def apply_filter(self):
        needle = self.search.text().strip().casefold()
        mode = self.filter_combo.currentData()
        visible = 0
        for i, row in enumerate(self._rows):
            hay = "{}\n{}\n{}".format(row.display_name, row.internal_name, row.path).casefold()
            show = not needle or needle in hay
            if mode == "active":
                show = show and row.active
            elif mode == "inactive":
                show = show and (not row.active)
            elif mode == "essential":
                show = show and row.essential
            self.table.setRowHidden(i, not show)
            if show:
                visible += 1
        active_count = sum(1 for row in self._rows if row.active)
        inactive_count = sum(1 for row in self._rows if not row.active)
        essential_count = sum(1 for row in self._rows if row.essential)
        self.info.setText(
            "已安装 {} 个 MOD；启用 {}；禁用 {}；受保护 {}；当前显示 {} 个。".format(
                len(self._rows), active_count, inactive_count, essential_count, visible
            )
        )

    def _check_visible(self, checked: bool):
        state = _checked_value() if checked else _unchecked_value()
        for i in range(self.table.rowCount()):
            if not self.table.isRowHidden(i):
                item = self.table.item(i, 0)
                if item:
                    item.setCheckState(state)

    def checked_names(self) -> List[str]:
        names = []
        for i in range(self.table.rowCount()):
            item = self.table.item(i, 0)
            if item and item.checkState() == _checked_value():
                name = str(item.data(_user_role()) or "")
                if name:
                    names.append(name)
        return names

    def _transaction_preview_text(self, plan: StateTransactionPlan) -> str:
        enable = [name for name, active in plan.changes.items() if active]
        disable = [name for name, active in plan.changes.items() if not active]
        lines = [
            plan.label,
            "",
            "实际需要变更：{} 个；无需变化：{} 个".format(plan.change_count, plan.skipped),
            "启用：{} 个；禁用：{} 个".format(len(enable), len(disable)),
        ]
        preview = list(plan.changes.keys())[:25]
        if preview:
            lines.append("")
            lines.append("将变更：")
            for name in preview:
                lines.append("• {} → {}".format(self.mod_state.display_name(name), "启用" if plan.changes[name] else "禁用"))
        if plan.change_count > 25:
            lines.append("…另有 {} 个".format(plan.change_count - 25))
        lines.append("")
        lines.append("如果操作失败，星黎会尝试恢复到操作前的状态。")
        return "\n".join(lines)

    def apply_names_active(self, names: List[str], active: bool, label: str = ""):
        names = [str(name) for name in names if str(name).strip()]
        if not names:
            QtWidgets.QMessageBox.information(self, "模组管理", "没有可操作的 MOD。")
            return None
        action = "启用" if active else "禁用"
        if not active:
            protected = [name for name in names if self.mod_state.is_essential(name, default=False)]
            if protected:
                names = [name for name in names if name not in set(protected)]
                QtWidgets.QMessageBox.information(
                    self, "受保护模组",
                    "已自动跳过 {} 个不能禁用的受保护 MOD。".format(len(protected)),
                )
                if not names:
                    return None
        plan = self.transactions.plan_set_active(names, active, label=label or "{} {} 个 MOD".format(action, len(names)))
        if not plan.changes:
            QtWidgets.QMessageBox.information(self, "模组管理", "所选 MOD 已经全部处于目标状态。")
            return None
        if QtWidgets.QMessageBox.question(
            self, "确认更改", self._transaction_preview_text(plan),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No,
        ) != QtWidgets.QMessageBox.Yes:
            return None

        logger.info("MOD state transaction requested: %s changes=%d", plan.label, plan.change_count)
        result = self.transactions.apply(plan, auto_rollback=True)
        if not result.ok:
            detail = "执行失败：\n{}".format("\n".join(result.failures[:20]) or "未知错误")
            if result.rolled_back:
                detail += "\n\n已尝试自动回滚。"
                if result.rollback_failures:
                    detail += "\n回滚仍有问题：\n{}".format("\n".join(result.rollback_failures[:20]))
            QtWidgets.QMessageBox.critical(self, "MOD 启停失败", detail)
            return result

        changed_set = set(plan.changes.keys())
        for row_index, row in enumerate(self._rows):
            if row.internal_name not in changed_set:
                continue
            row.active = bool(plan.target_states[row.internal_name])
            state_text = "启用" if row.active else "禁用"
            if row.essential:
                state_text += " / 受保护"
            elif not row.managed:
                state_text += " / 受保护"
            item = self.table.item(row_index, 1)
            if item is not None:
                item.setText(state_text)
        self.apply_filter()
        QtCore.QTimer.singleShot(1200, lambda n=list(changed_set), a=bool(active): self._verify_changed_rows(n, a))
        QtWidgets.QMessageBox.information(
            self, "操作完成",
            "{}已完成：共变更 {} 个 MOD。\n如需恢复，可点击“撤销上次启停”。".format(plan.label, result.changed),
        )
        return result

    def _set_checked_active(self, active: bool):
        names = self.checked_names()
        if not names:
            QtWidgets.QMessageBox.information(self, "模组管理", "请先勾选至少一个 MOD。")
            return
        action = "启用" if active else "禁用"
        self.apply_names_active(names, active, label="批量{} {} 个 MOD".format(action, len(names)))

    def _undo_last_state_transaction(self):
        plan = self.transactions.plan_undo_last()
        if plan is None:
            QtWidgets.QMessageBox.information(self, "撤销", "当前没有可撤销的启停操作。")
            return
        if not plan.changes:
            self.transactions.clear_last()
            QtWidgets.QMessageBox.information(self, "撤销", "当前状态已经与上次操作前一致，无需撤销。")
            return
        if QtWidgets.QMessageBox.question(
            self, "确认撤销", self._transaction_preview_text(plan),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No,
        ) != QtWidgets.QMessageBox.Yes:
            return
        result = self.transactions.undo_last()
        if result.ok:
            QtWidgets.QMessageBox.information(self, "撤销完成", "已恢复到上次启停操作之前的状态。")
            self.reload()
        else:
            QtWidgets.QMessageBox.critical(self, "撤销失败", "\n".join(result.failures + result.rollback_failures))

    def _verify_changed_rows(self, names: List[str], active: bool):
        """Lightweight post-write verification; never rebuild the whole table."""
        if not names or not self.isVisible():
            return
        failures = []
        for name in names:
            try:
                if self.mod_state.is_active(name, default=not active) != bool(active):
                    failures.append(name)
            except Exception:
                failures.append(name)
        if failures:
            logger.warning("Delayed MOD-state verification mismatch: %s", ", ".join(failures))
            self.info.setText(
                "MO2 状态校验发现 {} 个 MOD 与目标状态不一致；请点击‘刷新’确认。".format(len(failures))
            )

    def _uninstall_checked(self):
        names = self.checked_names()
        if not names:
            QtWidgets.QMessageBox.information(self, "模组管理", "请先勾选要卸载的 MOD。")
            return
        row_map = {r.internal_name: r for r in self._rows}
        protected = [n for n in names if row_map.get(n) and (row_map[n].essential or not row_map[n].managed)]
        removable = [n for n in names if n not in protected]
        if not removable:
            QtWidgets.QMessageBox.warning(self, "模组管理", "所选 MOD 全部为受保护项目，不能卸载。")
            return
        preview = "\n".join("• " + (row_map[n].display_name if n in row_map else n) for n in removable[:25])
        if len(removable) > 25:
            preview += "\n…另有 {} 个".format(len(removable) - 25)
        text = "将从磁盘和 MO2 中永久卸载 {} 个 MOD：\n\n{}".format(len(removable), preview)
        if protected:
            text += "\n\n{} 个受保护 MOD 已自动跳过。".format(len(protected))
        text += "\n\n此操作不可由星黎自动恢复，是否继续？"
        if QtWidgets.QMessageBox.warning(
            self, "确认批量卸载", text,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        ) != QtWidgets.QMessageBox.Yes:
            return
        ml = self.organizer.modList()
        ok = 0
        failures = []
        progress = QtWidgets.QProgressDialog("正在卸载 MOD…", "停止", 0, len(removable), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        for idx, name in enumerate(removable, 1):
            progress.setValue(idx - 1)
            progress.setLabelText("正在卸载：{}".format(row_map.get(name).display_name if row_map.get(name) else name))
            QtWidgets.QApplication.processEvents()
            if progress.wasCanceled():
                break
            try:
                mod = ml.getMod(name)
                if mod and ml.removeMod(mod):
                    ok += 1
                else:
                    failures.append(name)
            except Exception as exc:
                log_exception("mod_manager", "removeMod {}".format(name), exc)
                failures.append("{} ({})".format(name, exc))
        progress.setValue(len(removable))
        try:
            self.organizer.refresh(True)
        except Exception:
            pass
        msg = "成功卸载 {} 个 MOD。".format(ok)
        if failures:
            msg += "\n失败/跳过 {} 个：\n{}".format(len(failures), "\n".join(failures[:20]))
        QtWidgets.QMessageBox.information(self, "批量卸载结果", msg)
        self.initial_names = set()
        self.reload()

    def _accept(self):
        self._selected = self.checked_names()
        self.accept()

    def selected_names(self) -> List[str]:
        return list(self._selected)


def choose_mods(parent, organizer, initial_names=None, title="选择模组") -> Optional[List[str]]:
    dlg = ModListDialog(parent, organizer, initial_names=initial_names, title=title, mode="select")
    result = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
    if result == QtWidgets.QDialog.Accepted:
        return dlg.selected_names()
    return None


class GroupSelectDialog(QtWidgets.QDialog):
    def __init__(self, parent, organizer, initial_names=None, title="选择 MOD 组"):
        super().__init__(parent)
        self.store = ModGroupStore(organizer)
        self.initial = set(str(x) for x in (initial_names or []) if str(x).strip())
        self.setWindowTitle(title)
        self.resize(520, 520)
        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel("选择要使用的 MOD 组。组中包含的其它组会自动一起应用。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.list = QtWidgets.QListWidget()
        layout.addWidget(self.list, 1)
        for name in self.store.list_names():
            item = QtWidgets.QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(_checked_value() if name in self.initial else _unchecked_value())
            definition = self.store.get(name) or {}
            item.setToolTip(str(definition.get("description", "")))
            self.list.addItem(item)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_names(self) -> List[str]:
        result = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == _checked_value():
                result.append(item.text())
        return result


def choose_mod_groups(parent, organizer, initial_names=None, title="选择 MOD 组") -> Optional[List[str]]:
    dlg = GroupSelectDialog(parent, organizer, initial_names=initial_names, title=title)
    result = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
    if result == QtWidgets.QDialog.Accepted:
        return dlg.selected_names()
    return None


class GroupEditorDialog(QtWidgets.QDialog):
    def __init__(self, parent, organizer, store: ModGroupStore, group_name: str = ""):
        super().__init__(parent)
        self.organizer = organizer
        self.store = store
        self.original_name = str(group_name or "")
        definition = store.get(self.original_name) or {}
        self.mods = list(definition.get("mods", []))
        self.setWindowTitle("编辑 MOD 组" if self.original_name else "新建 MOD 组")
        self.resize(720, 620)
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.name_edit = QtWidgets.QLineEdit(self.original_name)
        if self.original_name:
            self.name_edit.setReadOnly(True)
        self.desc_edit = QtWidgets.QPlainTextEdit(str(definition.get("description", "")))
        self.desc_edit.setMaximumHeight(90)
        form.addRow("组名称:", self.name_edit)
        form.addRow("说明:", self.desc_edit)
        layout.addLayout(form)

        child_box = QtWidgets.QGroupBox("包含的 MOD 组")
        child_layout = QtWidgets.QVBoxLayout(child_box)
        self.child_list = QtWidgets.QListWidget()
        child_layout.addWidget(self.child_list)
        selected_children = set(str(x) for x in definition.get("groups", []))
        for name in store.list_names():
            if name == self.original_name:
                continue
            item = QtWidgets.QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(_checked_value() if name in selected_children else _unchecked_value())
            self.child_list.addItem(item)
        layout.addWidget(child_box, 1)

        mods_box = QtWidgets.QGroupBox("包含的 MOD")
        mods_layout = QtWidgets.QVBoxLayout(mods_box)
        self.mods_label = QtWidgets.QLabel()
        self.mods_label.setWordWrap(True)
        mods_layout.addWidget(self.mods_label)
        row = QtWidgets.QHBoxLayout()
        btn_pick = QtWidgets.QPushButton("从模组列表选择…")
        btn_clear = QtWidgets.QPushButton("清空 MOD")
        row.addWidget(btn_pick); row.addWidget(btn_clear); row.addStretch(1)
        mods_layout.addLayout(row)
        layout.addWidget(mods_box)
        btn_pick.clicked.connect(self._pick_mods)
        btn_clear.clicked.connect(self._clear_mods)
        self._refresh_mod_label()

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh_mod_label(self):
        if not self.mods:
            self.mods_label.setText("尚未选择 MOD。")
            return
        preview = "；".join(self.mods[:12])
        if len(self.mods) > 12:
            preview += "；…另有 {} 个".format(len(self.mods) - 12)
        self.mods_label.setText("已选择 {} 个 MOD\n{}".format(len(self.mods), preview))

    def _pick_mods(self):
        selected = choose_mods(self, self.organizer, self.mods, "为 MOD 组选择成员")
        if selected is not None:
            self.mods = list(selected)
            self._refresh_mod_label()

    def _clear_mods(self):
        self.mods = []
        self._refresh_mod_label()

    def _selected_children(self) -> List[str]:
        result = []
        for i in range(self.child_list.count()):
            item = self.child_list.item(i)
            if item.checkState() == _checked_value():
                result.append(item.text())
        return result

    def _save(self):
        name = self.name_edit.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "MOD 组", "组名称不能为空。")
            return
        children = self._selected_children()
        try:
            self.store.upsert(name, self.mods, children, self.desc_edit.toPlainText())
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "MOD 组", "保存失败：\n{}".format(exc))
            return
        self.accept()


class ModManagerDialog(QtWidgets.QDialog):
    def __init__(self, parent, organizer):
        super().__init__(parent)
        self.organizer = organizer
        self.group_store = ModGroupStore(organizer)
        self.setWindowTitle("星黎模组管理中心")
        self.resize(940, 700)
        root = QtWidgets.QVBoxLayout(self)

        tabs = QtWidgets.QTabWidget()
        root.addWidget(tabs, 1)

        installed_tab = QtWidgets.QWidget()
        installed_layout = QtWidgets.QVBoxLayout(installed_tab)
        self.browser = ModListDialog(self, organizer, title="", mode="manage")
        self.browser.setWindowFlags(Qt.Widget)
        # Embed the reusable manager directly. Its local Close button is hidden
        # because the parent dialog owns the actual Close button.
        for box in self.browser.findChildren(QtWidgets.QDialogButtonBox):
            box.hide()
        installed_layout.addWidget(self.browser)
        tabs.addTab(installed_tab, "已安装 MOD")

        group_tab = QtWidgets.QWidget()
        group_layout = QtWidgets.QVBoxLayout(group_tab)
        group_note = QtWidgets.QLabel(
            "把常用 MOD 保存为组，可一键启用或禁用，也可直接用于 LOD 生成规则。"
        )
        group_note.setWordWrap(True)
        group_layout.addWidget(group_note)
        splitter = QtWidgets.QSplitter()
        self.group_list = QtWidgets.QListWidget()
        self.group_details = QtWidgets.QPlainTextEdit()
        self.group_details.setReadOnly(True)
        splitter.addWidget(self.group_list); splitter.addWidget(self.group_details)
        splitter.setStretchFactor(1, 1)
        group_layout.addWidget(splitter, 1)
        group_buttons = QtWidgets.QHBoxLayout()
        btn_group_new = QtWidgets.QPushButton("新建组…")
        btn_group_edit = QtWidgets.QPushButton("编辑组…")
        btn_group_delete = QtWidgets.QPushButton("删除组")
        btn_group_enable = QtWidgets.QPushButton("启用此组")
        btn_group_disable = QtWidgets.QPushButton("禁用此组")
        group_buttons.addWidget(btn_group_new); group_buttons.addWidget(btn_group_edit); group_buttons.addWidget(btn_group_delete)
        group_buttons.addStretch(1); group_buttons.addWidget(btn_group_enable); group_buttons.addWidget(btn_group_disable)
        group_layout.addLayout(group_buttons)
        tabs.addTab(group_tab, "MOD 组")

        btn_group_new.clicked.connect(self._new_group)
        btn_group_edit.clicked.connect(self._edit_group)
        btn_group_delete.clicked.connect(self._delete_group)
        btn_group_enable.clicked.connect(lambda: self._apply_selected_group(True))
        btn_group_disable.clicked.connect(lambda: self._apply_selected_group(False))
        self.group_list.currentItemChanged.connect(lambda *_: self._refresh_group_details())
        self._reload_groups()

        install_tab = QtWidgets.QWidget()
        install_layout = QtWidgets.QVBoxLayout(install_tab)
        note = QtWidgets.QLabel(
            "可一次加入多个 MOD 压缩包并按顺序安装。需要选择安装选项时，MO2 会正常显示安装界面。"
        )
        note.setWordWrap(True)
        install_layout.addWidget(note)
        self.archive_list = QtWidgets.QListWidget()
        self.archive_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        install_layout.addWidget(self.archive_list, 1)
        row = QtWidgets.QHBoxLayout()
        btn_files = QtWidgets.QPushButton("添加压缩包…")
        btn_folder = QtWidgets.QPushButton("扫描目录…")
        btn_remove = QtWidgets.QPushButton("从队列移除")
        btn_clear = QtWidgets.QPushButton("清空队列")
        btn_install = QtWidgets.QPushButton("开始批量安装")
        row.addWidget(btn_files); row.addWidget(btn_folder); row.addWidget(btn_remove); row.addWidget(btn_clear); row.addStretch(1); row.addWidget(btn_install)
        install_layout.addLayout(row)
        tabs.addTab(install_tab, "批量安装")

        close = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        close.clicked.connect(self.close)
        root.addWidget(close)

        btn_files.clicked.connect(self._add_files)
        btn_folder.clicked.connect(self._scan_folder)
        btn_remove.clicked.connect(self._remove_selected_archives)
        btn_clear.clicked.connect(self.archive_list.clear)
        btn_install.clicked.connect(self._install_queue)
        QtCore.QTimer.singleShot(0, self._offer_pending_transaction_recovery)

    def _offer_pending_transaction_recovery(self):
        pending = self.browser.transactions.load_pending()
        before = pending.get("before_states", {}) if isinstance(pending, dict) else {}
        if not isinstance(before, dict) or not before:
            return
        label = str(pending.get("label", "未完成的 MOD 启停操作"))
        if QtWidgets.QMessageBox.warning(
            self, "检测到未完成操作",
            "上次 MOD 启停操作没有正常结束：\n{}\n\n建议恢复到操作前的状态。是否现在恢复？".format(label),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.Yes,
        ) != QtWidgets.QMessageBox.Yes:
            return
        result = self.browser.transactions.recover_pending()
        if result.ok:
            QtWidgets.QMessageBox.information(self, "恢复完成", "已恢复到上次未完成操作之前的 MOD 状态。")
            self.browser.reload()
            self._refresh_group_details()
        else:
            QtWidgets.QMessageBox.critical(self, "恢复失败", "\n".join(result.failures + result.rollback_failures))

    def _reload_groups(self):
        current = self.group_list.currentItem().text() if hasattr(self, "group_list") and self.group_list.currentItem() else ""
        self.group_list.clear()
        for name in self.group_store.list_names():
            self.group_list.addItem(name)
        if current:
            matches = self.group_list.findItems(current, Qt.MatchExactly)
            if matches:
                self.group_list.setCurrentItem(matches[0])
        if self.group_list.count() and self.group_list.currentRow() < 0:
            self.group_list.setCurrentRow(0)
        self._refresh_group_details()

    def _selected_group_name(self) -> str:
        item = self.group_list.currentItem() if hasattr(self, "group_list") else None
        return item.text() if item else ""

    def _refresh_group_details(self):
        if not hasattr(self, "group_details"):
            return
        name = self._selected_group_name()
        if not name:
            self.group_details.setPlainText("尚未创建 MOD 组。")
            return
        definition = self.group_store.get(name) or {}
        expansion = self.group_store.expand([name])
        installed = set(self.browser.mod_state.all_names(profile_priority=False))
        missing_mods = [mod for mod in expansion.mods if mod not in installed]
        lines = [
            "组：{}".format(name),
            "说明：{}".format(definition.get("description", "") or "（无）"),
            "直接选择：{}".format(len(definition.get("mods", []))),
            "包含的组：{}".format("；".join(definition.get("groups", [])) or "无"),
            "实际包含：{}".format(len(expansion.mods)),
            "未安装 MOD：{}".format(len(missing_mods)),
        ]
        if expansion.missing_groups:
            lines.append("缺少 MOD 组：{}".format("；".join(expansion.missing_groups)))
        if expansion.cycles:
            lines.append("组之间相互包含：{}".format("；".join(expansion.cycles)))
        if missing_mods:
            lines.append("\n当前未安装：\n" + "\n".join(missing_mods[:30]))
        lines.append("\n包含的 MOD：\n" + "\n".join(expansion.mods[:80]))
        if len(expansion.mods) > 80:
            lines.append("…另有 {} 个".format(len(expansion.mods) - 80))
        self.group_details.setPlainText("\n".join(lines))

    def _new_group(self):
        dlg = GroupEditorDialog(self, self.organizer, self.group_store)
        if (dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()) == QtWidgets.QDialog.Accepted:
            self._reload_groups()

    def _edit_group(self):
        name = self._selected_group_name()
        if not name:
            QtWidgets.QMessageBox.information(self, "MOD 组", "请先选择一个 MOD 组。")
            return
        dlg = GroupEditorDialog(self, self.organizer, self.group_store, name)
        if (dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()) == QtWidgets.QDialog.Accepted:
            self._reload_groups()

    def _delete_group(self):
        name = self._selected_group_name()
        if not name:
            return
        if QtWidgets.QMessageBox.warning(
            self, "删除 MOD 组", "仅删除组定义，不会卸载或禁用任何 MOD。\n\n确定删除“{}”？".format(name),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No,
        ) != QtWidgets.QMessageBox.Yes:
            return
        self.group_store.delete(name)
        self._reload_groups()

    def _apply_selected_group(self, active: bool):
        name = self._selected_group_name()
        if not name:
            QtWidgets.QMessageBox.information(self, "MOD 组", "请先选择一个 MOD 组。")
            return
        expansion = self.group_store.expand([name])
        if expansion.cycles or expansion.missing_groups:
            QtWidgets.QMessageBox.critical(
                self, "MOD 组无法使用",
                "MOD 组设置有问题。\n缺少的组：{}\n相互包含：{}".format(
                    "；".join(expansion.missing_groups) or "无", "；".join(expansion.cycles) or "无"
                ),
            )
            return
        installed = set(self.browser.mod_state.all_names(profile_priority=False))
        missing_mods = [mod for mod in expansion.mods if mod not in installed]
        actionable = [mod for mod in expansion.mods if mod in installed]
        if missing_mods:
            if QtWidgets.QMessageBox.warning(
                self, "部分 MOD 未安装",
                "该组有 {} 个 MOD 当前未安装。可以跳过这些 MOD，继续处理其余 {} 个。\n\n是否继续？".format(len(missing_mods), len(actionable)),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No,
            ) != QtWidgets.QMessageBox.Yes:
                return
        self.browser.apply_names_active(
            actionable, active,
            label="{} MOD 组“{}”".format("启用" if active else "禁用", name),
        )
        self._refresh_group_details()

    def _queued_paths(self) -> Set[str]:
        return set(str(self.archive_list.item(i).data(_user_role()) or "") for i in range(self.archive_list.count()))

    def _append_archives(self, paths):
        existing = self._queued_paths()
        for path in paths:
            p = os.path.abspath(str(path))
            if not os.path.isfile(p) or not p.lower().endswith(ARCHIVE_EXTENSIONS) or p in existing:
                continue
            item = QtWidgets.QListWidgetItem(os.path.basename(p))
            item.setToolTip(p)
            item.setData(_user_role(), p)
            self.archive_list.addItem(item)
            existing.add(p)

    def _add_files(self):
        start = ""
        try:
            start = str(self.organizer.downloadsPath())
        except Exception:
            pass
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "选择 MOD 压缩包", start, "MOD archives (*.7z *.zip *.rar);;All files (*)")
        self._append_archives(paths)

    def _scan_folder(self):
        start = ""
        try:
            start = str(self.organizer.downloadsPath())
        except Exception:
            pass
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "选择包含 MOD 压缩包的目录", start)
        if not folder:
            return
        paths = []
        try:
            for entry in sorted(os.listdir(folder)):
                path = os.path.join(folder, entry)
                if os.path.isfile(path) and path.lower().endswith(ARCHIVE_EXTENSIONS):
                    paths.append(path)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "批量安装", "扫描目录失败：\n{}".format(exc))
            return
        self._append_archives(paths)

    def _remove_selected_archives(self):
        for item in list(self.archive_list.selectedItems()):
            self.archive_list.takeItem(self.archive_list.row(item))

    def _install_queue(self):
        paths = [str(self.archive_list.item(i).data(_user_role()) or "") for i in range(self.archive_list.count())]
        paths = [p for p in paths if p and os.path.isfile(p)]
        if not paths:
            QtWidgets.QMessageBox.information(self, "批量安装", "安装队列为空。")
            return
        if QtWidgets.QMessageBox.question(
            self, "开始批量安装",
            "将依次交给 MO2 安装 {} 个归档。需要安装选项的 MOD 会继续显示 MO2 原生安装界面。\n\n是否开始？".format(len(paths)),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        ) != QtWidgets.QMessageBox.Yes:
            return
        ok = 0
        failed = []
        progress = QtWidgets.QProgressDialog("准备安装…", "完成当前项后停止", 0, len(paths), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        for idx, path in enumerate(paths, 1):
            progress.setValue(idx - 1)
            progress.setLabelText("安装 {}/{}：{}".format(idx, len(paths), os.path.basename(path)))
            QtWidgets.QApplication.processEvents()
            if progress.wasCanceled():
                break
            try:
                result = self.organizer.installMod(path)
                if result:
                    ok += 1
                else:
                    failed.append(os.path.basename(path))
            except Exception as exc:
                logger.exception("installMod failed for %s", path)
                failed.append("{} ({})".format(os.path.basename(path), exc))
            QtWidgets.QApplication.processEvents()
        progress.setValue(len(paths))
        try:
            self.organizer.refresh(True)
        except Exception:
            pass
        msg = "批量安装队列结束：成功 {} 个。".format(ok)
        if failed:
            msg += "\n失败/取消 {} 个：\n{}".format(len(failed), "\n".join(failed[:20]))
        QtWidgets.QMessageBox.information(self, "批量安装结果", msg)
        self.browser.reload()


def show_mod_manager(parent, organizer):
    dlg = ModManagerDialog(parent, organizer)
    return dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
