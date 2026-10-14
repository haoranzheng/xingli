# -*- coding: utf-8 -*-
"""XingLi modpack manifest and health baseline and repair UI (v2.1.0).

This module deliberately uses profile files for the baseline. It does not walk the
whole mod directory and does not change MO2 state. That keeps health checks fast and
safe on large modlists.
"""

import base64
import hashlib
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

try:
    import PyQt6.QtWidgets as QtWidgets
    import PyQt6.QtGui as QtGui
    from PyQt6.QtCore import Qt
except ImportError:
    import PyQt5.QtWidgets as QtWidgets
    import PyQt5.QtGui as QtGui
    from PyQt5.QtCore import Qt

from . import stability
from .mod_state_service import ModStateService
from .modpack_repair import ModpackRepairService, RepairPlan

logger = stability.get_logger("health")
SCHEMA_VERSION = 2
MANIFEST_FILE = "xingli_modpack_manifest.json"
REPORT_FILE = "last_health_report.json"


def _exec_dialog(dialog):
    if hasattr(dialog, "exec"):
        return dialog.exec()
    return dialog.exec_()


def _accepted():
    if hasattr(QtWidgets.QDialog, "DialogCode"):
        return QtWidgets.QDialog.DialogCode.Accepted
    return QtWidgets.QDialog.Accepted


def _plugin_data_root(organizer, plugin_dir: str) -> str:
    base = ""
    try:
        base = str(organizer.pluginDataPath())
    except Exception:
        base = ""
    if base:
        path = os.path.join(base, "xingli_assistant", "modpack")
    else:
        path = os.path.join(plugin_dir, "data", "modpack")
    os.makedirs(path, exist_ok=True)
    return path


def _profile_path(organizer) -> str:
    try:
        value = organizer.profilePath()
        if value:
            return os.path.abspath(str(value))
    except Exception:
        pass
    return ""


def _profile_name(organizer, profile_path: str) -> str:
    try:
        value = organizer.profileName()
        if value:
            return str(value)
    except Exception:
        pass
    return os.path.basename(profile_path.rstrip("\\/")) if profile_path else "CurrentProfile"


def _game_name(organizer) -> str:
    try:
        game = organizer.managedGame()
        short = game.gameShortName()
        if short:
            return str(short)
    except Exception:
        pass
    return ""


def _mo2_version(organizer) -> str:
    try:
        return str(organizer.version())
    except Exception:
        return ""


def _read_text(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ""
    for encoding in ("utf-8-sig", "utf-8", "mbcs"):
        try:
            with open(path, "r", encoding=encoding, errors="strict") as handle:
                return handle.read()
        except Exception:
            continue
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except Exception:
        return ""


def _normalized_lines(text: str) -> List[str]:
    result = []
    for raw in str(text or "").splitlines():
        line = raw.strip().lstrip("\ufeff")
        if line:
            result.append(line)
    return result


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text or "").replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _parse_modlist(text: str) -> List[Dict[str, object]]:
    mods = []
    for line in _normalized_lines(text):
        if line.startswith("#"):
            continue
        enabled = None
        name = line
        if line[0:1] == "+":
            enabled = True
            name = line[1:].strip()
        elif line[0:1] == "-":
            enabled = False
            name = line[1:].strip()
        elif line[0:1] == "*":
            enabled = True
            name = line[1:].strip()
        if not name:
            continue
        mods.append({"name": name, "enabled": enabled})
    return mods


def _capture_file(profile_path: str, filename: str, include_repair_data: bool = False) -> Dict[str, object]:
    path = os.path.join(profile_path, filename)
    exists = os.path.isfile(path)
    text = _read_text(path) if exists else ""
    result = {
        "name": filename,
        "exists": bool(exists),
        "sha256": _sha256_text(text) if exists else "",
        "lines": _normalized_lines(text) if exists else [],
    }
    if include_repair_data and exists:
        try:
            with open(path, "rb") as handle:
                result["content_b64"] = base64.b64encode(handle.read()).decode("ascii")
        except Exception:
            result["content_b64"] = ""
    return result


