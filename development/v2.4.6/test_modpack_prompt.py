# coding: utf-8
"""Exercise the real v2.4.6 packaged prompt with deterministic Qt message-box clicks."""
import importlib.util
import json
import logging
import os
from pathlib import Path
import tempfile
import types
import unittest
import zipfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "update/releases/xingli_Little_assistant_v2.4.6_update.zip"


class FakeOrganizer:
    def __init__(self, base, plugin_data):
        self.base = str(base)
        self.plugin_data = str(plugin_data)

    def basePath(self):
        return self.base

    def pluginDataPath(self):
        return self.plugin_data


class FakeButton:
    pass


class FakeMessageBox:
    Information = 1
    Warning = 2

    class ButtonRole:
        AcceptRole = 1
        ActionRole = 2
        RejectRole = 3

    sequence = []
    messages = []

    def __init__(self, parent=None):
        self.parent = parent
        self.buttons = []
        self.clicked = None
        self.text = ""
        FakeMessageBox.messages.append(self)

    def setWindowTitle(self, value):
        self.title = value

    def setIcon(self, value):
        self.icon = value

    def setText(self, value):
        self.text = value

    def setInformativeText(self, value):
        self.detail = value

    def addButton(self, *args):
        button = FakeButton()
        self.buttons.append(button)
        return button

    def exec(self):
        index = FakeMessageBox.sequence.pop(0) if FakeMessageBox.sequence else 0
        self.clicked = self.buttons[index] if index < len(self.buttons) else None
        return 0

    def clickedButton(self):
        return self.clicked

    @staticmethod
    def information(*args, **kwargs):
        FakeMessageBox.messages.append(("information", args))

    @staticmethod
    def warning(*args, **kwargs):
        FakeMessageBox.messages.append(("warning", args))


class ModpackPromptIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.extract = self.root / "extracted"
        self.extract.mkdir()
        if not PACKAGE.is_file():
            self.fail("v2.4.6 package missing; run build_release.py before this test")
        with zipfile.ZipFile(PACKAGE) as archive:
            archive.extractall(self.extract)
        self.plugin_dir = self.extract / "xingli_Little_assistant"

        self.base = self.root / "MO2"
        self.base.mkdir()
        self.plugin_data = self.root / "pluginData"
        self.plugin_data.mkdir()
        self.controller = types.SimpleNamespace(
            plugin_path=str(self.plugin_dir),
            organizer=FakeOrganizer(self.base, self.plugin_data),
            window=None,
        )

        self.manifest = {
            "schema": 2,
            "product": "xingli_modpack",
            "enabled": True,
            "pack_id": "xingli-skyrim",
            "name": "星黎整合包",
            "version": "4.0",
            "title": "星黎整合包 4.0",
            "published_at": "2026-11-08T10:51:31Z",
            "changelog": "整合包更新",
            "download_pages": [
                {"name": "夸克网盘", "url": "https://pan.quark.cn/s/example", "code": "mrUd"}
            ],
        }

        package_name = "xingli_prompt_test_{}".format(id(self))
        package = types.ModuleType(package_name)
        package.__path__ = [str(self.plugin_dir)]
        import sys
        sys.modules[package_name] = package
        self.addCleanup(lambda: sys.modules.pop(package_name, None))

        stability = types.ModuleType(package_name + ".stability")
        stability.get_logger = lambda name: logging.getLogger(name)
        mainmenu = types.ModuleType(package_name + ".mainmenu_sync")
        mainmenu.sync = mock.Mock(return_value={})
        sys.modules[package_name + ".stability"] = stability
        sys.modules[package_name + ".mainmenu_sync"] = mainmenu
        self.addCleanup(lambda: sys.modules.pop(package_name + ".stability", None))
        self.addCleanup(lambda: sys.modules.pop(package_name + ".mainmenu_sync", None))

        import PyQt6.QtWidgets as QtWidgets
        self.qt_widgets = QtWidgets
        self.original_message_box = QtWidgets.QMessageBox
        QtWidgets.QMessageBox = FakeMessageBox
        self.addCleanup(self.restore_message_box)

        spec = importlib.util.spec_from_file_location(
            package_name + ".modpack_updater",
            str(self.plugin_dir / "modpack_updater.py"),
        )
        self.updater = importlib.util.module_from_spec(spec)
        sys.modules[package_name + ".modpack_updater"] = self.updater
        self.addCleanup(lambda: sys.modules.pop(package_name + ".modpack_updater", None))
        spec.loader.exec_module(self.updater)
        self.mainmenu = mainmenu
        FakeMessageBox.messages = []
        FakeMessageBox.sequence = []

    def restore_message_box(self):
        self.qt_widgets.QMessageBox = self.original_message_box

    def installed_marker(self):
        path = self.plugin_data / "xingli_assistant" / "modpack" / "installed_release.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def test_confirm_updates_only_current_instance_marker(self):
        FakeMessageBox.sequence = [0, 0]  # first dialog: confirm; second dialog: yes
        result = self.updater.prompt_update(self.controller, self.manifest)
        self.assertTrue(result)
        marker = self.installed_marker()
        self.assertEqual(marker["installed_version"], "4.0")
        self.assertEqual(marker["confirmation_source"], "user_confirmed_installed_release")
        self.assertEqual(
            json.loads((self.plugin_dir / "modpack_release.json").read_text(encoding="utf-8"))["installed_version"],
            "3.3.1",
        )
        self.assertEqual(self.controller._xingli_modpack_update_status["state"], "up_to_date")
        self.mainmenu.sync.assert_called_once()

    def test_later_does_not_change_installed_version(self):
        FakeMessageBox.sequence = [2]
        result = self.updater.prompt_update(self.controller, self.manifest)
        self.assertFalse(result)
        marker = self.installed_marker()
        self.assertEqual(marker["installed_version"], "3.3.1")

    def test_second_confirmation_cancel_does_not_change_version(self):
        FakeMessageBox.sequence = [0, 1]  # first dialog: confirm; verification: cancel
        result = self.updater.prompt_update(self.controller, self.manifest)
        self.assertFalse(result)
        marker = self.installed_marker()
        self.assertEqual(marker["installed_version"], "3.3.1")

    def test_open_download_keeps_existing_download_flow(self):
        FakeMessageBox.sequence = [1]
        with mock.patch.object(self.updater, "_open_download_page", return_value=True) as opened:
            result = self.updater.prompt_update(self.controller, self.manifest)
        self.assertTrue(result)
        opened.assert_called_once()


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ModpackPromptIntegrationTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
