# coding=utf-8

import shutil
import sys
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError("{}: expected exactly one match, got {}".format(label, count))
    return text.replace(old, new, 1)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply_pgpatcher_support.py <plugin-root>")
    root = Path(sys.argv[1]).resolve()
    source_dir = Path(__file__).resolve().parent

    controller_path = root / "consolidation_controller.py"
    text = controller_path.read_text(encoding="utf-8-sig")
    text = replace_once(
        text,
        "from . import onboarding_state\n",
        "from . import onboarding_state\nfrom . import pgpatcher_adapter\n",
        "controller PGPatcher import",
    )
    text = replace_once(
        text,
        '        crash_action = more_menu.addAction("崩溃日志查看器")\n        crash_action.triggered.connect(self.show_crash_log_viewer)\n',
        '        crash_action = more_menu.addAction("崩溃日志查看器")\n'
        '        crash_action.triggered.connect(self.show_crash_log_viewer)\n'
        '        pgpatcher_action = more_menu.addAction("PBR / PGPatcher")\n'
        '        pgpatcher_action.triggered.connect(self.launch_pgpatcher)\n',
        "advanced tools PGPatcher action",
    )

    anchor = '''    def show_user_assistant(self):\n        """打开用户助手：启动游戏、自检环境、修复 SKSE 路径。"""\n'''
    method = '''    def launch_pgpatcher(self):\n        """预检并通过当前 MO2 VFS 启动 PGPatcher。"""\n        parent = self.window if hasattr(self, "window") else None\n        try:\n            status = pgpatcher_adapter.inspect(self.organizer)\n        except Exception as exc:\n            logger.exception("PGPatcher 预检失败")\n            QtWidgets.QMessageBox.critical(parent, "PGPatcher", "PGPatcher 预检失败：\\n{}".format(exc))\n            return\n\n        exe = str(status.get("exe", "") or "")\n        if not exe:\n            box = QtWidgets.QMessageBox(parent)\n            box.setWindowTitle("未找到 PGPatcher")\n            box.setIcon(QtWidgets.QMessageBox.Icon.Warning if hasattr(QtWidgets.QMessageBox, "Icon") else QtWidgets.QMessageBox.Warning)\n            box.setText("未检测到 PGPatcher.exe。\\n\\nTruePBR 材质需要先安装 PGPatcher，然后从 MO2 环境运行。")\n            nexus_button = box.addButton("打开 Nexus 官方页", QtWidgets.QMessageBox.ButtonRole.ActionRole if hasattr(QtWidgets.QMessageBox, "ButtonRole") else QtWidgets.QMessageBox.ActionRole)\n            github_button = box.addButton("打开 GitHub", QtWidgets.QMessageBox.ButtonRole.ActionRole if hasattr(QtWidgets.QMessageBox, "ButtonRole") else QtWidgets.QMessageBox.ActionRole)\n            box.addButton("关闭", QtWidgets.QMessageBox.ButtonRole.RejectRole if hasattr(QtWidgets.QMessageBox, "ButtonRole") else QtWidgets.QMessageBox.RejectRole)\n            box.exec() if hasattr(box, "exec") else box.exec_()\n            clicked = box.clickedButton()\n            if clicked is nexus_button:\n                webbrowser.open(pgpatcher_adapter.OFFICIAL_NEXUS_URL)\n            elif clicked is github_button:\n                webbrowser.open(pgpatcher_adapter.OFFICIAL_GITHUB_URL)\n            return\n\n        if not bool(status.get("instance_ini_ok", False)):\n            QtWidgets.QMessageBox.warning(\n                parent,\n                "PGPatcher：MO2 实例路径异常",\n                "当前 MO2 基础目录下没有找到 ModOrganizer.ini：\\n{}\\n\\n"\n                "PGPatcher 的 MO2 Instance Location 必须指向包含 ModOrganizer.ini 的实例目录。".format(\n                    status.get("instance_path", "")\n                ),\n            )\n            return\n\n        dyndolod = list(status.get("active_dyndolod", []) or [])\n        if dyndolod:\n            QtWidgets.QMessageBox.warning(\n                parent,\n                "PGPatcher：请先禁用 DynDOLOD 输出",\n                "检测到以下 DynDOLOD 输出仍处于启用状态：\\n- {}\\n\\n"\n                "PGPatcher 应在 TexGen / DynDOLOD 之前运行。请先禁用这些输出，再重新启动 PGPatcher。".format(\n                    "\\n- ".join(dyndolod)\n                ),\n            )\n            return\n\n        active_outputs = list(status.get("active_outputs", []) or [])\n        if active_outputs:\n            yes = QtWidgets.QMessageBox.StandardButton.Yes if hasattr(QtWidgets.QMessageBox, "StandardButton") else QtWidgets.QMessageBox.Yes\n            no = QtWidgets.QMessageBox.StandardButton.No if hasattr(QtWidgets.QMessageBox, "StandardButton") else QtWidgets.QMessageBox.No\n            reply = QtWidgets.QMessageBox.question(\n                parent,\n                "PGPatcher：需要禁用旧输出",\n                "PGPatcher 官方流程要求运行前禁用旧的输出 MOD。\\n\\n"\n                "当前启用：\\n- {}\\n\\n"\n                "是否由星黎小助手先禁用它们，再启动 PGPatcher？".format("\\n- ".join(active_outputs)),\n                yes | no,\n            )\n            if reply != yes:\n                return\n            ok, failures = pgpatcher_adapter.disable_output_mods(self.organizer, active_outputs)\n            if not ok:\n                QtWidgets.QMessageBox.critical(\n                    parent,\n                    "PGPatcher",\n                    "无法禁用以下输出 MOD：\\n- {}\\n\\n请手动禁用后再试。".format("\\n- ".join(failures)),\n                )\n                return\n\n        texgen = list(status.get("active_texgen", []) or [])\n        details = [\n            "将通过当前 MO2 VFS 启动 PGPatcher。",\n            "",\n            "PGPatcher：{}".format(exe),\n            "MO2 Instance Location：{}".format(status.get("instance_path", "")),\n            "推荐 Output Directory：{}".format(status.get("recommended_output", "")),\n            "",\n            "首次配置时选择 Mod Manager = MO2，Output 建议直接指向独立 PGPatcher_Output MOD，并关闭 ZIP 输出。",\n            "补丁完成后回到 MO2 启用 PGPatcher_Output；随后再运行 TexGen / DynDOLOD。",\n            "TruePBR 需要 Community Shaders 的 PBR 功能。",\n        ]\n        if texgen:\n            details.extend(["", "注意：当前 TexGen 输出已启用。PGPatcher 运行后建议重新生成 TexGen / DynDOLOD。"] )\n        QtWidgets.QMessageBox.information(parent, "PGPatcher 适配", "\\n".join(details))\n        try:\n            pgpatcher_adapter.launch(self.organizer, exe)\n        except Exception as exc:\n            logger.exception("启动 PGPatcher 失败")\n            QtWidgets.QMessageBox.critical(parent, "PGPatcher", "启动 PGPatcher 失败：\\n{}".format(exc))\n\n'''
    text = replace_once(text, anchor, method + anchor, "controller PGPatcher launch method")
    controller_path.write_text(text, encoding="utf-8")

    user_path = root / "user_assistant.py"
    text = user_path.read_text(encoding="utf-8-sig")
    text = replace_once(
        text,
        '        tools_row.addWidget(self._button("🎨 ENB 管理", self.open_enb_manager, "启用 / 更换 / 禁用 ENB 预设"))\n',
        '        tools_row.addWidget(self._button("🎨 ENB 管理", self.open_enb_manager, "启用 / 更换 / 禁用 ENB 预设"))\n'
        '        tools_row.addWidget(self._button("🧱 PBR / PGPatcher", self.controller.launch_pgpatcher, "检测并通过当前 MO2 VFS 启动 PGPatcher"))\n',
        "user assistant PGPatcher button",
    )
    text = replace_once(
        text,
        '        skse_entry = self._read_skse_executable_entry()\n',
        '        skse_entry = self._read_skse_executable_entry()\n'
        '        pg_status = None\n'
        '        try:\n'
        '            from . import pgpatcher_adapter\n'
        '            pg_status = pgpatcher_adapter.inspect(self.organizer)\n'
        '        except Exception:\n'
        '            pg_status = None\n',
        "user assistant PGPatcher health inspect",
    )
    report_anchor = '        for required in ["modlist.txt", "plugins.txt", "loadorder.txt"]:\n'
    report_block = '''        if pg_status:\n            if pg_status.get("exe"):\n                ok("找到 PGPatcher：{}".format(pg_status.get("exe")))\n                if pg_status.get("active_outputs"):\n                    warn("PGPatcher 输出 MOD 当前已启用；重新生成前必须先禁用。")\n                if pg_status.get("active_dyndolod"):\n                    warn("DynDOLOD 输出当前已启用；PGPatcher 应在 TexGen / DynDOLOD 之前运行。")\n            else:\n                warn("未找到 PGPatcher.exe；如果整合使用 TruePBR 材质，需要安装并通过 MO2 运行 PGPatcher。")\n\n'''
    text = replace_once(text, report_anchor, report_block + report_anchor, "user assistant PGPatcher report")
    user_path.write_text(text, encoding="utf-8")

    tutorial_path = root / "tutorial_data.py"
    text = tutorial_path.read_text(encoding="utf-8-sig")
    tutorial_anchor = '''        {\n            "name": "Grass / LOD 自动化的基本规则",\n'''
    tutorial_entry = '''        {\n            "name": "PBR / PGPatcher：什么时候必须运行？",\n            "content": (\n                "TruePBR 材质需要 Community Shaders，并需要 PGPatcher 根据当前 MO2 的纹理、模型和插件栈生成适配结果。\\n\\n"\n                "推荐从星黎小助手的【PBR / PGPatcher】入口启动，确保程序运行在当前 MO2 VFS 下。\\n"\n                "重新生成前必须禁用旧的 PGPatcher_Output；生成完成后再启用它。\\n\\n"\n                "推荐顺序：BodySlide → PGPatcher → TexGen → DynDOLOD。\\n"\n                "更换大型 PBR / Parallax / Mesh 资源后，应重新运行 PGPatcher，并重新生成后续 TexGen / DynDOLOD 输出。"\n            ),\n        },\n'''
    text = replace_once(text, tutorial_anchor, tutorial_entry + tutorial_anchor, "PGPatcher tutorial")
    tutorial_path.write_text(text, encoding="utf-8")

    shutil.copy2(str(source_dir / "pgpatcher_adapter.py"), str(root / "pgpatcher_adapter.py"))
    shutil.copy2(str(source_dir / "CHANGELOG_v2.2.0.md"), str(root / "CHANGELOG_v2.2.0.md"))
    print("Applied Xingli Assistant 2.2.0 PGPatcher/TruePBR support")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
