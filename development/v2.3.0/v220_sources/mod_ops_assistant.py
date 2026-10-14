# coding=utf-8
"""
星黎 MO2 小助手 - 整合维护增强模块

目标：
1. 让整合作者在 MO2 内看到已安装 Mod 的更新/元数据/1.5.97 风险状态。
2. 批量扫描 Downloads 或任意文件夹里的压缩包，并交给 MO2 自带安装器连续安装。
3. 自动把新安装 Mod 移动到推荐分隔符附近，减少左侧列表维护成本。
4. 生成分享给新手用的启动脚本、当前 Profile 快照、整合清单和 README 骨架。

注意：
- 本模块不绕过 MO2 的 FOMOD/安装器确认流程。复杂 FOMOD 仍应由用户选择选项。
- Nexus 更新检查依赖 MO2 已知的 newestVersion 元数据；若元数据缺失，本模块会标记为“缺元数据”。
- 对 Skyrim SE 1.5.97，SKSE DLL 类 Mod 默认归为高风险更新项，只给出审查提示，不会自动更新。
"""

import csv
import json
import os
import re
import shutil
import time
import webbrowser
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

try:
    import PyQt6.QtWidgets as QtWidgets
    import PyQt6.QtGui as QtGui
    from PyQt6.QtCore import Qt
except ImportError:
    import PyQt5.QtWidgets as QtWidgets
    import PyQt5.QtGui as QtGui
    from PyQt5.QtCore import Qt

ARCHIVE_EXTENSIONS = (".zip", ".7z", ".rar")
PLUGIN_EXTENSIONS = (".esp", ".esm", ".esl")

SENSITIVE_NAME_RE = re.compile(
    r"(skse|address\s*library|engine\s*fix|display\s*tweaks?|sse\s*fix|papyrusutil|jcontainers|racemenu|"
    r"nemesis|pandora|fnis|dar|oar|animation|combat|poise|truehud|mcm|dll|framework)",
    re.I,
)

CATEGORY_RULES: List[Tuple[str, re.Pattern, List[str]]] = [
    ("00_LOCKED_1.5.97_DO_NOT_UPDATE", re.compile(r"(skse|address|engine|display|sse\s*fix|papyrus|jcontainers|racemenu|dll)", re.I), ["1.5.97", "LOCK", "SKSE", "CORE", "核心"]),
    ("01_Core_SKSE_Framework", re.compile(r"(skse|framework|base|requirement|library|util)", re.I), ["CORE", "SKSE", "Framework", "核心"]),
    ("02_UI_MCM", re.compile(r"(skyui|mcm|hud|ui|menu|interface|font|truehud)", re.I), ["UI", "MCM", "界面"]),
    ("03_Bug_Fixes", re.compile(r"(fix|patch|bug|修复)", re.I), ["Fix", "Bug", "修复"]),
    ("04_Combat_Animation", re.compile(r"(combat|animation|nemesis|pandora|fnis|dar|oar|behavior|moveset|attack)", re.I), ["Combat", "Animation", "动画", "战斗"]),
    ("05_NPC_Character", re.compile(r"(npc|body|skin|hair|face|racemenu|cbbe|bhunp|xpms|character)", re.I), ["NPC", "Character", "角色", "身体"]),
    ("06_Armor_Weapon", re.compile(r"(armor|weapon|clothing|outfit|sword|服装|武器|盔甲)", re.I), ["Armor", "Weapon", "装备", "武器"]),
    ("07_World_Quest", re.compile(r"(quest|world|location|city|town|dungeon|landscape|任务|城市|世界)", re.I), ["World", "Quest", "世界", "任务"]),
    ("08_Texture_Mesh", re.compile(r"(texture|mesh|model|replacer|parallax|landscape|材质|模型)", re.I), ["Texture", "Mesh", "材质", "模型"]),
    ("09_Compatibility_Patches", re.compile(r"(patch|compat|consistency|compatibility|兼容|补丁)", re.I), ["Patch", "Compat", "补丁", "兼容"]),
    ("90_Generated_Output", re.compile(r"(synthesis|dyndolod|texgen|bodyslide|nemesis|pandora|bashed|smashed|output|generated)", re.I), ["Generated", "Output", "生成", "输出"]),
]

DEFAULT_SEPARATOR_SUGGESTIONS = [
    "00_LOCKED_1.5.97_DO_NOT_UPDATE",
    "01_Core_SKSE_Framework",
    "02_UI_MCM",
    "03_Bug_Fixes",
    "04_Combat_Animation",
    "05_NPC_Character",
    "06_Armor_Weapon",
    "07_World_Quest",
    "08_Texture_Mesh",
    "09_Compatibility_Patches",
    "90_Generated_Output",
]


@dataclass
class ModRecord:
    status: str
    priority: int
    name: str
    display_name: str
    active: str
    version: str
    newest_version: str
    nexus_id: int
    repository: str
    url: str
    category: str
    content: str
    suggestion: str
    install_file: str
    folder: str


def _standard_button(name: str):
    button_enum = getattr(QtWidgets.QMessageBox, "StandardButton", QtWidgets.QMessageBox)
    return getattr(button_enum, name)


def _exec_dialog(dialog):
    if hasattr(dialog, "exec"):
        return dialog.exec()
    return dialog.exec_()


def _qt_checked():
    state_enum = getattr(Qt, "CheckState", Qt)
    return getattr(state_enum, "Checked")


def _qt_unchecked():
    state_enum = getattr(Qt, "CheckState", Qt)
    return getattr(state_enum, "Unchecked")


def _qt_user_checkable():
    flag_enum = getattr(Qt, "ItemFlag", Qt)
    return getattr(flag_enum, "ItemIsUserCheckable")


def _qt_item_enabled():
    flag_enum = getattr(Qt, "ItemFlag", Qt)
    return getattr(flag_enum, "ItemIsEnabled")


def _safe_str(value) -> str:
    try:
        text = str(value)
    except Exception:
        return ""
    return text.strip()


def _version_key(version: str) -> Tuple[int, ...]:
    if not version:
        return tuple()
    parts = re.findall(r"\d+", version)
    return tuple(int(p) for p in parts[:6])


def _is_newer(newest: str, current: str) -> bool:
    nk = _version_key(newest)
    ck = _version_key(current)
    if not nk or not ck:
        return False
    return nk > ck


def _call_bool(obj, method: str, default: bool = False) -> bool:
    try:
        attr = getattr(obj, method)
        return bool(attr())
    except Exception:
        return default


def _call_str(obj, method: str, default: str = "") -> str:
    try:
        attr = getattr(obj, method)
        return _safe_str(attr())
    except Exception:
        return default


