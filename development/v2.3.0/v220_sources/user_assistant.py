# coding=utf-8
"""
星黎 MO2 小助手 - 用户助手模块

面向普通整合包用户：
1. 明确提示用户优先使用桌面【星黎整合启动器.bat】启动游戏。
2. 在用户误打开 MO2 时，提供一键通过 MO2 VFS 启动 SKSE 的入口。
3. 做只读环境自检，发现常见路径、Profile、SKSE、启动器、Overwrite 问题。
4. 仅提供低风险修复：把 MO2 中已有的 SKSE 可执行项校正到本机 skse64_loader.exe。
5. 保留玩家需要的 ENB 启用/更换/禁用入口，并提供自动更新。

本模块只保留玩家常用功能。
"""

import os
import re
import webbrowser
from datetime import datetime
from typing import List, Optional, Tuple

from . import auto_updater

try:
    import PyQt6.QtWidgets as QtWidgets
    import PyQt6.QtGui as QtGui
    from PyQt6.QtCore import Qt
except ImportError:
    import PyQt5.QtWidgets as QtWidgets
    import PyQt5.QtGui as QtGui
    from PyQt5.QtCore import Qt


def _exec_dialog(dialog):
    if hasattr(dialog, "exec"):
        return dialog.exec()
    return dialog.exec_()


def _align_center():
    if hasattr(Qt, "AlignmentFlag"):
        return Qt.AlignmentFlag.AlignCenter
    return Qt.AlignCenter