def capture_environment(organizer, assistant_version: str, include_repair_data: bool = False) -> Dict[str, object]:
    profile_path = _profile_path(organizer)
    if not profile_path or not os.path.isdir(profile_path):
        raise RuntimeError("无法读取当前 MO2 配置目录。")
    modlist_text = _read_text(os.path.join(profile_path, "modlist.txt"))
    files = {}
    for filename in ("plugins.txt", "loadorder.txt", "Skyrim.ini", "SkyrimPrefs.ini", "SkyrimCustom.ini"):
        files[filename] = _capture_file(profile_path, filename, include_repair_data=include_repair_data)
    return {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "assistant_version": str(assistant_version or ""),
        "mo2_version": _mo2_version(organizer),
        "game": _game_name(organizer),
        "profile": _profile_name(organizer, profile_path),
        "mods": _parse_modlist(modlist_text),
        "modlist_sha256": _sha256_text(modlist_text),
        "profile_files": files,
    }


def _mod_map(mods: List[Dict[str, object]]) -> Dict[str, Optional[bool]]:
    return {str(item.get("name", "")): item.get("enabled") for item in mods if str(item.get("name", ""))}


def _mod_order(mods: List[Dict[str, object]]) -> List[str]:
    return [str(item.get("name", "")) for item in mods if str(item.get("name", ""))]


def compare_environment(baseline: Dict[str, object], current: Dict[str, object]) -> Dict[str, object]:
    base_mods = list(baseline.get("environment", {}).get("mods", [])) if isinstance(baseline.get("environment"), dict) else []
    cur_mods = list(current.get("mods", []))
    base_map = _mod_map(base_mods)
    cur_map = _mod_map(cur_mods)

    base_names = set(base_map.keys())
    cur_names = set(cur_map.keys())
    missing = sorted(base_names - cur_names, key=str.lower)
    added = sorted(cur_names - base_names, key=str.lower)
    state_changes = []
    for name in sorted(base_names & cur_names, key=str.lower):
        if base_map.get(name) != cur_map.get(name):
            state_changes.append({"name": name, "expected": base_map.get(name), "current": cur_map.get(name)})

    common_base = [x for x in _mod_order(base_mods) if x in cur_names]
    common_cur = [x for x in _mod_order(cur_mods) if x in base_names]
    order_changed = common_base != common_cur

    base_files = baseline.get("environment", {}).get("profile_files", {}) if isinstance(baseline.get("environment"), dict) else {}
    cur_files = current.get("profile_files", {})
    file_changes = []
    for filename in ("plugins.txt", "loadorder.txt", "Skyrim.ini", "SkyrimPrefs.ini", "SkyrimCustom.ini"):
        before = base_files.get(filename, {}) if isinstance(base_files, dict) else {}
        after = cur_files.get(filename, {}) if isinstance(cur_files, dict) else {}
        if bool(before.get("exists")) != bool(after.get("exists")) or str(before.get("sha256", "")) != str(after.get("sha256", "")):
            file_changes.append(filename)

    issue_count = len(missing) + len(added) + len(state_changes) + (1 if order_changed else 0) + len(file_changes)
    status = "healthy" if issue_count == 0 else "changed"
    return {
        "schema": 1,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "issue_count": issue_count,
        "missing_mods": missing,
        "added_mods": added,
        "state_changes": state_changes,
        "mod_order_changed": bool(order_changed),
        "profile_file_changes": file_changes,
        "baseline_profile": str(baseline.get("environment", {}).get("profile", "")) if isinstance(baseline.get("environment"), dict) else "",
        "current_profile": str(current.get("profile", "")),
    }


