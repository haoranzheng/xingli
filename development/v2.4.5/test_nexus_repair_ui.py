# coding: utf-8
"""Offscreen real-Qt smoke tests for the repair dialog (run in Qt5 and Qt6)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import importlib
import unittest
from unittest.mock import patch

from test_nexus_repair_service import Mod, Organizer, ServiceTests, PACKAGE

try:
    from PyQt6 import QtCore, QtWidgets
except ImportError:
    from PyQt5 import QtCore, QtWidgets

ui = importlib.import_module(PACKAGE + ".nexus_repair_ui")


class QtPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.mods = [
            Mod("LOD", ServiceTests.one),
            Mod("Tree", ServiceTests.two),
            Mod("Another LOD", "Another LOD 195680 1.0.6 2026-10-19T04-53Z VMSnJrggR.7z"),
        ]
        self.organizer = Organizer(self.mods)

    def _run_with(self, interaction):
        def click():
            modal = QtWidgets.QApplication.activeModalWidget()
            self.assertIsNotNone(modal)
            table = modal.findChild(QtWidgets.QTableWidget, "nexusRepairTable")
            self.assertIsNotNone(table)
            interaction(modal, table)
        QtCore.QTimer.singleShot(0, click)
        with patch.object(QtWidgets.QMessageBox, "information", return_value=None), \
             patch.object(QtWidgets.QMessageBox, "warning", return_value=None):
            return ui.show_preview(None, self.organizer)

    def test_select_all_then_confirm_updates_three(self):
        def interact(modal, table):
            modal.findChild(QtWidgets.QPushButton, "nexusSelectAll").click()
            self.assertTrue(all(table.item(i, 0).checkState() == ui._check(True)
                                for i in range(table.rowCount())))
            modal.accept()
        self.assertEqual(self._run_with(interact), 3)
        self.assertEqual([m.mid for m in self.mods], [87521, 195679, 195680])

    def test_multi_select_non_adjacent_rows(self):
        def interact(modal, table):
            model = table.selectionModel()
            flag = ui._enum(QtCore.QItemSelectionModel, "Select", "SelectionFlag")
            rows = ui._enum(QtCore.QItemSelectionModel, "Rows", "SelectionFlag")
            model.select(table.model().index(0, 0), flag | rows)
            model.select(table.model().index(2, 0), flag | rows)
            modal.findChild(QtWidgets.QPushButton, "nexusCheckSelected").click()
            self.assertEqual([table.item(i, 0).checkState() == ui._check(True)
                              for i in range(3)], [True, False, True])
            modal.accept()
        self.assertEqual(self._run_with(interact), 2)
        self.assertEqual([m.mid for m in self.mods], [87521, 10, 195680])

    def test_select_all_then_clear_does_not_modify(self):
        def interact(modal, table):
            modal.findChild(QtWidgets.QPushButton, "nexusSelectAll").click()
            modal.findChild(QtWidgets.QPushButton, "nexusSelectNone").click()
            modal.accept()
        self.assertEqual(self._run_with(interact), 0)
        self.assertEqual([m.mid for m in self.mods], [10, 10, 10])

    def test_cancel_does_not_write(self):
        def interact(modal, table):
            modal.findChild(QtWidgets.QPushButton, "nexusSelectAll").click()
            modal.reject()
        self.assertEqual(self._run_with(interact), 0)
        self.assertFalse(any(m.writes for m in self.mods))

    def test_exception_is_caught_inside_slot(self):
        class BrokenOrganizer:
            def managedGame(self):
                raise RuntimeError("MO2 unavailable")
            def modList(self):
                raise RuntimeError("MOD list unavailable")
        with patch.object(QtWidgets.QMessageBox, "warning", return_value=None) as warn:
            self.assertEqual(ui.show_preview(None, BrokenOrganizer()), 0)
            self.assertTrue(warn.called)


if __name__ == "__main__":
    unittest.main(verbosity=2)
