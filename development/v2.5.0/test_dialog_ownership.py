# coding: utf-8
"""Qt5/Qt6 offscreen integration for v2.5.0 release ZIP ownership regression."""
import importlib.util
import logging
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PyQt6 import QtCore, QtWidgets
except ImportError:
    from PyQt5 import QtCore, QtWidgets

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "update/releases/xingli_Little_assistant_v2.5.0_update.zip"
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def load_module(package, name, path):
    full = package + "." + name
    spec = importlib.util.spec_from_file_location(full, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


class Controller:
    def __init__(self, root):
        self.mo2 = QtWidgets.QMainWindow()
        self.mo2.setWindowTitle("MO2 root window")
        self.mo2.show()
        self.window = QtWidgets.QDialog()
        self.window.setWindowTitle("星黎 MO2 小助手")
        self.window.show()
        self.organizer = types.SimpleNamespace(
            pluginDataPath=lambda: str(root / "plugin-data"),
            profilePath=lambda: str(root / "profiles" / "Current"),
        )
        APP.processEvents()
    def _parentWidget(self):
        return self.mo2

    def close(self):
        self.window.close()
        self.mo2.close()
        APP.processEvents()


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.work = Path(cls.temp.name)
        with zipfile.ZipFile(PACKAGE) as z:
            assert z.testzip() is None
            z.extractall(cls.work)
        cls.root = cls.work / "xingli_Little_assistant"

        cls.package_name = "xingli_ui_v250_test"
        base = types.ModuleType(cls.package_name)
        base.__path__ = [str(cls.root)]
        sys.modules[cls.package_name] = base

        stability = types.ModuleType(cls.package_name + ".stability")
        stability.get_logger = lambda name: logging.getLogger(name)
        sys.modules[stability.__name__] = stability

        cls.ownership = load_module(
            cls.package_name, "dialog_ownership", cls.root / "dialog_ownership.py"
        )
        for name in ("announcement_core", "profile_download_core"):
            load_module(cls.package_name, name, cls.root / (name + ".py"))
        cls.announcement = load_module(
            cls.package_name, "announcement_ui", cls.root / "announcement_ui.py"
        )
        cls.profile = load_module(
            cls.package_name, "profile_download_ui", cls.root / "profile_download_ui.py"
        )

    @classmethod
    def tearDownClass(cls):
        for name in list(sys.modules):
            if name == cls.package_name or name.startswith(cls.package_name + "."):
                del sys.modules[name]
        cls.temp.cleanup()

    def setUp(self):
        self.controller = Controller(self.work)
        self.addCleanup(self.controller.close)

    def _check_dialog_ownership(self, module, attr):
        with mock.patch.object(QtCore.QTimer, "singleShot", return_value=None):
            module.show_center(self.controller)
        dialog = getattr(self.controller, attr)
        APP.processEvents()
        self.assertIs(dialog.parentWidget(), self.controller.window)
        self.assertTrue(dialog.isModal(), "child must block parent until closed")
        self.assertTrue(dialog.isVisible())
        hint = getattr(QtCore.Qt, "WindowType", QtCore.Qt).WindowStaysOnTopHint
        self.assertFalse(bool(dialog.windowFlags() & hint),
                         "must not force a global always-on-top window")
        dialog.close()
        APP.processEvents()
        self.assertFalse(dialog.isVisible())
        self.assertTrue(self.controller.window.isVisible(),
                        "main dialog must remain open after closing child")
        return dialog

    def test_correct_parent_is_assistant_not_mo2(self):
        owner = self.ownership.owner(self.controller)
        self.assertIs(owner, self.controller.window)
        self.controller.window.hide()
        APP.processEvents()
        self.assertIs(self.ownership.owner(self.controller), self.controller.mo2)

    def test_announcement_center_cannot_fall_behind_main(self):
        self._check_dialog_ownership(
            self.announcement, "_xingli_announcement_center")

    def test_profile_download_center_cannot_fall_behind_main(self):
        self._check_dialog_ownership(
            self.profile, "_xingli_profile_center")

    def test_announcement_startup_popup_uses_assistant_as_parent(self):
        notification = {
            "id": "shader-ownership-test", "revision": 1,
            "title": "测试公告", "message": "更新",
            "links": [{"name": "夸克网盘",
                       "url": "https://pan.quark.cn/s/ff5ece3e4cb7",
                       "code": ""}],
            "published_at": "2026-11-11",
        }
        seen = {}
        def intercept(box):
            seen["parent"] = box.parentWidget()
            button = next(b for b in box.buttons() if "稍后提醒" in b.text())
            button.click()
        with mock.patch.object(self.announcement, "_exec", side_effect=intercept):
            self.announcement._notify(self.controller, [notification])
        self.assertIs(seen["parent"], self.controller.window)

    def test_profile_startup_popup_uses_assistant_as_parent(self):
        record = {"id": "profile", "version": "1.0", "name": "测试档案"}
        seen = {}
        def intercept(box):
            seen["parent"] = box.parentWidget()
            button = next(b for b in box.buttons() if "稍后提醒" in b.text())
            button.click()
        with mock.patch.object(self.profile, "_exec", side_effect=intercept):
            self.profile._notify(self.controller, [record])
        self.assertIs(seen["parent"], self.controller.window)

    def test_legacy_dialogs_and_self_update_prompt_are_also_patched(self):
        with zipfile.ZipFile(PACKAGE) as z:
            controller = z.read("xingli_Little_assistant/consolidation_controller.py").decode("utf-8")
            updater = z.read("xingli_Little_assistant/auto_updater.py").decode("utf-8")
            for name in ("tutorial_window", "enb_window", "resolution_window"):
                self.assertIn(name + " = QtWidgets.QDialog(self.window)", controller)
            self.assertIn("parent = dialog_ownership.owner(controller)", updater)
            for name in ("mod_update_ui.py", "sort_update_ui.py"):
                content = z.read("xingli_Little_assistant/" + name).decode("utf-8")
                self.assertIn("main = dialog_ownership.owner(controller)", content)
                self.assertIn("dialog_ownership.show_owned_dialog(dialog, controller)", content)
            self.assertIn("self.window.exec()", controller)


if __name__ == "__main__":
    unittest.main(verbosity=2)