class UserAssistantDialog(QtWidgets.QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.organizer = controller.organizer
        self.plugin_path = getattr(controller, "plugin_path", os.path.dirname(__file__))
        self.setWindowTitle("星黎整合用户助手 - 启动 / 自检 / 修复")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.resize(920, 680)
        self.last_report = ""
        self._build_ui()
        self.run_health_check()

    # ---------- UI ----------
    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)

        title = QtWidgets.QLabel("星黎整合用户助手")
        font = QtGui.QFont()
        font.setPointSize(15)
        font.setBold(True)
        title.setFont(font)
        title.setAlignment(_align_center())
        layout.addWidget(title)

        notice = QtWidgets.QLabel(
            "只想进入游戏时，使用桌面的【星黎整合启动器.bat】；教程可用【星黎帮助与教程.bat】。\n"
            "遇到启动问题时，可在这里自检、修复；助手更新与整合包更新分别显示，避免混淆。"
        )
        notice.setWordWrap(True)
        notice.setAlignment(_align_center())
        notice.setStyleSheet(
            "QLabel { background-color: #fff2cc; color: #5f4300; border: 1px solid #d6b656; "
            "border-radius: 6px; padding: 10px; font-size: 12px; }"
        )
        layout.addWidget(notice)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addWidget(self._button("🎮 通过 MO2 启动游戏", self.launch_game, "通过当前 MO2 配置启动游戏"))
        button_row.addWidget(self._button("🔍 重新自检", self.run_health_check))
        button_row.addWidget(self._button("🛠 修复 SKSE 路径", self.repair_skse_executable, "修复 MO2 中的 SKSE 启动路径"))
        button_row.addWidget(self._button("🖥 重建桌面启动器", self.create_desktop_launcher, "在桌面创建或刷新星黎整合启动器"))
        button_row.addWidget(self._button("📋 复制诊断报告", self.copy_report))
        layout.addLayout(button_row)

        tools_row = QtWidgets.QHBoxLayout()
        tools_row.addWidget(self._button("🎨 ENB 管理", self.open_enb_manager, "启用 / 更换 / 禁用 ENB 预设"))
        tools_row.addWidget(self._button("🧱 PBR / PGPatcher", self.controller.launch_pgpatcher, "检测并通过当前 MO2 VFS 启动 PGPatcher"))
        tools_row.addWidget(self._button("🔄 助手更新", self.open_auto_updater, "只检查和更新星黎 MO2 小助手"))
        tools_row.addWidget(self._button("📦 整合包更新", self.controller.show_modpack_update, "检查整合包内容版本并在需要时打开网盘"))
        tools_row.addWidget(self._button("📖 新手指南", self.controller.open_tutorial, "打开与当前版本同步的内置教程"))
        tools_row.addStretch(1)
        layout.addLayout(tools_row)

        open_row = QtWidgets.QHBoxLayout()
        open_row.addWidget(self._button("📁 打开 MO2 目录", self.open_mo_dir))
        open_row.addWidget(self._button("📁 打开当前配置目录", self.open_profile_dir))
        open_row.addWidget(self._button("📁 打开启动器位置", self.open_launcher_dir))
        open_row.addStretch(1)
        layout.addLayout(open_row)

        self.report_text = QtWidgets.QPlainTextEdit()
        self.report_text.setReadOnly(True)
        layout.addWidget(self.report_text, 1)

        help_box = QtWidgets.QGroupBox("新手注意事项")
        help_layout = QtWidgets.QVBoxLayout(help_box)
        help = QtWidgets.QLabel(
            "1. 不要随意更新核心组件，也不要随意调整 MOD 或插件顺序。\n"
            "2. ENB 建议通过【ENB 管理】更换或禁用。\n"
            "3. 启动失败时先运行自检，再把诊断报告发给维护人员。\n"
            "4. 日常启动优先使用桌面的【星黎整合启动器.bat】。"
        )
        help.setWordWrap(True)
        help_layout.addWidget(help)
        layout.addWidget(help_box)

        close_button = QtWidgets.QPushButton("关闭")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

    def _button(self, text, callback, tooltip=""):
        btn = QtWidgets.QPushButton(text)
        if tooltip:
            btn.setToolTip(tooltip)
        btn.clicked.connect(callback)
        return btn

    # ---------- Paths ----------
    def _base_path(self):
        try:
            path = self.organizer.basePath()
            if path:
                return os.path.abspath(path)
        except Exception:
            pass
        return os.path.abspath(os.path.join(self.plugin_path, "..", ".."))

    def _profile_path(self):
        try:
            path = self.organizer.profilePath()
            if path:
                return os.path.abspath(path)
        except Exception:
            pass
        return os.path.join(self._base_path(), "profiles")

    def _profile_name(self):
        try:
            return self.organizer.profileName() or "CurrentProfile"
        except Exception:
            return "CurrentProfile"

    def _mo_ini_path(self):
        return os.path.join(self._base_path(), "ModOrganizer.ini")

    def _mo_exe_path(self):
        return os.path.join(self._base_path(), "ModOrganizer.exe")

    def _candidate_game_dirs(self) -> List[str]:
        base = self._base_path()
        parent = os.path.dirname(base)
        candidates = []

        controller_game_path = getattr(self.controller, "game_path", None)
        if controller_game_path:
            candidates.append(controller_game_path)

        for rel in [
            "Stock Game", "Game Root", "Game", "Skyrim Special Edition", "SkyrimSE",
            os.path.join("..", "Stock Game"), os.path.join("..", "Skyrim Special Edition"),
        ]:
            candidates.append(os.path.abspath(os.path.join(base, rel)))

        candidates.extend([
            os.path.join(parent, "Stock Game"),
            os.path.join(parent, "Skyrim Special Edition"),
        ])

        ini_game = self._read_game_path_from_mo_ini()
        if ini_game:
            candidates.append(ini_game)

        result = []
        for path in candidates:
            if path and path not in result:
                result.append(path)
        return result

    def _read_game_path_from_mo_ini(self) -> Optional[str]:
        ini = self._mo_ini_path()
        if not os.path.exists(ini):
            return None
        try:
            with open(ini, "r", encoding="utf-8", errors="ignore") as handle:
                text = handle.read()
        except Exception:
            return None
        match = re.search(r"(?m)^gamePath=(.+)$", text)
        if match:
            return match.group(1).strip().strip('"')
        return None

    def _find_skse(self) -> Tuple[Optional[str], Optional[str]]:
        for game_dir in self._candidate_game_dirs():
            skse = os.path.join(game_dir, "skse64_loader.exe")
            if os.path.exists(skse):
                return os.path.abspath(skse), os.path.abspath(game_dir)
        return None, None

    def _launcher_candidates(self) -> List[str]:
        base = self._base_path()
        parent = os.path.dirname(base)
        names = ["星黎整合启动器.bat", "Play.bat"]
        desktop = None
        try:
            if hasattr(self.controller, "_desktop_dir"):
                desktop = self.controller._desktop_dir()
        except Exception:
            desktop = None
        dirs = [
            desktop,
            base,
            os.path.join(base, "_XingliRelease"),
            os.path.join(base, "release"),
            parent,
            os.path.join(parent, "_XingliRelease"),
            os.path.join(parent, "release"),
        ]
        paths = []
        for d in dirs:
            if not d:
                continue
            for n in names:
                p = os.path.abspath(os.path.join(d, n))
                if p not in paths:
                    paths.append(p)
        return paths

    def _find_launcher(self) -> Optional[str]:
        for path in self._launcher_candidates():
            if os.path.exists(path):
                return path
        return None

    def _read_skse_executable_entry(self):
        ini = self._mo_ini_path()
        if not os.path.exists(ini):
            return None
        try:
            with open(ini, "r", encoding="utf-8", errors="ignore") as handle:
                lines = handle.read().splitlines()
        except Exception:
            return None

        target_index = None
        for line in lines:
            m = re.match(r"^([0-9]+)\\title=(.+)$", line)
            if m and m.group(2).strip().lower() == "skse":
                target_index = m.group(1)
                break
        if target_index is None:
            return None

        entry = {"index": target_index, "title": "SKSE", "binary": "", "workingDirectory": ""}
        for line in lines:
            for key in ["binary", "workingDirectory"]:
                prefix = f"{target_index}\\{key}="
                if line.startswith(prefix):
                    entry[key] = line[len(prefix):].strip()
        return entry

    # ---------- Actions ----------
    def run_health_check(self):
        base = self._base_path()
        profile = self._profile_path()
        mo_exe = self._mo_exe_path()
        mo_ini = self._mo_ini_path()
        skse_path, game_dir = self._find_skse()
        if hasattr(self.controller, "ensure_desktop_launcher"):
            self.controller.ensure_desktop_launcher(silent=True)
        launcher = self._find_launcher()
        skse_entry = self._read_skse_executable_entry()
        pg_status = None
        try:
            from . import pgpatcher_adapter
            pg_status = pgpatcher_adapter.inspect(self.organizer)
        except Exception:
            pg_status = None

        lines = []
        lines.append("星黎整合用户自检报告")
        lines.append("====================")
        lines.append(f"时间：{datetime.now().isoformat(timespec='seconds')}")
        lines.append(f"MO2 根目录：{base}")
        lines.append(f"当前 Profile：{self._profile_name()}")
        lines.append(f"Profile 路径：{profile}")
        lines.append("")

        def ok(text):
            lines.append("[OK] " + text)

        def warn(text):
            lines.append("[注意] " + text)

        def bad(text):
            lines.append("[需要处理] " + text)

        if os.path.exists(mo_exe):
            ok(f"找到 ModOrganizer.exe：{mo_exe}")
        else:
            bad(f"未找到 ModOrganizer.exe：{mo_exe}")

        if os.path.exists(mo_ini):
            ok(f"找到 ModOrganizer.ini：{mo_ini}")
        else:
            warn("未找到 ModOrganizer.ini，可能不是 Portable MO2 结构，或 MO2 尚未初始化。")

        if launcher:
            ok(f"找到星黎启动入口：{launcher}")
        else:
            warn("未找到【星黎整合启动器.bat】或 Play.bat。仍可在本窗口尝试通过 MO2 启动游戏。")

        if skse_path:
            ok(f"找到本机 skse64_loader.exe：{skse_path}")
        else:
            bad("未找到 skse64_loader.exe。请确认整合包/Stock Game/游戏根目录中存在 SKSE。")

        if skse_entry:
            entry_binary = skse_entry.get("binary", "")
            entry_dir = skse_entry.get("workingDirectory", "")
            ok(f"MO2 中存在名为 SKSE 的可执行项。")
            lines.append(f"     binary={entry_binary}")
            lines.append(f"     workingDirectory={entry_dir}")
            if skse_path and entry_binary:
                norm_entry = os.path.normcase(os.path.abspath(entry_binary.replace('/', os.sep)))
                norm_skse = os.path.normcase(os.path.abspath(skse_path))
                if norm_entry != norm_skse:
                    warn("SKSE 可执行项路径与本机检测到的 skse64_loader.exe 不一致，可点击【修复 SKSE 路径】。")
                else:
                    ok("SKSE 可执行项路径与本机路径一致。")
        else:
            warn("MO2 中未找到名为 SKSE 的可执行项。请让整合包维护人员确认整合是否正确。")

        if pg_status:
            if pg_status.get("exe"):
                ok("找到 PGPatcher：{}".format(pg_status.get("exe")))
                if pg_status.get("active_outputs"):
                    warn("PGPatcher 输出 MOD 当前已启用；重新生成前必须先禁用。")
                if pg_status.get("active_dyndolod"):
                    warn("DynDOLOD 输出当前已启用；PGPatcher 应在 TexGen / DynDOLOD 之前运行。")
            else:
                warn("未找到 PGPatcher.exe；如果整合使用 TruePBR 材质，需要安装并通过 MO2 运行 PGPatcher。")

        for required in ["modlist.txt", "plugins.txt", "loadorder.txt"]:
            p = os.path.join(profile, required)
            if os.path.exists(p):
                ok(f"Profile 文件存在：{required}")
            else:
                warn(f"Profile 文件缺失：{required}")

        overwrite = os.path.join(base, "overwrite")
        if os.path.isdir(overwrite):
            try:
                has_items = any(os.scandir(overwrite))
            except Exception:
                has_items = False
            if has_items:
                warn("overwrite 目录中存在文件。不熟悉 MO2 时不要自行移动。启动失败时，请复制诊断报告。")
            else:
                ok("overwrite 目录为空。")

        lines.append("")
        lines.append("建议启动方式：")
        lines.append("1. 首选：关闭 MO2，双击桌面【星黎整合启动器.bat】。")
        lines.append("2. 如果你已经打开了 MO2：点击本窗口【通过 MO2 启动游戏】。")
        lines.append("3. 失败时：点击【复制诊断报告】发给整合包维护人员。")

        self.last_report = "\n".join(lines)
        self.report_text.setPlainText(self.last_report)

    def launch_game(self):
        skse_path, game_dir = self._find_skse()
        if not skse_path or not game_dir:
            QtWidgets.QMessageBox.warning(self, "缺少 SKSE", "没有找到 skse64_loader.exe。请先运行自检，并把诊断报告发给整合包维护人员。")
            return
        try:
            handle = self.organizer.startApplication(skse_path, [], game_dir, self._profile_name())
            if handle:
                QtWidgets.QMessageBox.information(self, "已启动", "游戏已通过 MO2 启动。")
            else:
                QtWidgets.QMessageBox.warning(self, "启动失败", "MO2 返回启动失败。请复制诊断报告发给整合包维护人员。")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "启动失败", f"启动 SKSE 失败：\n{exc}")

    def repair_skse_executable(self):
        skse_path, game_dir = self._find_skse()
        if not skse_path or not game_dir:
            QtWidgets.QMessageBox.warning(self, "缺少 SKSE", "没有找到本机 skse64_loader.exe，无法修复路径。")
            return
        ini = self._mo_ini_path()
        if not os.path.exists(ini):
            QtWidgets.QMessageBox.warning(self, "缺少 INI", "未找到 ModOrganizer.ini，无法修复 MO2 可执行项。")
            return
        entry = self._read_skse_executable_entry()
        if not entry:
            QtWidgets.QMessageBox.warning(self, "未找到 SKSE 项", "MO2 中未找到名为 SKSE 的可执行项。请联系整合包维护人员处理。")
            return

        reply = QtWidgets.QMessageBox.question(
            self,
            "确认修复 SKSE 路径",
            "将把 MO2 中名为 SKSE 的可执行项修正为本机路径：\n\n"
            f"binary={skse_path}\nworkingDirectory={game_dir}\n\n继续吗？",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No if hasattr(QtWidgets.QMessageBox, "StandardButton") else QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        yes = QtWidgets.QMessageBox.StandardButton.Yes if hasattr(QtWidgets.QMessageBox, "StandardButton") else QtWidgets.QMessageBox.Yes
        if reply != yes:
            return

        idx = entry["index"]
        try:
            with open(ini, "r", encoding="utf-8", errors="ignore") as handle:
                lines = handle.read().splitlines()
            new_lines = []
            fixed_binary = False
            fixed_workdir = False
            safe_skse = skse_path.replace('\\', '/')
            safe_dir = game_dir.replace('\\', '/')
            for line in lines:
                if re.match(r"^" + re.escape(idx) + r"\\binary=", line):
                    new_lines.append(f"{idx}\\binary={safe_skse}")
                    fixed_binary = True
                elif re.match(r"^" + re.escape(idx) + r"\\workingDirectory=", line):
                    new_lines.append(f"{idx}\\workingDirectory={safe_dir}")
                    fixed_workdir = True
                else:
                    new_lines.append(line)
            if not fixed_binary:
                new_lines.append(f"{idx}\\binary={safe_skse}")
            if not fixed_workdir:
                new_lines.append(f"{idx}\\workingDirectory={safe_dir}")
            with open(ini, "w", encoding="utf-8", newline="\n") as handle:
                handle.write("\n".join(new_lines) + "\n")
            QtWidgets.QMessageBox.information(self, "修复完成", "已修复 SKSE 可执行项路径。请重新启动游戏。")
            self.run_health_check()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "修复失败", f"修复 ModOrganizer.ini 失败：\n{exc}")


    def open_enb_manager(self):
        """打开原版 ENB 管理功能。"""
        if not hasattr(self.controller, "manage_enb"):
            QtWidgets.QMessageBox.warning(self, "功能不可用", "当前插件版本没有 ENB 管理功能。")
            return
        try:
            self.controller.manage_enb()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "ENB 管理打开失败", f"打开 ENB 管理失败：\n{exc}")

    def open_auto_updater(self):
        """打开自动更新窗口。"""
        try:
            auto_updater.show_auto_updater(self.controller, self)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "自动更新打开失败", f"打开自动更新失败：\n{exc}")

    def create_desktop_launcher(self):
        if not hasattr(self.controller, "ensure_desktop_launcher"):
            QtWidgets.QMessageBox.warning(self, "功能不可用", "当前插件版本不支持自动创建桌面启动器。")
            return
        path = self.controller.ensure_desktop_launcher(silent=False)
        if path:
            self.run_health_check()

    def copy_report(self):
        if not self.last_report:
            self.run_health_check()
        app = QtWidgets.QApplication.instance()
        if app:
            app.clipboard().setText(self.last_report)
            QtWidgets.QMessageBox.information(self, "已复制", "诊断报告已复制到剪贴板。")

    def open_mo_dir(self):
        self._open_folder(self._base_path())

    def open_profile_dir(self):
        self._open_folder(self._profile_path())

    def open_launcher_dir(self):
        launcher = self._find_launcher()
        if launcher:
            self._open_folder(os.path.dirname(launcher))
        else:
            self._open_folder(self._base_path())

    def _open_folder(self, path):
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                webbrowser.open("file://" + os.path.abspath(path))
        except Exception:
            webbrowser.open("file://" + os.path.abspath(path))


def show_user_assistant(controller):
    parent = None
    try:
        parent = controller._parentWidget()
    except Exception:
        parent = getattr(controller, "window", None)
    dialog = UserAssistantDialog(controller, parent)
    _exec_dialog(dialog)
