# coding=utf-8

import configparser
import shutil
import sys
from pathlib import Path


VERSION = "2.2.0"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError("{}: expected exactly one match, got {}".format(label, count))
    return text.replace(old, new, 1)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply_phase_a.py <plugin-root>")

    root = Path(sys.argv[1]).resolve()
    controller_path = root / "consolidation_controller.py"
    if not controller_path.is_file():
        raise RuntimeError("controller missing: {}".format(controller_path))

    text = controller_path.read_text(encoding="utf-8-sig")
    text = replace_once(
        text,
        "from . import mainmenu_sync\n",
        "from . import mainmenu_sync\nfrom . import instance_identity\nfrom . import onboarding_state\n",
        "controller imports",
    )
    text = replace_once(
        text,
        'DEFAULT_VERSION = "2.1.1"',
        'DEFAULT_VERSION = "{}"'.format(VERSION),
        "default version",
    )
    text = replace_once(
        text,
        'print("version() 返回版本 2.1.1-user")\n        return mobase.VersionInfo(2, 1, 1)',
        'print("version() 返回版本 2.2.0-user")\n        return mobase.VersionInfo(2, 2, 0)',
        "MO2 plugin version",
    )

    old_state_block = '''    def _plugin_data_state_dir(self) -> str:\n        try:\n            base = str(self.organizer.pluginDataPath() or "")\n            if base:\n                path = os.path.join(base, "xingli_assistant")\n                os.makedirs(path, exist_ok=True)\n                return path\n        except Exception:\n            pass\n        path = os.path.join(self._mo2_base_path(), "_XingliState")\n        os.makedirs(path, exist_ok=True)\n        return path\n\n    def _onboarding_state_path(self) -> str:\n        return os.path.join(self._plugin_data_state_dir(), "onboarding.json")\n\n    def _should_show_welcome(self) -> bool:\n        path = self._onboarding_state_path()\n        try:\n            if os.path.isfile(path):\n                with open(path, "r", encoding="utf-8-sig") as handle:\n                    data = json.load(handle)\n                if isinstance(data, dict) and bool(data.get("welcome_seen", False)):\n                    return False\n        except Exception:\n            pass\n        try:\n            with open(path, "w", encoding="utf-8") as handle:\n                json.dump({"schema": 1, "welcome_seen": True}, handle, ensure_ascii=False, indent=2)\n        except Exception:\n            logger.exception("保存新手欢迎状态失败")\n        return True\n'''

    new_state_block = '''    def _modpack_root(self) -> str:\n        """Return the physical modpack root (the parent directory of MO2)."""\n        return os.path.abspath(os.path.join(self._mo2_base_path(), ".."))\n\n    def _onboarding_instance_id(self) -> str:\n        instance_id, method = instance_identity.resolve_instance_identity(self._modpack_root())\n        if method != "win32-file-id":\n            logger.warning("整合实例身份使用回退方式: %s", method)\n        return instance_id\n\n    def _onboarding_state_path(self) -> str:\n        return onboarding_state.state_path(self._onboarding_instance_id())\n\n    def _should_show_welcome(self) -> bool:\n        try:\n            return onboarding_state.should_show_welcome(self._onboarding_instance_id())\n        except Exception:\n            logger.exception("读取新手欢迎状态失败；按首次启动处理")\n            return True\n\n    def _mark_welcome_seen(self) -> None:\n        try:\n            onboarding_state.mark_welcome_seen(self._onboarding_instance_id())\n        except Exception:\n            logger.exception("保存新手欢迎状态失败")\n'''
    text = replace_once(text, old_state_block, new_state_block, "onboarding state block")

    text = replace_once(
        text,
        "        # 显示对话框\n        welcome_dialog.exec()\n",
        "        # 只有欢迎窗口实际显示并结束后才记录完成状态。\n"
        "        # 如果创建/显示过程中异常退出，下次启动仍按首次启动处理。\n"
        "        welcome_dialog.exec()\n"
        "        self._mark_welcome_seen()\n",
        "welcome completion timing",
    )
    controller_path.write_text(text, encoding="utf-8")

    source_dir = Path(__file__).resolve().parent
    for name in ("instance_identity.py", "onboarding_state.py"):
        shutil.copy2(str(source_dir / name), str(root / name))

    version_ini = root / "version.ini"
    config = configparser.ConfigParser()
    if version_ini.is_file():
        config.read(str(version_ini), encoding="utf-8-sig")
    if "Version" not in config:
        config["Version"] = {}
    config["Version"]["local"] = VERSION
    with version_ini.open("w", encoding="utf-8") as handle:
        config.write(handle)

    print("Applied Xingli Assistant {} Phase A transform".format(VERSION))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
