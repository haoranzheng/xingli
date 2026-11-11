# coding: utf-8
"""Diagnostic: inspect actual release ZIP for top-level Qt window ownership."""
from pathlib import Path
import re
import sys
import zipfile
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
zip_path = Path("update/releases/xingli_Little_assistant_v2.4.9_update.zip")
with zipfile.ZipFile(zip_path) as z:
    for name in z.namelist():
        if not name.endswith(".py"):
            continue
        lines = z.read(name).decode("utf-8-sig").splitlines()
        hits = []
        probes = (
            "QDialog(", "QMainWindow(", "QWidget(", "QMessageBox(",
            "def _parent(", "def _parentWidget(", "def show_center(",
            "def show(", ".show()", ".exec()", ".exec_()",
            "raise_()", "activateWindow()", "setWindowFlags(", "setModal(",
            "setWindowModality(", "WindowStaysOnTopHint", "self.window =",
            "self._window =", "setParent(", "setVisible("
        )
        for i, line in enumerate(lines):
            if any(probe in line for probe in probes):
                hits.append(i)
        if not hits:
            continue
        if not any(phrase in name for phrase in (
            "controller", "announcement", "profile_download", "nexus_repair",
            "mod_update", "sort_update", "crash_log", "user_assistant",
            "mod_manager", "mod_compressor", "mod_ops_assistant",
            "modpack_updater", "auto_updater", "page_file", "mod_groups",
            "lod_",
        )):
            continue
        print("\n===== MODULE {} / {} lines =====".format(name, len(lines)))
        ranges = []
        for i in hits:
            lo, hi = max(0, i - 5), min(len(lines), i + 9)
            if ranges and lo <= ranges[-1][1]:
                ranges[-1] = (ranges[-1][0], max(ranges[-1][1], hi))
            else:
                ranges.append((lo, hi))
        for lo, hi in ranges:
            print("--- L{}–{} ---".format(lo + 1, hi))
            for i in range(lo, hi):
                print("{:04d}: {}".format(i + 1, lines[i]))