class ModpackHealthStore:
    def __init__(self, organizer, plugin_dir: str, assistant_version: str):
        self.organizer = organizer
        self.plugin_dir = plugin_dir
        self.assistant_version = assistant_version
        self.root = _plugin_data_root(organizer, plugin_dir)
        self.manifest_path = os.path.join(self.root, MANIFEST_FILE)
        self.report_path = os.path.join(self.root, REPORT_FILE)

    def load_manifest(self) -> Optional[Dict[str, object]]:
        if not os.path.isfile(self.manifest_path):
            return None
        try:
            with open(self.manifest_path, "r", encoding="utf-8-sig") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else None
        except Exception:
            logger.exception("读取整合包基准失败")
            return None

    def save_manifest(self, pack_name: str, pack_version: str) -> Dict[str, object]:
        environment = capture_environment(self.organizer, self.assistant_version, include_repair_data=True)
        # v2.1 baseline additionally snapshots numeric MO2 priorities so left-pane
        # order can be restored only when the MOD set remains identical.
        mod_state = ModStateService(self.organizer)
        for item in environment.get("mods", []) or []:
            if isinstance(item, dict):
                name = str(item.get("name", ""))
                if name:
                    item["priority"] = mod_state.priority(name, default=-1)
        data = {
            "schema": SCHEMA_VERSION,
            "product": "Xingli Modpack Manifest",
            "pack_name": str(pack_name or "星黎整合包").strip(),
            "pack_version": str(pack_version or "1.0.0").strip(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "environment": environment,
        }
        temp = self.manifest_path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(temp, self.manifest_path)
        return data

    def check(self) -> Tuple[Dict[str, object], Dict[str, object], Dict[str, object]]:
        baseline = self.load_manifest()
        if not baseline:
            raise RuntimeError("尚未建立整合包基准。")
        current = capture_environment(self.organizer, self.assistant_version)
        report = compare_environment(baseline, current)
        with open(self.report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        return baseline, current, report


class BaselineMetaDialog(QtWidgets.QDialog):
    def __init__(self, pack_name: str = "星黎整合包", pack_version: str = "1.0.0", parent=None):
        super().__init__(parent)
        self.setWindowTitle("建立整合包基准")
        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel("请确认当前 MOD 启停、顺序和插件顺序已经是你希望保留的标准状态。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QtWidgets.QFormLayout()
        self.name_edit = QtWidgets.QLineEdit(pack_name)
        self.version_edit = QtWidgets.QLineEdit(pack_version)
        form.addRow("整合包名称：", self.name_edit)
        form.addRow("整合包版本：", self.version_edit)
        layout.addLayout(form)
        box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
            if hasattr(QtWidgets.QDialogButtonBox, "StandardButton")
            else QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def values(self) -> Tuple[str, str]:
        return self.name_edit.text().strip(), self.version_edit.text().strip()


class RepairPreviewDialog(QtWidgets.QDialog):
    def __init__(self, plan: RepairPlan, parent=None):
        super().__init__(parent)
        self.setWindowTitle("确认安全修复")
        self.setMinimumSize(620, 460)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QtWidgets.QLabel("准备修复可安全回滚的项目")
        font = QtGui.QFont()
        font.setPointSize(13)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        intro = QtWidgets.QLabel(
            "星黎会先保存修复前快照，再执行修改并立即校验。任何一步失败都会尝试恢复到修复前状态。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        text = QtWidgets.QPlainTextEdit()
        text.setReadOnly(True)
        lines = ["可自动修复："]
        lines.extend("  ✓ " + item for item in plan.safe_items)
        if plan.manual_items:
            lines.extend(["", "不会自动处理："])
            lines.extend("  • " + item for item in plan.manual_items)
        text.setPlainText("\n".join(lines))
        layout.addWidget(text, 1)

        warning = QtWidgets.QLabel(
            "不会自动下载或删除 MOD，不会修改存档，也不会为了匹配基准而移除玩家新增内容。"
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #666;")
        layout.addWidget(warning)

        box = QtWidgets.QDialogButtonBox()
        accept_role = (
            QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole
            if hasattr(QtWidgets.QDialogButtonBox, "ButtonRole")
            else QtWidgets.QDialogButtonBox.AcceptRole
        )
        reject_role = (
            QtWidgets.QDialogButtonBox.ButtonRole.RejectRole
            if hasattr(QtWidgets.QDialogButtonBox, "ButtonRole")
            else QtWidgets.QDialogButtonBox.RejectRole
        )
        repair_btn = box.addButton("开始安全修复", accept_role)
        cancel_btn = box.addButton("取消", reject_role)
        repair_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(box)


class ModpackHealthDialog(QtWidgets.QDialog):
    def __init__(self, parent, organizer, plugin_dir: str, assistant_version: str):
        super().__init__(parent)
        self.organizer = organizer
        self.store = ModpackHealthStore(organizer, plugin_dir, assistant_version)
        self.repair_service = ModpackRepairService(organizer)
        self._last_plan: Optional[RepairPlan] = None
        self._last_baseline: Optional[Dict[str, object]] = None
        self._last_current: Optional[Dict[str, object]] = None
        self._last_report: Optional[Dict[str, object]] = None
        self._repair_blocked = False
        self.setWindowTitle("整合包检查")
        self.setMinimumSize(700, 540)
        self.resize(760, 600)
        self._build_ui()
        self._recover_if_needed()
        self._refresh_baseline_state()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("整合包检查与修复")
        font = QtGui.QFont()
        font.setPointSize(15)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        desc = QtWidgets.QLabel(
            "用正常状态作为基准，检查 MOD 启停、左侧顺序、插件顺序和 Profile 配置；只对可回滚项目提供自动修复。"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666;")
        layout.addWidget(desc)

        self.status_card = QtWidgets.QFrame()
        self.status_card.setObjectName("healthStatusCard")
        card_layout = QtWidgets.QVBoxLayout(self.status_card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        self.status_title = QtWidgets.QLabel("尚未建立基准")
        status_font = QtGui.QFont()
        status_font.setPointSize(12)
        status_font.setBold(True)
        self.status_title.setFont(status_font)
        self.status_detail = QtWidgets.QLabel("建立基准后即可检查当前整合包状态。")
        self.status_detail.setWordWrap(True)
        card_layout.addWidget(self.status_title)
        card_layout.addWidget(self.status_detail)
        layout.addWidget(self.status_card)

        buttons = QtWidgets.QHBoxLayout()
        self.create_btn = QtWidgets.QPushButton("建立当前基准")
        self.check_btn = QtWidgets.QPushButton("检查当前状态")
        self.repair_btn = QtWidgets.QPushButton("一键修复安全项目")
        self.update_btn = QtWidgets.QPushButton("更新基准")
        self.create_btn.clicked.connect(self.create_baseline)
        self.check_btn.clicked.connect(self.check_health)
        self.repair_btn.clicked.connect(self.repair_safe_items)
        self.update_btn.clicked.connect(self.update_baseline)
        self.repair_btn.setEnabled(False)
        buttons.addWidget(self.create_btn)
        buttons.addWidget(self.check_btn)
        buttons.addWidget(self.repair_btn)
        buttons.addWidget(self.update_btn)
        layout.addLayout(buttons)

        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details, 1)

        hint = QtWidgets.QLabel(
            "自动修复只处理可逆项目。缺失/新增 MOD、旧基准无法精确恢复的项目和复杂冲突始终交给用户确认。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #777;")
        layout.addWidget(hint)

        foot = QtWidgets.QHBoxLayout()
        open_btn = QtWidgets.QPushButton("打开基准目录")
        open_btn.clicked.connect(self.open_folder)
        close_btn = QtWidgets.QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        foot.addWidget(open_btn)
        foot.addStretch(1)
        foot.addWidget(close_btn)
        layout.addLayout(foot)

    def _set_card(self, title: str, detail: str, kind: str):
        palette = {
            "ok": ("#edf7ed", "#b7ddb8", "#1f6b2a"),
            "warn": ("#fff6e5", "#f1cf8b", "#8a5700"),
            "neutral": ("#f6f8fa", "#d9dee5", "#444"),
            "error": ("#fff0f0", "#e6b3b3", "#9b2c2c"),
        }
        bg, border, fg = palette.get(kind, palette["neutral"])
        self.status_card.setStyleSheet(
            "QFrame#healthStatusCard { background: %s; border: 1px solid %s; border-radius: 8px; padding: 4px; }" % (bg, border)
        )
        self.status_title.setText(title)
        self.status_title.setStyleSheet("color: %s;" % fg)
        self.status_detail.setText(detail)

    def _recover_if_needed(self):
        pending = self.repair_service.load_pending()
        if not pending:
            return
        buttons = (
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
            if hasattr(QtWidgets.QMessageBox, "StandardButton")
            else QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        yes = QtWidgets.QMessageBox.StandardButton.Yes if hasattr(QtWidgets.QMessageBox, "StandardButton") else QtWidgets.QMessageBox.Yes
        answer = QtWidgets.QMessageBox.question(
            self,
            "发现未完成修复",
            "上一次整合包修复没有正常结束。\n\n建议先恢复到那次修复开始前的状态，再继续检查。是否现在恢复？",
            buttons,
        )
        if answer != yes:
            self._repair_blocked = True
            self._set_card("存在未完成修复", "在恢复完成前不会执行新的自动修复。", "error")
            return
        result = self.repair_service.recover_pending()
        if result.ok:
            self._set_card("已恢复未完成修复", "已回到上一次修复开始前的状态。", "ok")
        else:
            self._repair_blocked = True
            detail = "；".join(result.rollback_failures or result.failures) or "恢复不完整"
            self._set_card("恢复未完成修复失败", detail, "error")
            QtWidgets.QMessageBox.critical(self, "恢复失败", detail)

    def _refresh_baseline_state(self):
        manifest = self.store.load_manifest()
        has = bool(manifest)
        self.check_btn.setEnabled(has)
        self.update_btn.setEnabled(has and not self._repair_blocked)
        self.create_btn.setEnabled((not has) and not self._repair_blocked)
        self.repair_btn.setEnabled(False)
        self._last_plan = None
        if self._repair_blocked:
            self._set_card("存在未完成修复", "请先恢复上一次未完成修复；在此之前不会执行新的写操作。", "error")
            self.details.setPlainText("你仍可以运行只读检查，但建立/更新基准和自动修复已锁定。重新打开此页面可再次选择恢复。")
            return
        if manifest:
            name = str(manifest.get("pack_name", "星黎整合包"))
            version = str(manifest.get("pack_version", ""))
            env = manifest.get("environment", {}) if isinstance(manifest.get("environment"), dict) else {}
            mods = env.get("mods", []) if isinstance(env, dict) else []
            schema = int(manifest.get("schema", 1) or 1)
            if schema < 2:
                self._set_card(
                    "基准已建立（旧格式）",
                    "{} {} · {} 个 MOD。可继续检查；更新一次基准后才能精确自动恢复 MOD 顺序和配置文件。".format(name, version, len(mods)),
                    "warn",
                )
            else:
                self._set_card("基准已建立", "{} {} · {} 个 MOD 记录".format(name, version, len(mods)), "neutral")
            self.details.setPlainText("点击【检查当前状态】比较当前配置与基准。")
        else:
            self._set_card("尚未建立基准", "请先确认当前整合包状态正常，再建立当前基准。", "neutral")
            self.details.setPlainText("基准会记录当前 Profile 的 MOD 启停与顺序、插件列表、加载顺序和 Profile INI。")

    def _choose_meta(self, existing=None) -> Optional[Tuple[str, str]]:
        existing = existing or {}
        dialog = BaselineMetaDialog(str(existing.get("pack_name", "星黎整合包")), str(existing.get("pack_version", "1.0.0")), self)
        if _exec_dialog(dialog) != _accepted():
            return None
        name, version = dialog.values()
        if not name or not version:
            QtWidgets.QMessageBox.warning(self, "信息不完整", "请填写整合包名称和版本。")
            return None
        return name, version

    def create_baseline(self):
        meta = self._choose_meta()
        if not meta:
            return
        try:
            data = self.store.save_manifest(meta[0], meta[1])
            env = data.get("environment", {}) if isinstance(data.get("environment"), dict) else {}
            self._set_card("基准建立完成", "已记录 {} 个 MOD，并保存可回滚顺序/配置快照。".format(len(env.get("mods", []))), "ok")
            self.details.setPlainText("以后更新 MOD、调整顺序或修改配置后，可以随时运行健康检查。")
            self._refresh_baseline_state()
        except Exception as exc:
            logger.exception("建立整合包基准失败")
            self._set_card("建立基准失败", str(exc), "error")
            QtWidgets.QMessageBox.critical(self, "建立失败", str(exc))

    def update_baseline(self):
        existing = self.store.load_manifest() or {}
        meta = self._choose_meta(existing)
        if not meta:
            return
        buttons = (
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
            if hasattr(QtWidgets.QMessageBox, "StandardButton")
            else QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        yes = QtWidgets.QMessageBox.StandardButton.Yes if hasattr(QtWidgets.QMessageBox, "StandardButton") else QtWidgets.QMessageBox.Yes
        if QtWidgets.QMessageBox.question(self, "更新基准", "确定用当前整合包状态替换原基准吗？", buttons) != yes:
            return
        try:
            self.store.save_manifest(meta[0], meta[1])
            self._set_card("基准已更新", "当前状态已成为新的检查与修复基准。", "ok")
            self.details.setPlainText("基准更新完成。")
            self._refresh_baseline_state()
        except Exception as exc:
            logger.exception("更新整合包基准失败")
            QtWidgets.QMessageBox.critical(self, "更新失败", str(exc))

    def _report_lines(self, report: Dict[str, object], plan: RepairPlan) -> List[str]:
        lines: List[str] = []
        missing = report.get("missing_mods", []) or []
        added = report.get("added_mods", []) or []
        state_changes = report.get("state_changes", []) or []
        if missing:
            lines.append("缺少基准 MOD：{} 个".format(len(missing)))
            lines.extend("  - {}".format(x) for x in missing[:20])
            if len(missing) > 20:
                lines.append("  …其余 {} 个请查看报告文件".format(len(missing) - 20))
        if added:
            lines.append("新增 MOD：{} 个".format(len(added)))
            lines.extend("  + {}".format(x) for x in added[:20])
            if len(added) > 20:
                lines.append("  …其余 {} 个请查看报告文件".format(len(added) - 20))
        if state_changes:
            lines.append("启用状态变化：{} 个".format(len(state_changes)))
            for item in state_changes[:20]:
                expected = "启用" if item.get("expected") else "禁用"
                current = "启用" if item.get("current") else "禁用"
                lines.append("  * {}：{} → {}".format(item.get("name", ""), expected, current))
            if len(state_changes) > 20:
                lines.append("  …其余 {} 个请查看报告文件".format(len(state_changes) - 20))
        if report.get("mod_order_changed"):
            lines.append("MOD 左侧顺序发生变化")
        changed_files = report.get("profile_file_changes", []) or []
        if changed_files:
            labels = {
                "plugins.txt": "插件启用列表",
                "loadorder.txt": "插件加载顺序",
                "Skyrim.ini": "Skyrim.ini",
                "SkyrimPrefs.ini": "SkyrimPrefs.ini",
                "SkyrimCustom.ini": "SkyrimCustom.ini",
            }
            lines.append("配置变化：{}".format("、".join(labels.get(x, x) for x in changed_files)))

        lines.extend(["", "────────────────", "可安全自动修复：{} 项".format(plan.safe_issue_count)])
        if plan.safe_items:
            lines.extend("  ✓ " + item for item in plan.safe_items)
        else:
            lines.append("  无")
        lines.append("需要人工处理：{} 项".format(plan.manual_issue_count))
        if plan.manual_items:
            lines.extend("  • " + item for item in plan.manual_items)
        else:
            lines.append("  无")
        return lines

    def check_health(self):
        try:
            baseline, current, report = self.store.check()
            self._last_baseline = baseline
            self._last_current = current
            self._last_report = report
            plan = self.repair_service.build_plan(baseline, current, report)
            self._last_plan = plan
            self.repair_btn.setEnabled(plan.has_safe_repairs and not self._repair_blocked)

            count = int(report.get("issue_count", 0) or 0)
            if count == 0:
                self._set_card("整合包状态正常", "当前状态与基准一致。", "ok")
                self.details.setPlainText("未发现 MOD 启停、顺序、插件列表或 Profile 配置变化。")
                return
            detail = "可安全修复 {} 项；需要人工处理 {} 项。".format(plan.safe_issue_count, plan.manual_issue_count)
            self._set_card("发现 {} 项变化".format(count), detail, "warn")
            self.details.setPlainText("\n".join(self._report_lines(report, plan)))
        except Exception as exc:
            logger.exception("整合包健康检查失败")
            self._set_card("检查失败", str(exc), "error")
            self.repair_btn.setEnabled(False)
            QtWidgets.QMessageBox.critical(self, "检查失败", str(exc))

    def repair_safe_items(self):
        plan = self._last_plan
        if plan is None or not plan.has_safe_repairs:
            QtWidgets.QMessageBox.information(self, "无需自动修复", "当前没有可安全自动修复的项目。")
            return
        if self._repair_blocked:
            QtWidgets.QMessageBox.warning(self, "修复已锁定", "请先恢复未完成的修复操作。")
            return

        preview = RepairPreviewDialog(plan, self)
        if _exec_dialog(preview) != _accepted():
            return

        self.repair_btn.setEnabled(False)
        self._set_card("正在修复", "正在保存快照、应用安全修复并校验结果…", "neutral")
        QtWidgets.QApplication.processEvents()
        try:
            result = self.repair_service.apply(plan)
        except Exception as exc:
            logger.exception("整合包安全修复异常")
            self._set_card("修复失败", str(exc), "error")
            QtWidgets.QMessageBox.critical(self, "修复失败", str(exc))
            return

        if not result.ok:
            parts = list(result.failures)
            if result.rolled_back:
                parts.append("已尝试恢复修复前状态。")
            if result.rollback_failures:
                parts.append("恢复失败：" + "；".join(result.rollback_failures))
            detail = "\n".join(parts) or "修复失败。"
            self._set_card("安全修复失败", "已执行回滚，请查看详情。", "error")
            self.details.setPlainText(detail)
            QtWidgets.QMessageBox.critical(self, "修复失败", detail)
            return

        changed = result.changed_states + result.changed_priorities + result.changed_files
        QtWidgets.QMessageBox.information(
            self,
            "安全修复完成",
            "已完成 {} 个实际修改。\n\n星黎将重新检查当前状态；缺失/新增 MOD 等人工项目不会被自动处理。".format(changed),
        )
        self.check_health()

    def open_folder(self):
        try:
            if os.name == "nt":
                os.startfile(self.store.root)  # type: ignore[attr-defined]
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "无法打开", str(exc))


def show_modpack_health(parent, organizer, plugin_dir: str, assistant_version: str):
    dialog = ModpackHealthDialog(parent, organizer, plugin_dir, assistant_version)
    return _exec_dialog(dialog)