def _call_int(obj, method: str, default: int = -1) -> int:
    try:
        attr = getattr(obj, method)
        return int(attr())
    except Exception:
        return default


def _display_qfileinfo_path(value) -> str:
    try:
        if hasattr(value, "absoluteFilePath"):
            return _safe_str(value.absoluteFilePath())
        if hasattr(value, "absolutePath"):
            return _safe_str(value.absolutePath())
    except Exception:
        pass
    return _safe_str(value)


def _mod_state_to_active_text(state) -> str:
    return "启用" if ModStateService.state_has(state, "ACTIVE") else "禁用"


def _infer_category_from_name(name: str) -> str:
    for category, pattern, _keywords in CATEGORY_RULES:
        if pattern.search(name or ""):
            return category
    return "未分类"


def _clean_archive_name(filename: str) -> str:
    base = os.path.basename(filename)
    for ext in ARCHIVE_EXTENSIONS:
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
            break
    # 去掉 Nexus 常见后缀: -12345-1-2-3-1700000000
    base = re.sub(r"-\d{3,}(?:[-_][\dv]+){1,}$", "", base)
    base = re.sub(r"[_]+", " ", base)
    base = re.sub(r"\s+", " ", base).strip(" -_")
    return base or os.path.basename(filename)


def _collect_mod_content(mod_dir: str) -> Tuple[str, bool]:
    if not mod_dir or not os.path.isdir(mod_dir):
        return "", False

    flags = []
    plugin_count = 0
    dll_count = 0
    script_count = 0
    bsa_count = 0
    has_meshes = os.path.isdir(os.path.join(mod_dir, "meshes")) or os.path.isdir(os.path.join(mod_dir, "Meshes"))
    has_textures = os.path.isdir(os.path.join(mod_dir, "textures")) or os.path.isdir(os.path.join(mod_dir, "Textures"))
    has_interface = os.path.isdir(os.path.join(mod_dir, "interface")) or os.path.isdir(os.path.join(mod_dir, "Interface"))

    for root, dirs, files in os.walk(mod_dir):
        # 避免非常深的目录造成卡顿；内容识别只需要粗粒度。
        depth = os.path.relpath(root, mod_dir).count(os.sep)
        if depth > 5:
            dirs[:] = []
            continue
        for file_name in files:
            lower = file_name.lower()
            if lower.endswith(PLUGIN_EXTENSIONS):
                plugin_count += 1
            elif lower.endswith(".dll"):
                dll_count += 1
            elif lower.endswith((".pex", ".psc")):
                script_count += 1
            elif lower.endswith(".bsa"):
                bsa_count += 1

    if plugin_count:
        flags.append(f"插件x{plugin_count}")
    if dll_count:
        flags.append(f"DLLx{dll_count}")
    if script_count:
        flags.append(f"脚本x{script_count}")
    if bsa_count:
        flags.append(f"BSAx{bsa_count}")
    if has_meshes:
        flags.append("Mesh")
    if has_textures:
        flags.append("Texture")
    if has_interface:
        flags.append("Interface")

    return ", ".join(flags), dll_count > 0


