# coding: utf-8
"""Qt6 offscreen integration checks for the actual notification popup/UI."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6 import QtCore, QtGui, QtWidgets

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).parent
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

package = types.ModuleType("xingli_ui_test_package")
package.__path__ = [str(WORK)]
sys.modules[package.__name__] = package

core_spec = importlib.util.spec_from_file_location(
    package.__name__ + ".announcement_core", WORK / "announcement_core.py"
)
core = importlib.util.module_from_spec(core_spec)
sys.modules[core_spec.name] = core
core_spec.loader.exec_module(core)

stability = types.ModuleType(package.__name__ + ".stability")
stability.get_logger = lambda name: __import__("logging").getLogger(name)
sys.modules[stability.__name__] = stability

ui_spec = importlib.util.spec_from_file_location(
    package.__name__ + ".announcement_ui", WORK / "announcement_ui.py"
)
ui = importlib.util.module_from_spec(ui_spec)
sys.modules[ui_spec.name] = ui
ui_spec.loader.exec_module(ui)


class Organizer:
    def __init__(self, root):
        self.root = root
    def pluginDataPath(self):
        return str(self.root)


class Controller:
    def __init__(self, root):
        self.organizer = Organizer(root)
        self.window = None
    def _parentWidget(self):
        return None


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.controller = Controller(Path(self.temp.name) / "plugin-data")
        manifest = json.loads((ROOT / "announcements/latest.json").read_text(encoding="utf-8"))
        self.item = core.validate_manifest(manifest)[0]

    def test_cloud_page_uses_external_browser_without_adding_mods(self):
        with mock.patch.object(QtGui.QDesktopServices, "openUrl", return_value=True) as opened:
            with mock.patch.object(QtWidgets.QMessageBox, "information", return_value=None):
                self.assertTrue(ui._open_page(self.controller, self.item, self.item["links"][1]))
        self.assertEqual(
            opened.call_args.args[0].toString(),
            "https://pan.baidu.com/s/18Vtwns7Kj8JpWU_vELsbxw?pwd=p58j",
        )
        self.assertEqual(
            core.read_receipts(core.receipt_path(self.controller.organizer))[self.item["id"]], 1
        )

    def test_popup_later_keeps_announcement_unread(self):
        def choose_later(box):
            target = next(btn for btn in box.buttons() if "稍后提醒" in btn.text())
            target.click()
        with mock.patch.object(ui, "_exec", side_effect=choose_later):
            ui._notify(self.controller, [self.item])
        self.assertEqual(core.read_receipts(core.receipt_path(self.controller.organizer)), {})

    def test_popup_dismiss_marks_read(self):
        def dismiss(box):
            target = next(btn for btn in box.buttons() if "不再提醒" in btn.text())
            target.click()
        with mock.patch.object(ui, "_exec", side_effect=dismiss):
            ui._notify(self.controller, [self.item])
        self.assertEqual(core.unseen(
            [self.item], core.read_receipts(core.receipt_path(self.controller.organizer))
        ), [])

    def test_announcement_center_lists_both_cloud_options(self):
        with mock.patch.object(QtCore.QTimer, "singleShot", return_value=None):
            center = ui.AnnouncementCenter(self.controller)
        center.items = [self.item]
        center.list_widget.addItem(self.item["title"])
        center.list_widget.setCurrentRow(0)
        self.assertTrue(center.link_buttons[0].isEnabled())
        self.assertTrue(center.link_buttons[1].isEnabled())
        self.assertIn("夸克", center.link_buttons[0].text())
        self.assertIn("百度", center.link_buttons[1].text())
        center.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