def _parse_meta_file(meta_path: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not os.path.exists(meta_path):
        return result
    try:
        with open(meta_path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("[") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                result[key.strip()] = value.strip()
    except Exception:
        return result
    return result


class ModOpsAssistantDialog(QtWidgets.QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.organizer = controller.organizer
        self.mod_state = ModStateService(self.organizer)
        self.plugin_path = getattr(controller, "plugin_path", os.path.dirname(__file__))
        self.records: List[ModRecord] = []
        self.archive_rows: List[Dict[str, str]] = []
        self.report_dir = os.path.join(self.plugin_path, "reports")
        os.makedirs(self.report_dir, exist_ok=True)
        self.setWindowTitle("星黎整合维护中心 - 更新 / 批量安装 / 发布")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.resize(1100, 760)
        self._build_ui()

    # ---------- UI ----------
    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("星黎整合维护中心")
        font = QtGui.QFont()
        font.setPointSize(14)
        font.setBold(True)
        title.setFont(font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter if hasattr(Qt, "AlignmentFlag") else Qt.AlignCenter)
        layout.addWidget(title)

        info = QtWidgets.QLabel(
            "用于检查整合包 MOD、批量安装下载包，并生成启动和发布文件。"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs)
        self._build_audit_tab()
        self._build_install_tab()
        self._build_sort_tab()
        self._build_release_tab()

        close_button = QtWidgets.QPushButton("关闭")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

    def _button(self, text: str, callback, tooltip: str = ""):
        btn = QtWidgets.QPushButton(text)
        if tooltip:
            btn.setToolTip(tooltip)
        btn.clicked.connect(callback)
        return btn

    def _build_audit_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(self._button("扫描已安装 Mod", self.scan_mods, "读取当前 Profile 的左侧列表、版本、Nexus 元数据和内容摘要"))
        controls.addWidget(self._button("保存快照", self.save_snapshot, "保存当前扫描结果，用于之后对比"))
        controls.addWidget(self._button("对比上次快照", self.compare_last_snapshot, "显示本次扫描和上次快照之间的变化"))
        controls.addWidget(self._button("导出 CSV / JSON", self.export_reports, "导出当前扫描表"))
        controls.addWidget(self._button("打开选中 Mod 页面", self.open_selected_mod_page, "打开 Nexus 页面或 Mod URL"))
        layout.addLayout(controls)

        filter_layout = QtWidgets.QHBoxLayout()
        filter_layout.addWidget(QtWidgets.QLabel("过滤状态:"))
        self.audit_filter = QtWidgets.QComboBox()
        self.audit_filter.addItems(["全部", "可更新", "缺元数据", "1.5.97锁定检查", "正常", "分隔符"])
        self.audit_filter.currentIndexChanged.connect(self.refresh_audit_table)
        filter_layout.addWidget(self.audit_filter)
        self.audit_summary = QtWidgets.QLabel("尚未扫描。")
        self.audit_summary.setWordWrap(True)
        filter_layout.addWidget(self.audit_summary, 1)
        layout.addLayout(filter_layout)

        self.audit_table = QtWidgets.QTableWidget(0, 10)
        self.audit_table.setHorizontalHeaderLabels(["状态", "优先级", "名称", "当前版本", "最新版本", "NexusID", "来源", "分类", "内容", "建议"])
        self.audit_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows if hasattr(QtWidgets.QAbstractItemView, "SelectionBehavior") else QtWidgets.QAbstractItemView.SelectRows)
        self.audit_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers if hasattr(QtWidgets.QAbstractItemView, "EditTrigger") else QtWidgets.QAbstractItemView.NoEditTriggers)
        header = self.audit_table.horizontalHeader()
        try:
            header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(9, QtWidgets.QHeaderView.ResizeMode.Stretch)
        except Exception:
            header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
            header.setSectionResizeMode(9, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.audit_table)
        self.tabs.addTab(tab, "更新雷达")

    def _build_install_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)

        path_layout = QtWidgets.QHBoxLayout()
        path_layout.addWidget(QtWidgets.QLabel("压缩包目录:"))
        self.archive_path_edit = QtWidgets.QLineEdit(self._default_downloads_path())
        path_layout.addWidget(self.archive_path_edit, 1)
        path_layout.addWidget(self._button("选择目录", self.choose_archive_dir))
        path_layout.addWidget(self._button("扫描压缩包", self.scan_archives))
        layout.addLayout(path_layout)

        option_layout = QtWidgets.QHBoxLayout()
        self.install_enable_checkbox = QtWidgets.QCheckBox("安装后启用 Mod")
        self.install_enable_checkbox.setChecked(True)
        self.install_move_checkbox = QtWidgets.QCheckBox("安装后按分类移动到对应分隔符附近")
        self.install_move_checkbox.setChecked(True)
        self.install_refresh_checkbox = QtWidgets.QCheckBox("安装结束后刷新 MO2")
        self.install_refresh_checkbox.setChecked(True)
        option_layout.addWidget(self.install_enable_checkbox)
        option_layout.addWidget(self.install_move_checkbox)
        option_layout.addWidget(self.install_refresh_checkbox)
        option_layout.addStretch(1)
        layout.addLayout(option_layout)

        self.archive_table = QtWidgets.QTableWidget(0, 6)
        self.archive_table.setHorizontalHeaderLabels(["选择", "文件名", "建议 Mod 名", "推断分类", "大小", "元数据"])
        self.archive_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows if hasattr(QtWidgets.QAbstractItemView, "SelectionBehavior") else QtWidgets.QAbstractItemView.SelectRows)
        self.archive_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked if hasattr(QtWidgets.QAbstractItemView, "EditTrigger") else QtWidgets.QAbstractItemView.DoubleClicked)
        header = self.archive_table.horizontalHeader()
        try:
            header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        except Exception:
            header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
            header.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.archive_table)

        action_layout = QtWidgets.QHBoxLayout()
        action_layout.addWidget(self._button("全选", self.select_all_archives))
        action_layout.addWidget(self._button("全不选", self.unselect_all_archives))
        action_layout.addStretch(1)
        action_layout.addWidget(self._button("安装选中压缩包", self.install_selected_archives, "逐个调用 MO2 installMod；FOMOD 仍会正常弹窗"))
        layout.addLayout(action_layout)

        note = QtWidgets.QLabel("需要选择安装选项的 MOD 仍会正常弹出安装界面。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tabs.addTab(tab, "批量安装队列")

    def _build_sort_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(self._button("检查左侧分隔符", self.check_separators))
        controls.addWidget(self._button("生成左侧整理建议", self.generate_sort_plan))
        controls.addWidget(self._button("导出整理规则", self.export_sort_rules))
        layout.addLayout(controls)
        self.sort_text = QtWidgets.QPlainTextEdit()
        self.sort_text.setReadOnly(True)
        self.sort_text.setPlainText("点击“检查左侧分隔符”或“生成左侧整理建议”。")
        layout.addWidget(self.sort_text)
        self.tabs.addTab(tab, "排序辅助")

    def _build_release_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("发布目录:"))
        self.release_dir_edit = QtWidgets.QLineEdit(os.path.join(self._base_path(), "_XingliRelease"))
        top.addWidget(self.release_dir_edit, 1)
        top.addWidget(self._button("选择目录", self.choose_release_dir))
        layout.addLayout(top)

        self.launcher_exe_name = QtWidgets.QLineEdit("SKSE")
        self.launcher_game_name = QtWidgets.QLineEdit("Skyrim Special Edition")
        form = QtWidgets.QFormLayout()
        form.addRow("MO2 快捷方式游戏名:", self.launcher_game_name)
        form.addRow("MO2 可执行项名:", self.launcher_exe_name)
        layout.addLayout(form)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(self._button("直接从插件启动 SKSE", self.launch_skse_from_mo, "使用 MO2 VFS 启动 skse64_loader.exe"))
        controls.addWidget(self._button("生成星黎整合启动器发布包", self.generate_release_pack, "生成 星黎整合启动器.bat、Play.bat、README、Profile 快照、清单"))
        controls.addWidget(self._button("打开发布目录", self.open_release_dir))
        layout.addLayout(controls)

        self.release_text = QtWidgets.QPlainTextEdit()
        self.release_text.setReadOnly(True)
        self.release_text.setPlainText(
            "生成内容包括：\n"
            "- 星黎整合启动器.bat：新手首选双击入口，品牌化窗口标题与启动提示。\n"
            "- Play.bat：兼容旧说明/旧习惯的别名入口，调用同一套路径安全逻辑。\n"
            "- XingliLaunch.ps1：真正负责定位本机 MO2、SKSE 并修正 MO2 可执行项路径的启动脚本。\n"
            "- README_新手必读.txt：给新手的最短启动说明。\n"
            "- manifest.csv/json：当前整合 Mod 清单。\n"
            "- profile_snapshot：当前 Profile 的 modlist/plugins/loadorder 文件快照。\n"
            "\n注意：这不是 Wabbajack 编译器，不会重新分发第三方 Mod 文件。"
        )
        layout.addWidget(self.release_text)
        self.tabs.addTab(tab, "新手启动/发布")

    # ---------- Common path helpers ----------
    def _base_path(self) -> str:
        try:
            return self.organizer.basePath()
        except Exception:
            return os.path.abspath(os.path.join(self.plugin_path, "..", ".."))

    def _default_downloads_path(self) -> str:
        try:
            path = self.organizer.downloadsPath()
            if path:
                return path
        except Exception:
            pass
        return os.path.join(self._base_path(), "downloads")

    def _mods_path(self) -> str:
        try:
            return self.organizer.modsPath()
        except Exception:
            return os.path.join(self._base_path(), "mods")

    def _profile_path(self) -> str:
        try:
            return self.organizer.profilePath()
        except Exception:
            return os.path.join(self._base_path(), "profiles")

    def _profile_name(self) -> str:
        try:
            return self.organizer.profileName() or "CurrentProfile"
        except Exception:
            return "CurrentProfile"

    # ---------- Audit ----------
    def scan_mods(self):
        try:
            self.records = self._scan_mod_records()
            self.refresh_audit_table()
            self.audit_summary.setText(self._make_summary_text())
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "扫描失败", f"扫描 Mod 列表失败：\n{exc}")

    def _scan_mod_records(self) -> List[ModRecord]:
        mod_list = self.organizer.modList()
        try:
            profile = self.organizer.profile()
            names = list(mod_list.allModsByProfilePriority(profile))
        except Exception:
            names = list(mod_list.allMods())

        records: List[ModRecord] = []
        mods_path = self._mods_path()
        for name in names:
            mod = mod_list.getMod(name)
            if mod is None:
                continue
            display_name = _safe_str(mod_list.displayName(name)) or _call_str(mod, "name", name) or name
            priority = -1
            try:
                priority = int(mod_list.priority(name))
            except Exception:
                pass
            is_separator = _call_bool(mod, "isSeparator")
            is_overwrite = _call_bool(mod, "isOverwrite")
            is_foreign = _call_bool(mod, "isForeign")
            folder = os.path.join(mods_path, name)

            version = _call_str(mod, "version")
            newest = _call_str(mod, "newestVersion")
            nexus_id = _call_int(mod, "nexusId", -1)
            repo = _call_str(mod, "repository")
            url = _call_str(mod, "url")
            install_file = _call_str(mod, "installationFile")
            active_text = "启用" if self.mod_state.is_active(name, default=False) else "禁用"

            content, has_dll = _collect_mod_content(folder)
            category = _infer_category_from_name(display_name + " " + content)
            suggestion = ""
            status = "正常"

            if is_separator:
                status = "分隔符"
                suggestion = "用于左侧排序分组。"
            elif is_overwrite:
                status = "Overwrite"
                suggestion = "建议把生成文件移动到专用 Generated Output Mod。"
            elif is_foreign:
                status = "外部/非MO管理"
                suggestion = "来自游戏 Data 或外部来源，分享整合时需额外说明。"
            elif _is_newer(newest, version):
                status = "可更新"
                suggestion = "更新前确认是否支持 SkyrimSE 1.5.97；DLL/FOMOD 类先在测试 Profile 更新。"
            elif nexus_id <= 0 and not url:
                status = "缺元数据"
                suggestion = "建议在 MO2 Downloads 右键 Query Info，或重新用 Mod Manager Download 获取。"
            elif has_dll or SENSITIVE_NAME_RE.search(display_name):
                status = "1.5.97锁定检查"
                suggestion = "版本敏感项，不建议无脑更新；确认支持 runtime 1.5.97 后再更新。"

            records.append(
                ModRecord(
                    status=status,
                    priority=priority,
                    name=name,
                    display_name=display_name,
                    active=active_text,
                    version=version,
                    newest_version=newest,
                    nexus_id=nexus_id,
                    repository=repo,
                    url=url,
                    category=category,
                    content=content,
                    suggestion=suggestion,
                    install_file=install_file,
                    folder=folder,
                )
            )
        return records

    def _make_summary_text(self) -> str:
        total = len(self.records)
        counts: Dict[str, int] = {}
        for record in self.records:
            counts[record.status] = counts.get(record.status, 0) + 1
        pieces = [f"总数 {total}"]
        for key in ["可更新", "缺元数据", "1.5.97锁定检查", "Overwrite", "外部/非MO管理"]:
            if counts.get(key):
                pieces.append(f"{key} {counts[key]}")
        return "；".join(pieces)

    def refresh_audit_table(self):
        current_filter = self.audit_filter.currentText() if hasattr(self, "audit_filter") else "全部"
        rows = [r for r in self.records if current_filter == "全部" or r.status == current_filter]
        self.audit_table.setRowCount(0)
        for record in rows:
            row = self.audit_table.rowCount()
            self.audit_table.insertRow(row)
            values = [
                record.status,
                str(record.priority),
                record.display_name,
                record.version,
                record.newest_version,
                str(record.nexus_id if record.nexus_id > 0 else ""),
                record.repository or record.url,
                record.category,
                record.content,
                record.suggestion,
            ]
            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setData(32, record.name)  # Qt.UserRole without enum dependency
                if record.status == "可更新":
                    item.setBackground(QtGui.QColor(255, 245, 205))
                elif record.status == "缺元数据":
                    item.setBackground(QtGui.QColor(255, 225, 225))
                elif record.status == "1.5.97锁定检查":
                    item.setBackground(QtGui.QColor(225, 235, 255))
                self.audit_table.setItem(row, col, item)

    def _selected_record(self) -> Optional[ModRecord]:
        indexes = self.audit_table.selectionModel().selectedRows() if self.audit_table.selectionModel() else []
        if not indexes:
            return None
        row = indexes[0].row()
        item = self.audit_table.item(row, 0)
        if not item:
            return None
        internal_name = item.data(32)
        for record in self.records:
            if record.name == internal_name:
                return record
        return None

    def open_selected_mod_page(self):
        record = self._selected_record()
        if not record:
            QtWidgets.QMessageBox.information(self, "未选择", "请先在表格中选择一个 Mod。")
            return
        url = record.url
        if not url and record.nexus_id > 0:
            url = f"https://www.nexusmods.com/skyrimspecialedition/mods/{record.nexus_id}?tab=logs"
        if not url:
            QtWidgets.QMessageBox.information(self, "没有链接", "该 Mod 没有 Nexus ID 或 URL。")
            return
        webbrowser.open(url)

    def export_reports(self):
        if not self.records:
            self.scan_mods()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = os.path.join(self.report_dir, f"mod_audit_{timestamp}.csv")
        json_path = os.path.join(self.report_dir, f"mod_audit_{timestamp}.json")
        self._write_records(csv_path, json_path)
        QtWidgets.QMessageBox.information(self, "导出完成", f"已导出：\n{csv_path}\n{json_path}")
        self._open_folder(self.report_dir)

    def _write_records(self, csv_path: str, json_path: str):
        fields = list(ModRecord.__dataclass_fields__.keys())
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for record in self.records:
                writer.writerow(asdict(record))
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump([asdict(r) for r in self.records], handle, ensure_ascii=False, indent=2)

    def _snapshot_path(self) -> str:
        return os.path.join(self.report_dir, "last_snapshot.json")

    def save_snapshot(self):
        if not self.records:
            self.scan_mods()
        snapshot = {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "profile": self._profile_name(),
            "records": [asdict(r) for r in self.records],
        }
        with open(self._snapshot_path(), "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2)
        QtWidgets.QMessageBox.information(self, "快照已保存", f"已保存当前扫描快照：\n{self._snapshot_path()}")

    def compare_last_snapshot(self):
        if not os.path.exists(self._snapshot_path()):
            QtWidgets.QMessageBox.information(self, "没有快照", "还没有上次快照，请先点击“保存快照”。")
            return
        if not self.records:
            self.scan_mods()
        with open(self._snapshot_path(), "r", encoding="utf-8") as handle:
            old = json.load(handle)
        old_map = {r.get("name"): r for r in old.get("records", [])}
        new_map = {r.name: asdict(r) for r in self.records}
        added = sorted(set(new_map) - set(old_map))
        removed = sorted(set(old_map) - set(new_map))
        changed = []
        for name in sorted(set(old_map) & set(new_map)):
            old_r = old_map[name]
            new_r = new_map[name]
            diffs = []
            for key in ["version", "newest_version", "status", "priority", "active", "content"]:
                if _safe_str(old_r.get(key)) != _safe_str(new_r.get(key)):
                    diffs.append(f"{key}: {old_r.get(key)} -> {new_r.get(key)}")
            if diffs:
                changed.append((name, diffs))

        lines = [f"上次快照：{old.get('saved_at', '未知')} / Profile: {old.get('profile', '未知')}", ""]
        lines.append(f"新增 Mod：{len(added)}")
        lines.extend(["  + " + n for n in added[:100]])
        lines.append("")
        lines.append(f"移除 Mod：{len(removed)}")
        lines.extend(["  - " + n for n in removed[:100]])
        lines.append("")
        lines.append(f"发生变化：{len(changed)}")
        for name, diffs in changed[:200]:
            lines.append("  * " + name)
            for diff in diffs:
                lines.append("      " + diff)
        self._show_text_dialog("快照对比", "\n".join(lines))

    # ---------- Archive install queue ----------
    def choose_archive_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择压缩包目录", self.archive_path_edit.text() or self._default_downloads_path())
        if path:
            self.archive_path_edit.setText(path)

    def scan_archives(self):
        folder = self.archive_path_edit.text().strip()
        if not os.path.isdir(folder):
            QtWidgets.QMessageBox.warning(self, "目录无效", "请选择有效的压缩包目录。")
            return
        self.archive_rows = []
        for root, _dirs, files in os.walk(folder):
            for file_name in files:
                if not file_name.lower().endswith(ARCHIVE_EXTENSIONS):
                    continue
                full = os.path.join(root, file_name)
                suggestion = _clean_archive_name(file_name)
                category = _infer_category_from_name(suggestion)
                meta = _parse_meta_file(full + ".meta")
                meta_text = ""
                if meta:
                    mod_id = meta.get("modID") or meta.get("modId") or meta.get("repository_id") or ""
                    file_id = meta.get("fileID") or meta.get("fileId") or ""
                    game = meta.get("gameName") or ""
                    meta_text = f"modID={mod_id} fileID={file_id} {game}".strip()
                self.archive_rows.append(
                    {
                        "path": full,
                        "file": file_name,
                        "suggestion": suggestion,
                        "category": category,
                        "size": self._format_size(os.path.getsize(full)),
                        "meta": meta_text or "缺 .meta / 未识别",
                    }
                )
        self._refresh_archive_table()

    def _refresh_archive_table(self):
        self.archive_table.setRowCount(0)
        for row_data in self.archive_rows:
            row = self.archive_table.rowCount()
            self.archive_table.insertRow(row)

            check_item = QtWidgets.QTableWidgetItem("")
            check_item.setFlags(_qt_item_enabled() | _qt_user_checkable())
            check_item.setCheckState(_qt_checked())
            check_item.setData(32, row_data["path"])
            self.archive_table.setItem(row, 0, check_item)

            for col, key in enumerate(["file", "suggestion", "category", "size", "meta"], start=1):
                item = QtWidgets.QTableWidgetItem(row_data[key])
                item.setData(32, row_data["path"])
                self.archive_table.setItem(row, col, item)

    def select_all_archives(self):
        for row in range(self.archive_table.rowCount()):
            item = self.archive_table.item(row, 0)
            if item:
                item.setCheckState(_qt_checked())

    def unselect_all_archives(self):
        for row in range(self.archive_table.rowCount()):
            item = self.archive_table.item(row, 0)
            if item:
                item.setCheckState(_qt_unchecked())

    def install_selected_archives(self):
        selected = []
        for row in range(self.archive_table.rowCount()):
            item = self.archive_table.item(row, 0)
            if item and item.checkState() == _qt_checked():
                path = item.data(32)
                suggestion_item = self.archive_table.item(row, 2)
                category_item = self.archive_table.item(row, 3)
                selected.append((path, suggestion_item.text() if suggestion_item else "", category_item.text() if category_item else "未分类"))
        if not selected:
            QtWidgets.QMessageBox.information(self, "没有选择", "请先选择要安装的压缩包。")
            return
        reply = QtWidgets.QMessageBox.question(
            self,
            "确认批量安装",
            f"将逐个交给 MO2 安装 {len(selected)} 个压缩包。\n复杂 FOMOD 仍会弹出安装向导。\n\n是否开始？",
            _standard_button("Yes") | _standard_button("No"),
            _standard_button("No"),
        )
        if reply != _standard_button("Yes"):
            return

        installed = []
        failed = []
        for archive_path, suggestion, category in selected:
            try:
                new_mod = self.organizer.installMod(archive_path, suggestion)
                if new_mod is None:
                    failed.append((archive_path, "用户取消或安装失败"))
                    continue
                new_name = _call_str(new_mod, "name", suggestion) or suggestion
                installed.append(new_name)
                if not self.install_enable_checkbox.isChecked():
                    self.mod_state.set_active([new_name], False)
                if self.install_move_checkbox.isChecked():
                    self._place_mod_by_category(new_name, category)
            except Exception as exc:
                failed.append((archive_path, str(exc)))

        if self.install_refresh_checkbox.isChecked():
            try:
                self.organizer.refresh(True)
            except Exception:
                pass
        msg = [f"安装完成：成功 {len(installed)}，失败/取消 {len(failed)}。"]
        if installed:
            msg.append("\n成功：")
            msg.extend("  + " + name for name in installed[:50])
        if failed:
            msg.append("\n失败/取消：")
            msg.extend("  - " + os.path.basename(path) + f"：{reason}" for path, reason in failed[:50])
        self._show_text_dialog("批量安装结果", "\n".join(msg))

    def _place_mod_by_category(self, mod_name: str, category: str) -> bool:
        mod_list = self.organizer.modList()
        separator_priorities = self._separator_priorities()
        keywords = []
        for cat, _pattern, keys in CATEGORY_RULES:
            if cat == category:
                keywords = keys + [cat]
                break
        target_priority = None
        for sep_name, priority in separator_priorities:
            sep_lower = sep_name.lower()
            if any(k.lower() in sep_lower for k in keywords):
                target_priority = priority + 1
        if target_priority is None:
            # 没有对应分隔符时，放到列表末尾附近。
            try:
                all_names = list(mod_list.allMods())
                target_priority = len(all_names) - 1
            except Exception:
                return False
        try:
            return bool(mod_list.setPriority(mod_name, target_priority))
        except Exception:
            return False

    def _separator_priorities(self) -> List[Tuple[str, int]]:
        result = []
        try:
            mod_list = self.organizer.modList()
            for name in mod_list.allMods():
                mod = mod_list.getMod(name)
                if mod is not None and _call_bool(mod, "isSeparator"):
                    result.append((name, int(mod_list.priority(name))))
        except Exception:
            pass
        return sorted(result, key=lambda x: x[1])

    # ---------- Sort helpers ----------
    def check_separators(self):
        separators = self._separator_priorities()
        names = [name for name, _priority in separators]
        lines = ["当前检测到的左侧分隔符：", ""]
        if not separators:
            lines.append("未检测到 MO2 分隔符。建议先在 MO2 左侧创建分隔符，再让本助手按分类移动新安装 Mod。")
        else:
            for name, priority in separators:
                lines.append(f"[{priority}] {name}")
        lines.append("\n建议至少准备这些区块：")
        for suggestion in DEFAULT_SEPARATOR_SUGGESTIONS:
            found = any(any(k.lower() in n.lower() for k in [suggestion, suggestion.split('_', 1)[-1]]) for n in names)
            lines.append(("✓ " if found else "□ ") + suggestion)
        self.sort_text.setPlainText("\n".join(lines))

    def generate_sort_plan(self):
        if not self.records:
            self.scan_mods()
        lines = ["左侧整理建议（只生成计划，不自动移动已有大列表）：", ""]
        for record in self.records:
            if record.status in ("分隔符", "Overwrite"):
                continue
            expected = _infer_category_from_name(record.display_name + " " + record.content)
            risk = ""
            if record.status in ("可更新", "缺元数据", "1.5.97锁定检查"):
                risk = f" / {record.status}"
            lines.append(f"[{record.priority}] {record.display_name} -> {expected}{risk}")
        self.sort_text.setPlainText("\n".join(lines))

    def export_sort_rules(self):
        path = os.path.join(self.report_dir, "sort_rules.json")
        data = [
            {"category": cat, "pattern": pattern.pattern, "separator_keywords": keywords}
            for cat, pattern, keywords in CATEGORY_RULES
        ]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        QtWidgets.QMessageBox.information(self, "已导出", f"整理规则已导出：\n{path}")
        self._open_folder(self.report_dir)

    # ---------- Release helpers ----------
    def choose_release_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择发布目录", self.release_dir_edit.text() or self._base_path())
        if path:
            self.release_dir_edit.setText(path)

    def generate_release_pack(self):
        if not self.records:
            self.scan_mods()
        release_dir = self.release_dir_edit.text().strip() or os.path.join(self._base_path(), "_XingliRelease")
        os.makedirs(release_dir, exist_ok=True)
        profile_snapshot_dir = os.path.join(release_dir, "profile_snapshot")
        os.makedirs(profile_snapshot_dir, exist_ok=True)

        # Manifest
        csv_path = os.path.join(release_dir, "manifest.csv")
        json_path = os.path.join(release_dir, "manifest.json")
        self._write_records(csv_path, json_path)

        # Profile snapshot
        for file_name in ["modlist.txt", "plugins.txt", "loadorder.txt", "archives.txt", "lockedorder.txt"]:
            src = os.path.join(self._profile_path(), file_name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(profile_snapshot_dir, file_name))

        launcher_bat_path = os.path.join(release_dir, "星黎整合启动器.bat")
        help_bat_path = os.path.join(release_dir, "星黎帮助与教程.bat")
        play_path = os.path.join(release_dir, "Play.bat")
        launch_ps1_path = os.path.join(release_dir, "XingliLaunch.ps1")
        game_name = self.launcher_game_name.text().strip() or "Skyrim Special Edition"
        exe_name = self.launcher_exe_name.text().strip() or "SKSE"
        with open(launcher_bat_path, "w", encoding="utf-8-sig", newline="\r\n") as handle:
            handle.write(self._make_play_bat("星黎整合启动器"))
        with open(play_path, "w", encoding="utf-8-sig", newline="\r\n") as handle:
            handle.write(self._make_play_bat("星黎整合启动器 - Play.bat 兼容入口"))
        with open(help_bat_path, "w", encoding="utf-8-sig", newline="\r\n") as handle:
            handle.write(self._make_help_bat())
        with open(launch_ps1_path, "w", encoding="utf-8-sig", newline="\r\n") as handle:
            handle.write(self._make_launch_ps1(game_name, exe_name))

        readme_path = os.path.join(release_dir, "README_新手必读.txt")
        with open(readme_path, "w", encoding="utf-8") as handle:
            handle.write(self._make_release_readme(game_name, exe_name))

        checklist_path = os.path.join(release_dir, "RELEASE_CHECKLIST.txt")
        with open(checklist_path, "w", encoding="utf-8") as handle:
            handle.write(self._make_release_checklist())

        self.release_text.setPlainText(
            f"发布骨架已生成：\n{release_dir}\n\n"
            f"核心文件：\n- {launcher_bat_path}\n- {help_bat_path}\n- {play_path}\n- {launch_ps1_path}\n- {readme_path}\n- {csv_path}\n- {json_path}\n- {profile_snapshot_dir}\n\n"
            "星黎整合启动器.bat / Play.bat 均不写死作者电脑路径；星黎帮助与教程.bat 会请求当前 MO2 自动打开小助手教程中心。"
        )
        self._open_folder(release_dir)

    def _make_play_bat(self, window_title: str = "星黎整合启动器") -> str:
        safe_title = window_title.replace('"', '').replace('&', '＆')
        return (
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            f"title {safe_title}\r\n"
            "setlocal\r\n"
            "set \"SCRIPT_DIR=%~dp0\"\r\n"
            "echo ============================================================\r\n"
            "echo  星黎整合启动器\r\n"
            "echo ============================================================\r\n"
            "echo  正在通过本机 MO2 启动 SKSE，请稍候...\r\n"
            "echo  如果启动失败，请把本窗口的报错截图发给整合作者。\r\n"
            "echo.\r\n"
            "powershell -NoProfile -ExecutionPolicy Bypass -File \"%SCRIPT_DIR%XingliLaunch.ps1\"\r\n"
            "if errorlevel 1 (\r\n"
            "  echo.\r\n"
            "  echo 启动器遇到错误，按任意键关闭。\r\n"
            "  pause >nul\r\n"
            ")\r\n"
        )

    def _make_help_bat(self) -> str:
        return (
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            "title 星黎帮助与教程\r\n"
            "setlocal\r\n"
            "set \"HERE=%~dp0\"\r\n"
            "set \"MO2DIR=%HERE%\"\r\n"
            "if exist \"%MO2DIR%ModOrganizer.exe\" goto :found\r\n"
            "for %%I in (\"%HERE%..\") do set \"MO2DIR=%%~fI\\\"\r\n"
            "if exist \"%MO2DIR%ModOrganizer.exe\" goto :found\r\n"
            "echo [错误] 未找到 ModOrganizer.exe。\r\n"
            "echo 请把本文件放在 MO2 根目录或 MO2 根目录下的 _XingliRelease/release 目录。\r\n"
            "pause\r\n"
            "exit /b 10\r\n"
            ":found\r\n"
            ">\"%MO2DIR%_XingliOpenHelp.request\" echo open\r\n"
            "start \"\" \"%MO2DIR%ModOrganizer.exe\"\r\n"
            "exit /b 0\r\n"
        )

    def _make_launch_ps1(self, game_name: str, exe_name: str) -> str:
        game_name_escaped = game_name.replace("'", "''")
        exe_name_escaped = exe_name.replace("'", "''")
        template = r"""
$ErrorActionPreference = 'Stop'
$GameName = '__GAME_NAME__'
$ExeName = '__EXE_NAME__'
$Host.UI.RawUI.WindowTitle = '星黎整合启动器'
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host ' 星黎整合启动器' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host '正在定位本机 MO2、SKSE 与整合路径...' -ForegroundColor Cyan
Write-Host ''

function Write-StopMessage([string]$Message) {
    Write-Host ''
    Write-Host $Message -ForegroundColor Red
    Write-Host ''
    exit 1
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptDir = [System.IO.Path]::GetFullPath($ScriptDir)

# 支持两种放置方式：
# 1) 星黎整合启动器.bat / Play.bat 与 ModOrganizer.exe 同级；
# 2) 星黎整合启动器.bat / Play.bat 位于 MO2 根目录下的 _XingliRelease / release 子目录。
$CandidateDirs = @(
    $ScriptDir,
    (Split-Path -Parent $ScriptDir),
    (Join-Path $ScriptDir 'MO2'),
    (Join-Path $ScriptDir 'ModOrganizer'),
    (Join-Path (Split-Path -Parent $ScriptDir) 'MO2'),
    (Join-Path (Split-Path -Parent $ScriptDir) 'ModOrganizer')
) | Select-Object -Unique

$MoDir = $null
foreach ($Dir in $CandidateDirs) {
    if ($Dir -and (Test-Path (Join-Path $Dir 'ModOrganizer.exe'))) {
        $MoDir = [System.IO.Path]::GetFullPath($Dir)
        break
    }
}

if (-not $MoDir) {
    Write-StopMessage '未找到本整合包内的 ModOrganizer.exe。请把【星黎整合启动器.bat】或 Play.bat 放到 MO2 根目录，或放到 MO2 根目录下的 _XingliRelease / release 子目录。'
}

$MoExe = Join-Path $MoDir 'ModOrganizer.exe'
$MoIni = Join-Path $MoDir 'ModOrganizer.ini'

# 尽量寻找“本机/本整合包”里的 skse64_loader.exe，不使用作者电脑上的绝对路径。
$SkseCandidates = @(
    (Join-Path $MoDir 'Stock Game\skse64_loader.exe'),
    (Join-Path $MoDir 'Game Root\skse64_loader.exe'),
    (Join-Path $MoDir 'Game\skse64_loader.exe'),
    (Join-Path $MoDir 'Skyrim Special Edition\skse64_loader.exe'),
    (Join-Path (Split-Path -Parent $MoDir) 'Stock Game\skse64_loader.exe'),
    (Join-Path (Split-Path -Parent $MoDir) 'Skyrim Special Edition\skse64_loader.exe')
)
$SksePath = $null
foreach ($Candidate in $SkseCandidates) {
    if (Test-Path $Candidate) {
        $SksePath = [System.IO.Path]::GetFullPath($Candidate)
        break
    }
}

# 如果不是 Stock Game 结构，则允许读取 MO2 当前 INI 的 gamePath，再在该路径下查找 SKSE。
if (-not $SksePath -and (Test-Path $MoIni)) {
    $IniText = Get-Content -LiteralPath $MoIni -Raw -Encoding UTF8
    $GamePathMatch = [regex]::Match($IniText, '(?m)^gamePath=(.+)$')
    if ($GamePathMatch.Success) {
        $GamePath = $GamePathMatch.Groups[1].Value.Trim()
        if ($GamePath -and (Test-Path (Join-Path $GamePath 'skse64_loader.exe'))) {
            $SksePath = [System.IO.Path]::GetFullPath((Join-Path $GamePath 'skse64_loader.exe'))
        }
    }
}

# 主动修正 ModOrganizer.ini 中名为 SKSE 的 custom executable，避免残留作者电脑路径。
# 这一步只修正 title 等于 $ExeName 的条目；找不到条目则不强行新建，仍交给 moshortcut 报错/由 MO2 打开。
if ($SksePath -and (Test-Path $MoIni)) {
    $Lines = [System.Collections.Generic.List[string]]::new()
    [string[]]$RawLines = Get-Content -LiteralPath $MoIni -Encoding UTF8
    foreach ($Line in $RawLines) { [void]$Lines.Add($Line) }

    $TargetIndex = $null
    foreach ($Line in $Lines) {
        $TitleMatch = [regex]::Match($Line, '^([0-9]+)\\title=(.+)$')
        if ($TitleMatch.Success -and $TitleMatch.Groups[2].Value.Trim() -eq $ExeName) {
            $TargetIndex = $TitleMatch.Groups[1].Value
            break
        }
    }

    if ($TargetIndex) {
        $SkseDir = Split-Path -Parent $SksePath
        $SafeSksePath = $SksePath.Replace('\', '/')
        $SafeSkseDir = $SkseDir.Replace('\', '/')
        for ($i = 0; $i -lt $Lines.Count; $i++) {
            if ($Lines[$i] -match ('^' + [regex]::Escape($TargetIndex) + '\\binary=')) {
                $Lines[$i] = "$TargetIndex\binary=$SafeSksePath"
            }
            elseif ($Lines[$i] -match ('^' + [regex]::Escape($TargetIndex) + '\\workingDirectory=')) {
                $Lines[$i] = "$TargetIndex\workingDirectory=$SafeSkseDir"
            }
        }
        Set-Content -LiteralPath $MoIni -Value $Lines -Encoding UTF8
        Write-Host "已校正 MO2 可执行项 $ExeName 到本机路径：$SksePath" -ForegroundColor Green
    }
}

Write-Host "启动器入口：星黎整合启动器.bat / Play.bat" -ForegroundColor Cyan
Write-Host "MO2：$MoExe" -ForegroundColor Cyan
if ($SksePath) { Write-Host "SKSE：$SksePath" -ForegroundColor Cyan }
else { Write-Host "未能预先定位 skse64_loader.exe，将尝试使用 MO2 内已有的 $ExeName 可执行项。" -ForegroundColor Yellow }

$ShortcutArg = "moshortcut://$GameName`:$ExeName"
Write-Host "即将通过 MO2 快捷方式启动：$ShortcutArg" -ForegroundColor Green
Start-Process -FilePath $MoExe -ArgumentList @($ShortcutArg) -WorkingDirectory $MoDir
exit 0
"""
        return template.replace('__GAME_NAME__', game_name_escaped).replace('__EXE_NAME__', exe_name_escaped).lstrip()

    def _make_release_readme(self, game_name: str, exe_name: str) -> str:
        return (
            "星黎整合包 - 新手启动说明\n"
            "========================\n\n"
            "最重要：如果你只是想玩游戏，请双击【星黎整合启动器.bat】。\n"
            "Play.bat 是兼容旧说明的备用入口，和【星黎整合启动器.bat】使用同一套启动逻辑。\n"
            "需要教程、更新或排错时，双击【星黎帮助与教程.bat】；它会打开 MO2 并请求星黎小助手自动显示教程中心。\n\n"
            "1. 请确认你使用的是正版 Skyrim Special Edition，并且运行时版本符合整合要求。\n"
            "2. 解压整合包后，不要把 MO2 放在 Program Files、桌面、下载目录等受系统权限影响的位置。\n"
            "3. 双击【星黎整合启动器.bat】启动游戏；不需要先打开 MO2。需要帮助则双击【星黎帮助与教程.bat】。\n"
            "4. 启动器会优先使用本整合包/MO2 根目录下的 ModOrganizer.exe，不使用作者电脑路径。\n"
            "5. 如果检测到本地 skse64_loader.exe，会自动校正 MO2 里名为 " + exe_name + " 的可执行项路径。\n"
            "6. 如果启动器只打开了 MO2 而没有进游戏，请在 MO2 右上角确认存在名为 " + exe_name + " 的可执行项。\n"
            "7. 不要随意点击 MO2 的“全部更新”。Skyrim SE 1.5.97 对 SKSE DLL 类 Mod 版本非常敏感。\n\n"
            f"启动器当前调用：moshortcut://{game_name}:{exe_name}\n"
        )

    def _make_release_checklist(self) -> str:
        counts: Dict[str, int] = {}
        for record in self.records:
            counts[record.status] = counts.get(record.status, 0) + 1
        lines = [
            "发布前检查清单",
            "============",
            f"生成时间：{datetime.now().isoformat(timespec='seconds')}",
            f"Profile：{self._profile_name()}",
            "",
            "状态统计：",
        ]
        for key, value in sorted(counts.items()):
            lines.append(f"- {key}: {value}")
        lines.extend(
            [
                "",
                "发布前建议：",
                "- 确认所有 SKSE DLL 类 Mod 支持 SkyrimSE.exe 1.5.97。",
                "- 确认 Overwrite 已清空或转移到专用输出 Mod。",
                "- 确认当前 Profile 的 modlist/plugins/loadorder 是最终版本。",
                "- 给新手提供明确的安装路径、启动方式和常见报错处理。",
                "- 发布包根目录必须包含【星黎整合启动器.bat】和【星黎帮助与教程.bat】；Play.bat 仅作为兼容入口保留。",
                "- 第一次打开 MO2 的欢迎提示必须同时说明：游戏用启动器，教程/排错用帮助入口。",
                "- 如果做 Wabbajack，确保 downloads 中 archive 的 .meta 元数据完整。",
            ]
        )
        return "\n".join(lines)

    def launch_skse_from_mo(self):
        game_path = getattr(self.controller, "game_path", None) or ""
        if not game_path or not os.path.isdir(game_path):
            QtWidgets.QMessageBox.warning(self, "游戏路径无效", "未找到有效游戏路径，请先在主助手里重新扫描/设置路径。")
            return
        skse_path = os.path.join(game_path, "skse64_loader.exe")
        if not os.path.exists(skse_path):
            QtWidgets.QMessageBox.warning(self, "缺少 SKSE", f"没有找到：\n{skse_path}")
            return
        try:
            handle = self.organizer.startApplication(skse_path, [], game_path, self._profile_name())
            if handle:
                QtWidgets.QMessageBox.information(self, "已启动", "已通过 MO2 VFS 启动 skse64_loader.exe。")
            else:
                QtWidgets.QMessageBox.warning(self, "启动失败", "MO2 未能启动游戏。")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "启动失败", f"启动 SKSE 失败：\n{exc}")

    def open_release_dir(self):
        release_dir = self.release_dir_edit.text().strip() or os.path.join(self._base_path(), "_XingliRelease")
        os.makedirs(release_dir, exist_ok=True)
        self._open_folder(release_dir)

    # ---------- Utility ----------
    def _format_size(self, bytes_size: int) -> str:
        size = float(bytes_size)
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}"
            size /= 1024
        return str(bytes_size)

    def _open_folder(self, path: str):
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                webbrowser.open("file://" + os.path.abspath(path))
        except Exception:
            webbrowser.open("file://" + os.path.abspath(path))

    def _show_text_dialog(self, title: str, text: str):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(900, 650)
        layout = QtWidgets.QVBoxLayout(dialog)
        edit = QtWidgets.QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(text)
        layout.addWidget(edit)
        close_btn = QtWidgets.QPushButton("关闭")
        close_btn.clicked.connect(dialog.close)
        layout.addWidget(close_btn)
        _exec_dialog(dialog)


def show_mod_ops_assistant(controller):
    parent = None
    try:
        parent = controller._parentWidget()
    except Exception:
        parent = getattr(controller, "window", None)
    dialog = ModOpsAssistantDialog(controller, parent)
    _exec_dialog(dialog)
