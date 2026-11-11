# -*- coding: utf-8 -*-
"""
XingLi LOD Pipeline v1.9.1 - MO2 2.4.4 / Python 3.8 / PyQt5 oriented.

Purpose:
- Detect when generated landscape / grass / LOD assets may be stale.
- Orchestrate built-in Grass Precaching -> xLODGen -> TexGen -> DynDOLOD.
- Preflight and apply reversible GrassControl.ini distant-grass presets for broad modlist distribution.
- Temporarily toggle helper/debug mods for each stage and restore them afterwards.
- Retry one time with a stage-specific debug-mod policy after a failure.
- Persist Grass Cache checkpoints across Skyrim crashes, MO2 restarts and Windows restarts.
- Monitor launched processes non-blockingly so MO2 remains responsive during precaching.
- Verify outputs/logs and stop the pipeline on an error instead of continuing blindly.

Grass precaching behavior is integrated from GrassPrecacher v3.2.0 (AL, leostevano, GaRRuSS):
- detect NGIO-NG / GrassControl, create PrecacheGrass.txt, launch SKSE through MO2,
  and restart automatically while the marker still exists.

This module deliberately does NOT use IPluginList callbacks. MO2 2.4.4 has known
Python/Boost.Python pain points around plugin-list state notifications.
"""

import configparser
import ctypes
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from ctypes import wintypes

from . import lod_quality
from . import mod_manager
from .mod_state_service import ModStateService
from .mod_groups import ModGroupStore
from .stability import get_logger, log_exception

logger = get_logger("lod_pipeline")

try:
    from PyQt5 import QtCore, QtGui, QtWidgets
    from PyQt5.QtCore import Qt
except ImportError:  # Allows source reuse on MO2 2.5.x, but 2.4.4 is the target.
    from PyQt6 import QtCore, QtGui, QtWidgets
    from PyQt6.QtCore import Qt


STAGES = ("grass", "xlodgen", "texgen", "dyndolod")
STAGE_LABELS = {
    "grass": "Grass Cache 草地缓存",
    "xlodgen": "xLODGen 地形远景",
    "texgen": "TexGen 纹理远景",
    "dyndolod": "DynDOLOD 对象/树木远景",
}

DEFAULT_SUCCESS_MARKERS = {
    "grass": "",
    "xlodgen": "",
    "texgen": "TexGen completed successfully",
    "dyndolod": "DynDOLOD completed successfully",
}

DEFAULT_LOG_PATTERNS = {
    "grass": "",
    "xlodgen": "*LODGen*_log.txt",
    "texgen": "TexGen*_log.txt",
    "dyndolod": "DynDOLOD*_log.txt",
}

# A conservative set. We only treat a line as fatal if one of these expressions is seen
# close to an obvious error prefix. DynDOLOD logs can contain words like "error" in help text.
FATAL_PATTERNS = [
    re.compile(r"(^|\s)(fatal error|unhandled exception|exception:|terminated unexpectedly|failed to generate|error:\s)", re.I),
    re.compile(r"lodgen.*returned error", re.I),
]

GRASS_RESTART_DELAY_MS = 8000
GRASS_MAX_RESTARTS = 240
GRASS_MAX_RUNTIME_SECONDS = 8 * 60 * 60
GRASS_MAX_NO_PROGRESS_RESTARTS = 4

# Non-blocking Windows process monitoring. MO2 2.4.4's Python bridge must not call
# IOrganizer.waitForApplication() from a Python worker thread: it can stall the bridge/UI.
PROCESS_POLL_INTERVAL_MS = 750
GRASS_CHILD_ATTACH_GRACE_SECONDS = 20
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_PATH = 260


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


def _kernel32():
    if os.name != "nt":
        return None
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _handle_value(handle) -> int:
    try:
        return int(handle)
    except (TypeError, ValueError):
        try:
            return int(handle.value)
        except Exception:
            return 0


def _wait_handle_nonblocking(handle) -> Tuple[bool, int, str]:
    """Return (finished, exit_code, error). Never blocks and never touches MO2 APIs."""
    if os.name != "nt":
        return False, -1, "process polling requires Windows"
    hv = _handle_value(handle)
    if hv <= 0 or hv == INVALID_HANDLE_VALUE:
        return True, -1, "invalid process handle"
    k32 = _kernel32()
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    result = int(k32.WaitForSingleObject(wintypes.HANDLE(hv), 0))
    if result == WAIT_TIMEOUT:
        return False, -1, ""
    if result == WAIT_FAILED:
        return True, -1, "WaitForSingleObject failed: {}".format(ctypes.get_last_error())
    code = wintypes.DWORD(0)
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.GetExitCodeProcess.restype = wintypes.BOOL
    if not k32.GetExitCodeProcess(wintypes.HANDLE(hv), ctypes.byref(code)):
        return True, -1, "GetExitCodeProcess failed: {}".format(ctypes.get_last_error())
    return True, int(code.value), ""


def _close_process_handle(handle) -> None:
    if os.name != "nt":
        return
    hv = _handle_value(handle)
    if hv <= 0 or hv == INVALID_HANDLE_VALUE:
        return
    try:
        k32 = _kernel32()
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.CloseHandle(wintypes.HANDLE(hv))
    except Exception:
        logger.exception("CloseHandle failed")


def _get_process_id(handle) -> int:
    if os.name != "nt":
        return 0
    hv = _handle_value(handle)
    if hv <= 0 or hv == INVALID_HANDLE_VALUE:
        return 0
    try:
        k32 = _kernel32()
        k32.GetProcessId.argtypes = [wintypes.HANDLE]
        k32.GetProcessId.restype = wintypes.DWORD
        return int(k32.GetProcessId(wintypes.HANDLE(hv)))
    except Exception:
        return 0


def _snapshot_processes() -> Dict[int, Tuple[int, str]]:
    """Return pid -> (ppid, exe_name_lower) using Toolhelp. Safe to call from QTimer."""
    if os.name != "nt":
        return {}
    k32 = _kernel32()
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32FirstW.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.restype = wintypes.BOOL
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    sv = _handle_value(snap)
    if sv in (0, INVALID_HANDLE_VALUE):
        return {}
    result: Dict[int, Tuple[int, str]] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = bool(k32.Process32FirstW(snap, ctypes.byref(entry)))
        while ok:
            result[int(entry.th32ProcessID)] = (int(entry.th32ParentProcessID), str(entry.szExeFile).casefold())
            ok = bool(k32.Process32NextW(snap, ctypes.byref(entry)))
    finally:
        k32.CloseHandle(snap)
    return result


def _descendant_pids(processes: Dict[int, Tuple[int, str]], root_pid: int) -> Set[int]:
    if root_pid <= 0:
        return set()
    descendants: Set[int] = set()
    frontier = {root_pid}
    for _ in range(16):
        found = {pid for pid, (ppid, _name) in processes.items() if ppid in frontier and pid not in descendants}
        if not found:
            break
        descendants.update(found)
        frontier = found
    return descendants


class GrassFilesystemWorker(QtCore.QObject):
    """Pure filesystem worker. Never touches MO2/mobase objects off the UI thread."""
    finished = QtCore.pyqtSignal(str, object, str)
    progress = QtCore.pyqtSignal(str)

    def __init__(self, operation: str, grass_root: str):
        super().__init__()
        self.operation = operation
        self.grass_root = grass_root

    @QtCore.pyqtSlot()
    def run(self):
        try:
            if self.operation == "clear":
                removed = 0
                if os.path.isdir(self.grass_root):
                    for dirpath, _dirs, files in os.walk(self.grass_root):
                        for fn in files:
                            if not fn.lower().endswith((".cgid", ".gid")):
                                continue
                            try:
                                os.remove(os.path.join(dirpath, fn))
                                removed += 1
                                if removed % 500 == 0:
                                    self.progress.emit("后台已清理 {} 个旧草缓存文件…".format(removed))
                            except OSError:
                                pass
                self.finished.emit(self.operation, {"removed": removed}, "")
                return

            if self.operation == "stats":
                count = 0
                total = 0
                if os.path.isdir(self.grass_root):
                    for dirpath, _dirs, files in os.walk(self.grass_root):
                        for fn in files:
                            if not fn.lower().endswith((".cgid", ".gid")):
                                continue
                            count += 1
                            try:
                                total += int(os.path.getsize(os.path.join(dirpath, fn)))
                            except OSError:
                                pass
                            if count % 2000 == 0:
                                self.progress.emit("后台已统计 {} 个草缓存文件…".format(count))
                self.finished.emit(self.operation, {"count": count, "total": total}, "")
                return

            self.finished.emit(self.operation, {}, "未知文件系统任务：{}".format(self.operation))
        except Exception as e:
            logger.exception("grass filesystem worker failed")
            self.finished.emit(self.operation, {}, str(e))



@dataclass
class StageConfig:
    exe: str = ""
    args: str = ""
    cwd: str = ""
    output_source: str = ""
    output_mod: str = ""
    log_dir: str = ""
    log_pattern: str = ""
    success_marker: str = ""
    enable_mods: str = ""
    disable_mods: str = ""
    retry_enable_mods: str = ""
    retry_disable_mods: str = ""
    enable_groups: str = ""
    disable_groups: str = ""
    retry_enable_groups: str = ""
    retry_disable_groups: str = ""
    auto_retry: bool = True
    copy_output: bool = True
    clear_output_source_after_copy: bool = False


class PipelineStore:
    def __init__(self, organizer, plugin_dir: str):
        self.organizer = organizer
        self.mod_state = ModStateService(organizer)
        self.plugin_dir = plugin_dir
        base = ""
        try:
            base = str(organizer.pluginDataPath())
        except Exception:
            pass
        if not base:
            try:
                base = str(organizer.profilePath())
            except Exception:
                base = plugin_dir
        self.root = os.path.join(base, "xingli_lod_pipeline")
        self.config_path = os.path.join(self.root, "pipeline.ini")
        self.state_path = os.path.join(self.root, "state.json")
        self.snapshot_path = os.path.join(self.root, "snapshot.json")
        self.grass_checkpoint_path = os.path.join(self.root, "grass_checkpoint.json")
        self.logs_path = os.path.join(self.root, "logs")
        os.makedirs(self.logs_path, exist_ok=True)

    def load_config(self):
        cfg = configparser.ConfigParser()
        if os.path.isfile(self.config_path):
            try:
                cfg.read(self.config_path, encoding="utf-8")
            except Exception:
                logger.exception("LOD pipeline config read failed")
        mode = cfg.get("Pipeline", "DetectionMode", fallback="smart").strip().lower()
        if mode not in ("smart", "full"):
            mode = "smart"
        quality_preset = cfg.get("Pipeline", "QualityPreset", fallback="balanced").strip().lower()
        if quality_preset not in ("balanced", "performance", "off"):
            quality_preset = "balanced"
        shader_mode = cfg.get("Pipeline", "ShaderMode", fallback="auto").strip().lower()
        if shader_mode not in ("auto", "community_shaders", "enb", "vanilla"):
            shader_mode = "auto"
        auto_quality = cfg.getboolean("Pipeline", "AutoApplyQuality", fallback=True)
        result = {}
        for stage in STAGES:
            section = "Stage." + stage
            c = StageConfig()
            c.exe = cfg.get(section, "Exe", fallback="")
            c.args = cfg.get(section, "Args", fallback="-sse" if stage in ("xlodgen", "texgen", "dyndolod") else "")
            c.cwd = cfg.get(section, "Cwd", fallback="")
            c.output_source = cfg.get(section, "OutputSource", fallback="")
            c.output_mod = cfg.get(section, "OutputMod", fallback={
                "grass": "Grass Cache Output",
                "xlodgen": "xLODGen Output",
                "texgen": "TexGen Output",
                "dyndolod": "DynDOLOD Output",
            }[stage])
            c.log_dir = cfg.get(section, "LogDir", fallback="")
            c.log_pattern = cfg.get(section, "LogPattern", fallback=DEFAULT_LOG_PATTERNS[stage])
            c.success_marker = cfg.get(section, "SuccessMarker", fallback=DEFAULT_SUCCESS_MARKERS[stage])
            c.enable_mods = cfg.get(section, "EnableMods", fallback="")
            c.disable_mods = cfg.get(section, "DisableMods", fallback="")
            c.retry_enable_mods = cfg.get(section, "RetryEnableMods", fallback="")
            c.retry_disable_mods = cfg.get(section, "RetryDisableMods", fallback="")
            c.enable_groups = cfg.get(section, "EnableGroups", fallback="")
            c.disable_groups = cfg.get(section, "DisableGroups", fallback="")
            c.retry_enable_groups = cfg.get(section, "RetryEnableGroups", fallback="")
            c.retry_disable_groups = cfg.get(section, "RetryDisableGroups", fallback="")
            c.auto_retry = cfg.getboolean(section, "AutoRetry", fallback=True)
            c.copy_output = cfg.getboolean(section, "CopyOutput", fallback=True)
            c.clear_output_source_after_copy = cfg.getboolean(section, "ClearOutputSourceAfterCopy", fallback=False)
            if stage == "grass":
                # v1.5+: GrassPrecacher is built in. Keep only output MOD name and optional MOD policies.
                c.exe = ""; c.args = ""; c.cwd = ""; c.output_source = ""
                c.log_dir = ""; c.log_pattern = ""; c.success_marker = ""
                c.copy_output = True; c.clear_output_source_after_copy = False
            result[stage] = c
        return mode, quality_preset, shader_mode, auto_quality, result

    def save_config(self, mode: str, stages: Dict[str, StageConfig], quality_preset: str = "balanced",
                    shader_mode: str = "auto", auto_quality: bool = True) -> None:
        os.makedirs(self.root, exist_ok=True)
        cfg = configparser.ConfigParser()
        cfg["Pipeline"] = {
            "DetectionMode": mode,
            "QualityPreset": quality_preset,
            "ShaderMode": shader_mode,
            "AutoApplyQuality": "1" if auto_quality else "0",
        }
        for stage in STAGES:
            c = stages[stage]
            cfg["Stage." + stage] = {
                "Exe": c.exe,
                "Args": c.args,
                "Cwd": c.cwd,
                "OutputSource": c.output_source,
                "OutputMod": c.output_mod,
                "LogDir": c.log_dir,
                "LogPattern": c.log_pattern,
                "SuccessMarker": c.success_marker,
                "EnableMods": c.enable_mods,
                "DisableMods": c.disable_mods,
                "RetryEnableMods": c.retry_enable_mods,
                "RetryDisableMods": c.retry_disable_mods,
                "EnableGroups": c.enable_groups,
                "DisableGroups": c.disable_groups,
                "RetryEnableGroups": c.retry_enable_groups,
                "RetryDisableGroups": c.retry_disable_groups,
                "AutoRetry": "1" if c.auto_retry else "0",
                "CopyOutput": "1" if c.copy_output else "0",
                "ClearOutputSourceAfterCopy": "1" if c.clear_output_source_after_copy else "0",
            }
        with open(self.config_path, "w", encoding="utf-8") as f:
            cfg.write(f)

    def load_json(self, path: str, default):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    def save_json(self, path: str, data) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


class SnapshotAnalyzer:
    """Fast, low-risk invalidation detector.

    It intentionally does not hash every texture byte. MO2 mod installations normally change
    directory metadata, and profile/load-order files are hashed exactly. The UI always offers
    a conservative full rebuild when the user wants certainty.
    """

    TEXTURE_DIRS = (
        "textures/landscape", "textures/terrain", "textures/architecture",
        "textures/clutter", "textures/plants", "textures/trees",
    )
    GRASS_DIRS = (
        "meshes/landscape/grass", "grass", "net script framework/plugins",
        "skse/plugins",
    )
    TERRAIN_DIRS = (
        "textures/landscape", "textures/terrain", "meshes/terrain", "lodsettings",
    )
    MODEL_DIRS = (
        "meshes/landscape", "meshes/architecture", "meshes/trees", "meshes/plants",
    )

    def __init__(self, organizer):
        self.organizer = organizer
        self.mod_state = ModStateService(organizer)

    @staticmethod
    def _digest(parts: List[str]) -> str:
        h = hashlib.sha256()
        for p in parts:
            h.update(p.encode("utf-8", errors="replace"))
            h.update(b"\0")
        return h.hexdigest()

    @staticmethod
    def _path_signature(path: str) -> str:
        if not path or not os.path.exists(path):
            return "missing"
        pieces = []
        try:
            st = os.stat(path)
            pieces.append("{}:{}".format(int(st.st_mtime), int(st.st_size)))
        except OSError:
            return "unreadable"
        if os.path.isdir(path):
            try:
                # One level is enough to catch the normal MO2 replace/install workflow and is
                # dramatically cheaper than walking millions of texture files.
                for entry in sorted(os.scandir(path), key=lambda e: e.name.casefold()):
                    try:
                        est = entry.stat(follow_symlinks=False)
                        pieces.append("{}:{}:{}".format(entry.name.casefold(), int(est.st_mtime), int(est.st_size)))
                    except OSError:
                        continue
            except OSError:
                pass
        return SnapshotAnalyzer._digest(pieces)

    def _profile_file_hash(self, filename: str) -> str:
        try:
            path = os.path.join(str(self.organizer.profilePath()), filename)
        except Exception:
            return ""
        if not os.path.isfile(path):
            return ""
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                while True:
                    data = f.read(1024 * 256)
                    if not data:
                        break
                    h.update(data)
            return h.hexdigest()
        except OSError:
            return ""

    def _active_mod_rows(self) -> List[Tuple[str, str, int]]:
        rows = []
        for record in self.mod_state.active_records():
            if record.internal_name.lower().endswith("_separator"):
                continue
            rows.append((record.internal_name, record.path, record.priority))
        rows.sort(key=lambda x: (x[2], x[0].casefold()))
        return rows

    def build(self) -> dict:
        active_rows = self._active_mod_rows()
        active_parts = ["{}|{}".format(name, prio) for name, _path, prio in active_rows]
        texture_parts, grass_parts, terrain_parts, model_parts = [], [], [], []

        for name, root, prio in active_rows:
            base = "{}|{}".format(name, prio)
            for rel in self.TEXTURE_DIRS:
                p = os.path.join(root, *rel.split("/")) if root else ""
                if p and os.path.exists(p):
                    texture_parts.append(base + "|" + rel + "|" + self._path_signature(p))
            for rel in self.GRASS_DIRS:
                p = os.path.join(root, *rel.split("/")) if root else ""
                if p and os.path.exists(p):
                    grass_parts.append(base + "|" + rel + "|" + self._path_signature(p))
            for rel in self.TERRAIN_DIRS:
                p = os.path.join(root, *rel.split("/")) if root else ""
                if p and os.path.exists(p):
                    terrain_parts.append(base + "|" + rel + "|" + self._path_signature(p))
            for rel in self.MODEL_DIRS:
                p = os.path.join(root, *rel.split("/")) if root else ""
                if p and os.path.exists(p):
                    model_parts.append(base + "|" + rel + "|" + self._path_signature(p))

        return {
            "created_at": time.time(),
            "active_mods": self._digest(active_parts),
            "plugins_txt": self._profile_file_hash("plugins.txt"),
            "loadorder_txt": self._profile_file_hash("loadorder.txt"),
            "textures": self._digest(texture_parts),
            "grass": self._digest(grass_parts),
            "terrain": self._digest(terrain_parts),
            "models": self._digest(model_parts),
            "active_mod_count": len(active_rows),
        }

    @staticmethod
    def required_stages(old: dict, new: dict, mode: str) -> Tuple[Set[str], List[str]]:
        if not old:
            return set(STAGES), ["没有历史基线，首次运行建议完整生成"]
        changed = []
        for key in ("active_mods", "plugins_txt", "loadorder_txt", "textures", "grass", "terrain", "models"):
            if old.get(key) != new.get(key):
                changed.append(key)
        if not changed:
            return set(), ["未检测到会影响生成结果的变化"]
        if mode == "full":
            return set(STAGES), ["保守全重建模式：检测到变化 " + ", ".join(changed)]

        required: Set[str] = set()
        reasons = []
        # A load-order or enabled-mod set change is conservatively treated as affecting every
        # generated artifact because plugin records can alter landscape/grass/worldspaces.
        if any(k in changed for k in ("active_mods", "plugins_txt", "loadorder_txt")):
            required.update(STAGES)
            reasons.append("启用 MOD / 插件加载顺序发生变化：完整重建")
        else:
            if "grass" in changed:
                required.update(("grass", "texgen", "dyndolod"))
                reasons.append("草地模型/配置相关目录变化：Grass Cache + TexGen + DynDOLOD")
            if "terrain" in changed:
                required.update(("xlodgen", "texgen", "dyndolod"))
                reasons.append("地形资源变化：xLODGen + TexGen + DynDOLOD")
            if "models" in changed:
                required.update(("texgen", "dyndolod"))
                reasons.append("远景相关模型目录变化：TexGen + DynDOLOD")
            if "textures" in changed:
                required.update(("texgen", "dyndolod"))
                reasons.append("材质变化：TexGen + DynDOLOD；不强制重建 Grass Cache")
        return required, reasons


class PipelineDialog(QtWidgets.QDialog):
    def __init__(self, parent, organizer, plugin_dir: str):
        super().__init__(parent)
        self.organizer = organizer
        self.plugin_dir = plugin_dir
        self.store = PipelineStore(organizer, plugin_dir)
        # v1.8.4: PipelineDialog is the runtime owner of all stage/checkpoint MOD
        # operations. Reuse the store's single ModStateService instance so every
        # grass checkpoint, stage policy and restore path shares the same MO2 2.4.4
        # compatible state semantics. v1.8.2/1.8.3 forgot this binding, which made
        # checkpoint discard/restore raise AttributeError before any MOD state could
        # be restored.
        self.mod_state = self.store.mod_state
        self.mod_groups = ModGroupStore(organizer)
        self.detection_mode, self.quality_preset, self.shader_mode, self.auto_quality, self.configs = self.store.load_config()
        self.snapshot = SnapshotAnalyzer(organizer)
        self.required: Set[str] = set()
        self.pending: List[str] = []
        self.current_stage: Optional[str] = None
        self.current_attempt = 0
        self.saved_mod_states: Dict[str, bool] = {}
        # Two-level MOD-state safety model:
        # - saved_mod_states: exact state before the current stage policy;
        # - pipeline_original_mod_states: first-seen state before the whole pipeline.
        # The latter is the final drift guard that restores any temporary cross-stage
        # output/helper state after success/failure. Newly created output MODs are
        # intentionally excluded because they had no pre-run state to restore.
        self.pipeline_original_mod_states: Dict[str, bool] = {}
        self._pipeline_new_output_mods: Set[str] = set()
        self._pipeline_created_output_mods: Set[str] = set()
        self._stage_output_existed_before = True
        self._active_process = None
        self._process_timer = QtCore.QTimer(self)
        self._process_timer.setInterval(PROCESS_POLL_INTERVAL_MS)
        self._process_timer.timeout.connect(self._poll_active_process)
        self.pipeline_running = False
        self.grass_restart_count = 0
        self.grass_started_at = 0.0
        self.grass_abort_requested = False
        self.grass_restart_timer = None
        self.grass_resume_mode = False
        self.grass_checkpoint = {}
        self._pipeline_start_snapshot = {}
        self._grass_fs_thread = None
        self._grass_fs_worker = None
        self._grass_fs_context = None
        self.stage_rows = {}
        self.setWindowTitle("星黎 LOD 自动化")
        self.resize(920, 700)
        self._build_ui()
        self.quality_manager = lod_quality.LODQualityManager(organizer, self.store.root, self._append)
        self.analyze_changes()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("🌲 LOD 自动生成")
        font = title.font(); font.setBold(True); font.setPointSize(13); title.setFont(font)
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        note = QtWidgets.QLabel(
            "星黎会根据整合包变化自动判断需要更新的阶段，并按顺序运行草缓存、xLODGen、TexGen 和 DynDOLOD。"
            "生成前可应用推荐画质与远景草地设置，完成后自动整理输出。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("QLabel{background:#fff4ce;border:1px solid #e3c15b;padding:8px;border-radius:5px;}")
        layout.addWidget(note)

        top = QtWidgets.QHBoxLayout()
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItem("按变化生成（推荐）", "smart")
        self.mode_combo.addItem("有变化时全部重新生成", "full")
        idx = self.mode_combo.findData(self.detection_mode)
        if idx >= 0: self.mode_combo.setCurrentIndex(idx)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        top.addWidget(QtWidgets.QLabel("重建方式:"))
        top.addWidget(self.mode_combo)
        top.addStretch()
        self.btn_analyze = QtWidgets.QPushButton("重新分析")
        self.btn_settings = QtWidgets.QPushButton("生成设置")
        self.btn_open_logs = QtWidgets.QPushButton("打开日志")
        self.btn_analyze.clicked.connect(self.analyze_changes)
        self.btn_settings.clicked.connect(self.open_settings)
        self.btn_open_logs.clicked.connect(lambda: os.startfile(self.store.logs_path) if os.name == "nt" else None)
        top.addWidget(self.btn_analyze); top.addWidget(self.btn_settings); top.addWidget(self.btn_open_logs)
        layout.addLayout(top)

        quality_row = QtWidgets.QHBoxLayout()
        self.chk_auto_quality = QtWidgets.QCheckBox("生成前应用推荐画质和远景草地设置")
        self.chk_auto_quality.setChecked(self.auto_quality)
        self.chk_auto_quality.toggled.connect(self._quality_changed)
        quality_row.addWidget(self.chk_auto_quality)
        quality_row.addWidget(QtWidgets.QLabel("预设:"))
        self.quality_combo = QtWidgets.QComboBox()
        self.quality_combo.addItem("均衡画质（推荐）", "balanced")
        self.quality_combo.addItem("性能优先", "performance")
        self.quality_combo.addItem("保持当前设置", "off")
        qidx = self.quality_combo.findData(self.quality_preset)
        if qidx >= 0: self.quality_combo.setCurrentIndex(qidx)
        self.quality_combo.currentIndexChanged.connect(self._quality_changed)
        quality_row.addWidget(self.quality_combo)
        quality_row.addWidget(QtWidgets.QLabel("图形方案:"))
        self.shader_combo = QtWidgets.QComboBox()
        self.shader_combo.addItem("自动检测", "auto")
        self.shader_combo.addItem("Community Shaders", "community_shaders")
        self.shader_combo.addItem("ENB", "enb")
        self.shader_combo.addItem("原版 / 其它", "vanilla")
        sidx = self.shader_combo.findData(self.shader_mode)
        if sidx >= 0: self.shader_combo.setCurrentIndex(sidx)
        self.shader_combo.currentIndexChanged.connect(self._quality_changed)
        quality_row.addWidget(self.shader_combo)
        self.btn_check_grass = QtWidgets.QPushButton("检查草地设置")
        self.btn_check_grass.clicked.connect(self._inspect_grass_settings)
        quality_row.addWidget(self.btn_check_grass)
        self.btn_restore_quality = QtWidgets.QPushButton("恢复原设置")
        self.btn_restore_quality.clicked.connect(self._restore_quality_settings)
        quality_row.addWidget(self.btn_restore_quality)
        layout.addLayout(quality_row)

        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.table = QtWidgets.QTableWidget(len(STAGES), 5)
        self.table.setHorizontalHeaderLabels(["阶段", "是否需要", "配置", "输出", "状态"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        for row, stage in enumerate(STAGES):
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(STAGE_LABELS[stage]))
            for col in range(1, 5): self.table.setItem(row, col, QtWidgets.QTableWidgetItem(""))
            self.stage_rows[stage] = row
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)

        actions = QtWidgets.QHBoxLayout()
        self.btn_run_needed = QtWidgets.QPushButton("▶ 运行需要更新的阶段")
        self.btn_run_full = QtWidgets.QPushButton("▶ 全部重新生成")
        self.btn_resume_grass = QtWidgets.QPushButton("▶ 继续草缓存")
        self.btn_resume_grass.setEnabled(False)
        self.btn_abort_grass = QtWidgets.QPushButton("Ⅱ 暂停草缓存")
        self.btn_abort_grass.setEnabled(False)
        self.btn_discard_grass = QtWidgets.QPushButton("清除草缓存进度")
        self.btn_discard_grass.setEnabled(False)
        self.btn_mark_clean = QtWidgets.QPushButton("将当前状态作为基准")
        self.btn_run_needed.clicked.connect(self.run_needed)
        self.btn_run_full.clicked.connect(lambda: self.start_pipeline(list(STAGES)))
        self.btn_resume_grass.clicked.connect(self._resume_grass_checkpoint)
        self.btn_abort_grass.clicked.connect(self._abort_grass_precache)
        self.btn_discard_grass.clicked.connect(self._discard_grass_checkpoint)
        self.btn_mark_clean.clicked.connect(self.mark_baseline)
        actions.addWidget(self.btn_run_needed); actions.addWidget(self.btn_run_full); actions.addWidget(self.btn_resume_grass); actions.addWidget(self.btn_abort_grass); actions.addWidget(self.btn_discard_grass); actions.addWidget(self.btn_mark_clean)
        layout.addLayout(actions)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        layout.addWidget(self.log, 1)
        self._append("草缓存支持断点续跑。异常退出后，只要整合包环境没有变化，就可以从已有进度继续。")

    def _mode_changed(self):
        self.detection_mode = str(self.mode_combo.currentData())
        self.store.save_config(self.detection_mode, self.configs, self.quality_preset, self.shader_mode, self.auto_quality)
        self.analyze_changes()

    def _quality_changed(self):
        if not hasattr(self, "quality_combo"):
            return
        self.quality_preset = str(self.quality_combo.currentData())
        self.shader_mode = str(self.shader_combo.currentData())
        self.auto_quality = bool(self.chk_auto_quality.isChecked())
        self.store.save_config(self.detection_mode, self.configs, self.quality_preset, self.shader_mode, self.auto_quality)
        self.analyze_changes()

    def _inspect_grass_settings(self):
        if self.pipeline_running:
            return
        ok, msg = self.quality_manager.inspect_grass_control()
        msg += "\n\n星黎当前草地预设：{}".format(
            lod_quality.GRASS_PRESET_SUMMARY.get(self.quality_preset if self.auto_quality else "off", "off"))
        self._append(("✅ " if ok else "⚠ ") + msg.replace("\n", " | "))
        box = QtWidgets.QMessageBox.information if ok else QtWidgets.QMessageBox.warning
        box(self, "GrassControl.ini 检查", msg)

    def _restore_quality_settings(self):
        if self.pipeline_running:
            return
        ok, msg = self.quality_manager.restore_latest()
        self._append(("✅ " if ok else "⚠ ") + msg)
        if ok:
            QtWidgets.QMessageBox.information(self, "已恢复工具设置", msg)
        else:
            QtWidgets.QMessageBox.warning(self, "无法恢复", msg)

    def _append(self, text: str):
        stamp = time.strftime("%H:%M:%S")
        self.log.appendPlainText("[{}] {}".format(stamp, text))
        try:
            with open(os.path.join(self.store.logs_path, "pipeline.log"), "a", encoding="utf-8") as f:
                f.write("[{}] {}\n".format(time.strftime("%Y-%m-%d %H:%M:%S"), text))
        except Exception:
            pass

    def analyze_changes(self):
        self._append("正在分析当前 MO2 环境...")
        old = self.store.load_json(self.store.snapshot_path, {})
        new = self.snapshot.build()
        required, reasons = SnapshotAnalyzer.required_stages(old, new, self.detection_mode)
        self.required = required
        self._latest_snapshot = new
        if required:
            text = "检测结果：需要生成 {}。\n{}".format(" → ".join(STAGE_LABELS[s] for s in STAGES if s in required), "\n".join("• " + r for r in reasons))
        else:
            text = "检测结果：当前生成资产没有检测到失效。\n" + "\n".join("• " + r for r in reasons)
        effective_shader = self.shader_mode
        if hasattr(self, "quality_manager") and self.shader_mode == "auto":
            effective_shader = self.quality_manager.detect_shader()
        if self.auto_quality and self.quality_preset != "off":
            text += "\n• 画质护栏：{}；着色器：{}".format(
                lod_quality.QUALITY_LABELS.get(self.quality_preset, self.quality_preset),
                lod_quality.SHADER_LABELS.get(effective_shader, effective_shader))
            text += "\n• 远景草地：{}".format(lod_quality.GRASS_PRESET_SUMMARY.get(self.quality_preset, self.quality_preset))
        else:
            text += "\n• 画质护栏：关闭；草缓存仍会自动修复 Use-grass-cache / Only-load-from-cache 必需项"
        checkpoint = self.store.load_json(self.store.grass_checkpoint_path, {})
        if self._grass_checkpoint_is_resumable(checkpoint):
            count = int(checkpoint.get("cache_file_count", 0) or 0)
            status = checkpoint.get("status", "paused")
            text += "\n• 检测到草缓存断点：状态 {}，已记录 {} 个缓存文件，可使用【继续草缓存断点】。".format(status, count)
        self.summary.setText(text)
        for stage in STAGES:
            row = self.stage_rows[stage]
            c = self.configs[stage]
            self.table.item(row, 1).setText("是" if stage in required else "否")
            self.table.item(row, 2).setText("内置（无需配置）" if stage == "grass" else ("已配置" if c.exe and os.path.isfile(c.exe) else "未配置"))
            self.table.item(row, 3).setText(c.output_mod or "—")
            if not self.table.item(row, 4).text(): self.table.item(row, 4).setText("待机")
        self.btn_run_needed.setEnabled(bool(required) and not self.pipeline_running)
        if hasattr(self, "btn_resume_grass"):
            resumable = self._grass_checkpoint_is_resumable(checkpoint)
            self.btn_resume_grass.setEnabled(resumable and not self.pipeline_running)
            self.btn_discard_grass.setEnabled(resumable and not self.pipeline_running)
            if resumable:
                self.table.item(self.stage_rows["grass"], 4).setText("有断点（{} 文件）".format(int(checkpoint.get("cache_file_count", 0) or 0)))

    def mark_baseline(self):
        snap = getattr(self, "_latest_snapshot", None) or self.snapshot.build()
        self.store.save_json(self.store.snapshot_path, snap)
        self._append("已将当前 MO2 环境记录为新的生成基线。")
        self.analyze_changes()

    def run_needed(self):
        stages = [s for s in STAGES if s in self.required]
        self.start_pipeline(stages)

    def start_pipeline(self, stages: List[str]):
        if self.pipeline_running:
            return
        missing = [STAGE_LABELS[s] for s in stages if s != "grass" and (not self.configs[s].exe or not os.path.isfile(self.configs[s].exe))]
        if missing:
            QtWidgets.QMessageBox.warning(self, "工具未配置", "以下阶段缺少有效 EXE 路径：\n\n" + "\n".join(missing) + "\n\n请先打开【流水线设置】。")
            return
        # Reuse the snapshot already calculated by Analyze. Do not rescan hundreds of
        # mods again in the launch hot path; on large modlists that can look like a hang.
        self._pipeline_start_snapshot = dict(getattr(self, "_latest_snapshot", {}) or {})
        if not self._pipeline_start_snapshot:
            self._pipeline_start_snapshot = self.snapshot.build()
        self.pipeline_running = True
        self.grass_abort_requested = False
        # Fresh run baseline. A resumable Grass checkpoint will repopulate this
        # from checkpoint.original_mod_states before re-applying its policy.
        self.pipeline_original_mod_states = {}
        self._pipeline_new_output_mods = set()
        self._pipeline_created_output_mods = set()
        self.pending = list(stages)
        self._set_controls(False)
        self._append("开始流水线：" + " → ".join(STAGE_LABELS[s] for s in self.pending))
        self._run_next()

    def _set_controls(self, enabled: bool):
        self.btn_run_needed.setEnabled(enabled and bool(self.required))
        self.btn_run_full.setEnabled(enabled)
        self.btn_settings.setEnabled(enabled)
        self.btn_mark_clean.setEnabled(enabled)
        self.btn_analyze.setEnabled(enabled)
        self.mode_combo.setEnabled(enabled)
        self.chk_auto_quality.setEnabled(enabled)
        self.quality_combo.setEnabled(enabled)
        self.shader_combo.setEnabled(enabled)
        self.btn_restore_quality.setEnabled(enabled)
        self.btn_abort_grass.setEnabled((not enabled) and self.current_stage == "grass")
        if hasattr(self, "btn_resume_grass"):
            resumable = self._grass_checkpoint_is_resumable(self.store.load_json(self.store.grass_checkpoint_path, {}))
            self.btn_resume_grass.setEnabled(enabled and resumable)
            self.btn_discard_grass.setEnabled(enabled and resumable)

    def _run_next(self):
        if not self.pending:
            self._restore_pipeline_mod_states("流水线全部完成")
            self.pipeline_running = False
            self._set_controls(True)
            self.store.save_json(self.store.snapshot_path, self.snapshot.build())
            self._append("✅ 流水线全部完成，已更新环境基线。")
            QtWidgets.QMessageBox.information(self, "LOD 自动化", "所选生成阶段均已完成。\n当前状态已保存为新的比较基准。")
            self.analyze_changes()
            return
        self.current_stage = self.pending.pop(0)
        self.current_attempt = 1
        self._launch_current_stage(retry=False)

    @staticmethod
    def _split_mods(text: str) -> List[str]:
        return [x.strip() for x in re.split(r"[;,\n]+", text or "") if x.strip()]

    def _mod_active(self, name: str) -> bool:
        return self.mod_state.is_active(name, default=False)

    def _set_mods(self, names: List[str], active: bool):
        if not names:
            return
        unique = []
        seen = set()
        for raw in names:
            name = str(raw)
            if name and name not in seen:
                seen.add(name)
                unique.append(name)
        to_change = []
        for name in unique:
            current = self.mod_state.is_active(name, default=False)
            if name not in self.saved_mod_states:
                self.saved_mod_states[name] = current
            # Capture the first state seen across the whole pipeline. Do not create
            # a fake "disabled" baseline for an output MOD that did not exist yet.
            if name not in self._pipeline_new_output_mods and name not in self.pipeline_original_mod_states:
                self.pipeline_original_mod_states[name] = current
            if current != bool(active):
                to_change.append(name)
        if not to_change:
            return
        self._append("MO2 VFS：{} {} 个实际需要变更的 MOD…".format("启用" if active else "禁用", len(to_change)))
        result = self.mod_state.set_active(to_change, active)
        for name in result.failures:
            self._append("警告：无法{} MOD：{}".format("启用" if active else "禁用", name))

    def _restore_state_map_exact(self, states: Dict[str, bool], label: str) -> List[str]:
        """Restore a state map, retrying any failed batch items individually.

        MO2 2.4.4 can expose slightly different QString/QStringList behavior across
        installations. A verified batch restore is kept for performance, while failed
        items are retried through the stable scalar setActive(name, bool) path.
        """
        if not states:
            return []
        normalized = {str(name): bool(active) for name, active in states.items() if str(name)}
        if not normalized:
            return []
        result = self.mod_state.restore_states(normalized, verify=True)
        failures = []
        for name in result.failures:
            if name not in normalized:
                continue
            retry = self.mod_state.set_active([name], normalized[name], verify=True)
            if retry.failures:
                failures.append(name)
        if failures:
            for name in failures:
                self._append("警告：{}失败：{}".format(label, name))
        return failures

    def _restore_mod_policy(self, keep_output_active: bool = False):
        if self.saved_mod_states:
            self._restore_state_map_exact(self.saved_mod_states, "恢复阶段 MOD 状态")
        # Successful generated output stays available for downstream stages. The
        # pipeline-level final restore below will return pre-existing output MODs to
        # their exact pre-run state. A truly new output MOD remains enabled.
        if keep_output_active and self.current_stage:
            out = self.configs[self.current_stage].output_mod.strip()
            if out:
                self.mod_state.set_active([out], True, verify=True)
        self.saved_mod_states = {}

    def _restore_pipeline_mod_states(self, reason: str = "流水线结束") -> None:
        """Final exact-state guard for every pre-existing MOD touched by the run."""
        states = dict(self.pipeline_original_mod_states or {})
        if states:
            failures = self._restore_state_map_exact(states, "恢复流水线前 MOD 状态")
            if not failures:
                self._append("✅ {}：已恢复 {} 个受影响 MOD 的运行前状态。".format(reason, len(states)))
        # Output MODs created by this run had no previous state. Keep them enabled so
        # the generated assets are immediately usable without violating old state.
        for name in sorted(self._pipeline_created_output_mods):
            if name in self._pipeline_new_output_mods:
                self.mod_state.set_active([name], True, verify=True)
        self.pipeline_original_mod_states = {}
        self._pipeline_new_output_mods = set()
        self._pipeline_created_output_mods = set()

    def _expand_policy_groups(self, text: str, label: str) -> List[str]:
        groups = self._split_mods(text)
        if not groups:
            return []
        expansion = self.mod_groups.expand(groups)
        if expansion.missing_groups:
            self._append("警告：{}引用了缺失 MOD 组：{}".format(label, ", ".join(expansion.missing_groups)))
        if expansion.cycles:
            raise RuntimeError("{}存在 MOD 组循环依赖：{}".format(label, "；".join(expansion.cycles)))
        if groups:
            self._append("{} MOD 组 [{}] 展开为 {} 个 MOD。".format(label, ", ".join(groups), len(expansion.mods)))
        return list(expansion.mods)

    @staticmethod
    def _merge_mod_names(*lists: List[str]) -> List[str]:
        result = []
        seen = set()
        for values in lists:
            for raw in values:
                name = str(raw).strip()
                if name and name not in seen:
                    seen.add(name)
                    result.append(name)
        return result

    def _apply_stage_mod_policy(self, retry: bool, preserve_saved: bool = False):
        c = self.configs[self.current_stage]
        if not preserve_saved:
            self.saved_mod_states = {}
        direct_enable = self._split_mods(c.retry_enable_mods if retry else c.enable_mods)
        direct_disable = self._split_mods(c.retry_disable_mods if retry else c.disable_mods)
        group_enable_text = c.retry_enable_groups if retry else c.enable_groups
        group_disable_text = c.retry_disable_groups if retry else c.disable_groups
        group_enable = self._expand_policy_groups(group_enable_text, "失败重试启用" if retry else "阶段启用")
        group_disable = self._expand_policy_groups(group_disable_text, "失败重试禁用" if retry else "阶段禁用")
        enable = self._merge_mod_names(direct_enable, group_enable)
        disable = self._merge_mod_names(direct_disable, group_disable)
        disable_set = set(disable)
        enable = [name for name in enable if name not in disable_set]

        # Determine whether this output MOD existed before this stage. Existing output
        # MODs participate in the final exact-state restore; newly created ones do not.
        out = c.output_mod.strip()
        self._stage_output_existed_before = True
        if out:
            try:
                self._stage_output_existed_before = out in set(self.mod_state.all_names(profile_priority=False))
            except Exception:
                self._stage_output_existed_before = True
            if not self._stage_output_existed_before:
                self._pipeline_new_output_mods.add(out)
            if out not in disable:
                disable.append(out)

        self._set_mods(disable, False)
        self._set_mods(enable, True)
        if enable or disable:
            self._append("已应用{} MOD 规则：启用 [{}]；禁用 [{}]".format("重试" if retry else "阶段", ", ".join(enable) or "无", ", ".join(disable) or "无"))

    def _launch_current_stage(self, retry: bool):
        stage = self.current_stage
        c = self.configs[stage]
        row = self.stage_rows[stage]
        self.table.item(row, 4).setText("运行中" + ("（自动重试）" if retry else ""))
        self._append("启动 {}{}".format(STAGE_LABELS[stage], "（失败调试规则重试）" if retry else ""))
        try:
            # Grass precaching always runs the mandatory INI preflight. Other quality
            # changes follow the AutoApplyQuality switch. TexGen CS grass setup is also
            # applied here when the guard is enabled.
            if stage == "grass":
                quality_ok, quality_detail = self.quality_manager.apply_for_stage(
                    stage, c, self.quality_preset if self.auto_quality else "off", self.shader_mode)
                self._append(quality_detail)
                if not quality_ok:
                    raise RuntimeError(quality_detail)
                self._launch_builtin_grass_precache(retry)
                return
            if self.auto_quality and self.quality_preset != "off" and stage in ("xlodgen", "texgen", "dyndolod"):
                quality_ok, quality_detail = self.quality_manager.apply_for_stage(
                    stage, c, self.quality_preset, self.shader_mode)
                self._append(quality_detail)
                if not quality_ok:
                    raise RuntimeError(quality_detail)
            self._apply_stage_mod_policy(retry)
            args = shlex.split(c.args or "", posix=False)
            cwd = c.cwd.strip() or os.path.dirname(c.exe)
            # IMPORTANT: do not pass an explicit profile to startApplication on MO2 2.4.4.
            # There is a known VFS discrepancy when a plugin launches with another profile.
            handle = self.organizer.startApplication(c.exe, args, cwd)
            if _handle_value(handle) <= 0 or _handle_value(handle) == INVALID_HANDLE_VALUE:
                raise RuntimeError("MO2 startApplication 返回无效句柄 {}".format(handle))
        except Exception as e:
            self._restore_mod_policy(False)
            self._stage_failed("无法启动工具：{}".format(e), allow_retry=not retry)
            return
        self._begin_stage_process_monitor(handle, stage, retry)

    def _begin_stage_process_monitor(self, handle, stage: str, retry: bool) -> None:
        """Monitor xLODGen/TexGen/DynDOLOD without blocking MO2 or calling MO2 APIs off-thread."""
        self._clear_active_process(close_handle=True)
        self._active_process = {
            "kind": "stage",
            "handle": handle,
            "stage": stage,
            "retry": bool(retry),
            "started_at": time.time(),
        }
        self._process_timer.start()
        self._append("{}：已进入非阻塞进程监控，星黎界面保持可操作。".format(STAGE_LABELS.get(stage, stage)))

    def _begin_grass_process_monitor(self, handle, skse_path: str, baseline_game_pids: Optional[Set[int]] = None) -> None:
        """Track the real Skyrim session, not just the short-lived SKSE loader handle."""
        self._clear_active_process(close_handle=True)
        processes = _snapshot_processes()
        game_names = {"skyrimse.exe", "skyrimvr.exe"}
        if baseline_game_pids is None:
            baseline_game_pids = {pid for pid, (_ppid, name) in processes.items() if name in game_names}
        self._active_process = {
            "kind": "grass",
            "handle": handle,
            "root_pid": _get_process_id(handle),
            "loader_name": os.path.basename(skse_path).casefold(),
            "game_names": game_names,
            "baseline_game_pids": baseline_game_pids,
            "seen_game": False,
            "root_finished": False,
            "root_exit_code": -1,
            "started_at": time.time(),
        }
        self._process_timer.start()
        self._append("Grass Cache：已进入非阻塞监控；将等待实际 Skyrim 进程退出后再判断是否续跑。")

    def _clear_active_process(self, close_handle: bool = True) -> None:
        active = self._active_process
        self._active_process = None
        if not active:
            if not self._active_process and self._process_timer.isActive():
                self._process_timer.stop()
            return
        if close_handle:
            _close_process_handle(active.get("handle"))
        if self._process_timer.isActive():
            self._process_timer.stop()

    def _poll_active_process(self) -> None:
        active = self._active_process
        if not active:
            self._process_timer.stop()
            return
        try:
            if active.get("kind") == "grass":
                self._poll_grass_process(active)
            else:
                self._poll_stage_process(active)
        except Exception as exc:
            logger.exception("non-blocking process monitor failed")
            kind = active.get("kind")
            self._clear_active_process(close_handle=True)
            if kind == "grass":
                self._pause_grass_with_error("非阻塞进程监控失败：{}".format(exc))
            else:
                self._restore_mod_policy(False)
                self._stage_failed("进程监控失败：{}".format(exc), allow_retry=not bool(active.get("retry")))

    def _poll_stage_process(self, active: dict) -> None:
        finished, code, err = _wait_handle_nonblocking(active.get("handle"))
        if not finished:
            return
        stage = str(active.get("stage", self.current_stage or ""))
        retry = bool(active.get("retry"))
        self._clear_active_process(close_handle=True)
        if err:
            self._restore_mod_policy(False)
            self._stage_failed("{} 进程状态读取失败：{}".format(STAGE_LABELS.get(stage, stage), err), allow_retry=not retry)
            return
        self._stage_process_finished(code, retry)

    def _poll_grass_process(self, active: dict) -> None:
        processes = _snapshot_processes()
        root_pid = int(active.get("root_pid", 0) or 0)
        baseline = set(active.get("baseline_game_pids", set()) or set())
        game_names = set(active.get("game_names", {"skyrimse.exe", "skyrimvr.exe"}))

        # A newly-created Skyrim process is the strongest signal. Descendants of the SKSE
        # loader are also accepted, so this remains correct when the launcher chain changes.
        descendants = _descendant_pids(processes, root_pid)
        new_game_pids = {
            pid for pid, (_ppid, name) in processes.items()
            if name in game_names and pid not in baseline
        }
        descendant_game_pids = {
            pid for pid in descendants
            if pid in processes and processes[pid][1] in game_names
        }
        running_game_pids = new_game_pids | descendant_game_pids
        if running_game_pids:
            active["seen_game"] = True
            active["last_game_pids"] = sorted(running_game_pids)

        if not active.get("root_finished"):
            finished, code, err = _wait_handle_nonblocking(active.get("handle"))
            if finished:
                active["root_finished"] = True
                active["root_exit_code"] = code
                if err:
                    active["root_error"] = err

        if running_game_pids:
            return

        # Once the real Skyrim process has appeared and is now gone, the session ended.
        if active.get("seen_game"):
            code = int(active.get("root_exit_code", -1) or -1)
            self._clear_active_process(close_handle=True)
            self._grass_game_finished(code)
            return

        # The SKSE loader can exit before SkyrimSE.exe appears. Give the child process time
        # to attach; otherwise a fast launcher exit would be misread as a game crash.
        elapsed = time.time() - float(active.get("started_at", time.time()))
        if active.get("root_finished") and elapsed >= GRASS_CHILD_ATTACH_GRACE_SECONDS:
            code = int(active.get("root_exit_code", -1) or -1)
            err = str(active.get("root_error", "") or "")
            self._append("Grass Cache：SKSE loader 已结束，但 {} 秒内未检测到 Skyrim 进程{}；按一次失败运行处理。".format(
                GRASS_CHILD_ATTACH_GRACE_SECONDS, "（{}）".format(err) if err else ""))
            self._clear_active_process(close_handle=True)
            self._grass_game_finished(code)

    def _game_directory(self) -> str:
        try:
            qdir = self.organizer.managedGame().gameDirectory()
            if hasattr(qdir, "absolutePath"):
                return os.path.normpath(str(qdir.absolutePath()))
            return os.path.normpath(str(qdir))
        except Exception:
            return ""

    def _grass_marker_path(self) -> str:
        game_dir = self._game_directory()
        return os.path.join(game_dir, "PrecacheGrass.txt") if game_dir else ""

    def _grass_output_root(self) -> str:
        try:
            root = str(self.organizer.overwritePath())
            if root:
                return os.path.normpath(root)
        except Exception:
            pass
        return ""

    def _grass_plugin_present(self) -> Tuple[bool, str]:
        checks = (
            ("SKSE/Plugins/", "*NGIO-NG.dll", "NGIO-NG"),
            ("NetScriptFramework/Plugins/", "*GrassControl.dll", "GrassControl"),
        )
        for folder, pattern, label in checks:
            try:
                result = self.organizer.findFiles(folder, pattern)
                if result:
                    return True, label
            except Exception:
                continue
        return False, ""

    def _grass_skse_path(self) -> Tuple[str, str]:
        game_dir = self._game_directory()
        try:
            short = str(self.organizer.managedGame().gameShortName())
        except Exception:
            short = ""
        loader = "sksevr_loader.exe" if short == "SkyrimVR" else "skse64_loader.exe"
        return os.path.join(game_dir, loader), short

    def _clear_overwrite_grass_cache(self) -> int:
        root = self._grass_output_root()
        if not root:
            return 0
        grass_root = os.path.join(root, "Grass")
        if not os.path.isdir(grass_root):
            return 0
        removed = 0
        for dirpath, _dirs, files in os.walk(grass_root):
            for fn in files:
                if not fn.lower().endswith((".cgid", ".gid")):
                    continue
                try:
                    os.remove(os.path.join(dirpath, fn))
                    removed += 1
                except OSError:
                    pass
        return removed

    def _grass_cache_stats(self) -> Tuple[int, int]:
        """Return (file_count, total_bytes) for partial cache currently in MO2 Overwrite/Grass."""
        root = self._grass_output_root()
        if not root:
            return 0, 0
        grass_root = os.path.join(root, "Grass")
        if not os.path.isdir(grass_root):
            return 0, 0
        count = 0
        total = 0
        for dirpath, _dirs, files in os.walk(grass_root):
            for fn in files:
                if not fn.lower().endswith((".cgid", ".gid")):
                    continue
                count += 1
                try:
                    total += int(os.path.getsize(os.path.join(dirpath, fn)))
                except OSError:
                    pass
        return count, total

    @staticmethod
    def _grass_checkpoint_is_resumable(checkpoint: dict) -> bool:
        return bool(checkpoint) and str(checkpoint.get("status", "")).lower() in {
            "running", "paused", "paused_error", "interrupted"
        }

    def _grass_policy_signature(self) -> str:
        c = self.configs["grass"]
        parts = [
            c.output_mod or "",
            c.enable_mods or "", c.disable_mods or "",
            c.retry_enable_mods or "", c.retry_disable_mods or "",
            c.enable_groups or "", c.disable_groups or "",
            c.retry_enable_groups or "", c.retry_disable_groups or "",
            self.quality_preset if self.auto_quality else "off",
            self.shader_mode or "auto",
        ]
        h = hashlib.sha256()
        for item in parts:
            h.update(str(item).strip().casefold().encode("utf-8", errors="replace"))
            h.update(b"\0")
        return h.hexdigest()

    @staticmethod
    def _hash_file(path: str) -> str:
        if not path or not os.path.isfile(path):
            return ""
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                while True:
                    data = f.read(256 * 1024)
                    if not data:
                        break
                    h.update(data)
            return h.hexdigest()
        except OSError:
            return ""

    def _legacy_grass_checkpoint_fast_compatible(self, checkpoint: dict) -> Tuple[bool, str]:
        """Compatibility gate for v1.7.0/1.7.1 checkpoints without rescanning mod contents.

        Called after the same temporary grass MOD policy is re-applied. It verifies the
        profile/load order, active mod names/priorities, GrassControl and game INIs. This
        avoids the old full directory snapshot that could freeze very large modlists.
        """
        old = checkpoint.get("fingerprint_details", {}) if isinstance(checkpoint, dict) else {}
        if not isinstance(old, dict) or not old:
            return False, "旧断点缺少 fingerprint_details"
        try:
            active_rows = self.snapshot._active_mod_rows()
            active_parts = ["{}|{}".format(name, prio) for name, _path, prio in active_rows]
            active_mods = self.snapshot._digest(active_parts)
        except Exception:
            active_mods = ""
        try:
            grass_ini = self.quality_manager._find_grass_control_source()
        except Exception:
            grass_ini = ""
        try:
            profile = os.path.normpath(str(self.organizer.profilePath()))
        except Exception:
            profile = ""
        current = {
            "active_mods": active_mods,
            "plugins_txt": self.snapshot._profile_file_hash("plugins.txt"),
            "loadorder_txt": self.snapshot._profile_file_hash("loadorder.txt"),
            "grass_control_ini": self._hash_file(grass_ini),
            "skyrim_ini": self._hash_file(os.path.join(profile, "Skyrim.ini")) if profile else "",
            "skyrimprefs_ini": self._hash_file(os.path.join(profile, "SkyrimPrefs.ini")) if profile else "",
            "policy": self._grass_policy_signature(),
        }
        mismatched = [k for k, v in current.items() if str(old.get(k, "")) != str(v)]
        if mismatched:
            return False, "旧断点关键环境发生变化：{}".format(", ".join(mismatched))
        return True, "旧断点关键环境一致；已跳过旧版全目录指纹重算"

    def _grass_environment_fingerprint(self) -> Tuple[str, dict]:
        """Fingerprint the effective generation environment after the grass stage MOD policy is applied."""
        snap = dict(self._pipeline_start_snapshot or getattr(self, "_latest_snapshot", {}) or {})
        if not snap:
            # Defensive fallback only. Normal runs always have an Analyze snapshot.
            snap = self.snapshot.build()
        try:
            grass_ini = self.quality_manager._find_grass_control_source()
        except Exception:
            grass_ini = ""
        try:
            profile = os.path.normpath(str(self.organizer.profilePath()))
        except Exception:
            profile = ""
        details = {
            "active_mods": snap.get("active_mods", ""),
            "plugins_txt": snap.get("plugins_txt", ""),
            "loadorder_txt": snap.get("loadorder_txt", ""),
            "grass": snap.get("grass", ""),
            "terrain": snap.get("terrain", ""),
            "models": snap.get("models", ""),
            "grass_control_ini": self._hash_file(grass_ini),
            "skyrim_ini": self._hash_file(os.path.join(profile, "Skyrim.ini")) if profile else "",
            "skyrimprefs_ini": self._hash_file(os.path.join(profile, "SkyrimPrefs.ini")) if profile else "",
            "policy": self._grass_policy_signature(),
        }
        payload = json.dumps(details, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest(), details

    def _save_grass_checkpoint(self, status: Optional[str] = None, detail: str = "", refresh_stats: bool = False) -> dict:
        checkpoint = dict(self.grass_checkpoint or self.store.load_json(self.store.grass_checkpoint_path, {}))
        if not checkpoint:
            checkpoint = {"schema": 1, "created_at": time.time()}
        if status:
            checkpoint["status"] = status
        checkpoint["updated_at"] = time.time()
        checkpoint["restart_count"] = int(self.grass_restart_count)
        checkpoint["marker_path"] = self._grass_marker_path()
        checkpoint["pipeline_tail"] = list(self.pending)
        checkpoint["policy_signature"] = self._grass_policy_signature()
        if detail:
            checkpoint["detail"] = detail
        if refresh_stats:
            count, total = self._grass_cache_stats()
            checkpoint["cache_file_count"] = count
            checkpoint["cache_total_bytes"] = total
        self.grass_checkpoint = checkpoint
        self.store.save_json(self.store.grass_checkpoint_path, checkpoint)
        return checkpoint

    def _restore_checkpoint_mod_states(self, checkpoint: dict) -> None:
        states = checkpoint.get("original_mod_states", {}) if checkpoint else {}
        if not isinstance(states, dict) or not states:
            return
        # Defensive fallback for old/partially constructed dialogs. The normal
        # v1.8.4 path binds self.mod_state in __init__, but checkpoint cleanup must
        # never crash merely because a state-service reference is missing.
        service = getattr(self, "mod_state", None) or getattr(self.store, "mod_state", None)
        if service is None:
            self._append("警告：无法恢复断点前 MOD 状态：ModStateService 未初始化。")
            return
        try:
            normalized = {str(name): bool(active) for name, active in states.items()}
            # Prefer the dialog helper so partial batch failures are retried through
            # the scalar MO2 2.4.4 path. Keep the service fallback for defensive use.
            if service is self.mod_state:
                self._restore_state_map_exact(normalized, "恢复断点前 MOD 状态")
            else:
                result = service.restore_states(normalized, verify=True)
                for name in result.failures:
                    self._append("警告：恢复断点前 MOD 状态失败：{}".format(name))
        except Exception as e:
            logger.exception("grass checkpoint MOD state restore failed")
            self._append("警告：恢复断点前 MOD 状态异常：{}".format(e))
            return

    def _discard_grass_checkpoint(self, ask: bool = True):
        if self.pipeline_running:
            return
        checkpoint = self.store.load_json(self.store.grass_checkpoint_path, {})
        if not self._grass_checkpoint_is_resumable(checkpoint):
            self._append("当前没有可放弃的草缓存断点。")
            return
        if ask:
            result = QtWidgets.QMessageBox.warning(
                self, "清除草缓存进度",
                "这会删除当前未完成的草缓存，并恢复生成前的临时 MOD 状态。\n\n确定清除进度并重新开始吗？",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if result != QtWidgets.QMessageBox.Yes:
                return
        self._restore_checkpoint_mod_states(checkpoint)
        self._cleanup_grass_marker()
        self.btn_discard_grass.setEnabled(False)
        self._append("正在后台清理断点草缓存；界面保持可操作…")
        if not self._start_grass_fs_job("clear", {"action": "discard_clear", "checkpoint": checkpoint}):
            self._append("⚠ 无法启动后台断点清理任务。")

    def _finish_discard_grass_checkpoint(self, removed: int, context: dict):
        checkpoint = dict(context.get("checkpoint") or {})
        checkpoint["status"] = "discarded"
        checkpoint["updated_at"] = time.time()
        checkpoint["detail"] = "user/environment discarded checkpoint; removed={}".format(removed)
        self.store.save_json(self.store.grass_checkpoint_path, checkpoint)
        self.grass_checkpoint = checkpoint
        self._append("已清除草缓存进度并后台清理 {} 个部分缓存文件；下次将从头生成。".format(removed))
        self.analyze_changes()

    def _resume_grass_checkpoint(self):
        if self.pipeline_running:
            return
        checkpoint = self.store.load_json(self.store.grass_checkpoint_path, {})
        if not self._grass_checkpoint_is_resumable(checkpoint):
            QtWidgets.QMessageBox.information(self, "草缓存断点", "当前没有可继续的草缓存断点。")
            return
        marker = self._grass_marker_path()
        if not marker or not os.path.isfile(marker):
            QtWidgets.QMessageBox.warning(
                self, "草缓存进度不可用",
                "草缓存断点已经不完整，无法安全继续。\n\n建议清除当前进度后重新生成。")
            return
        tail = checkpoint.get("pipeline_tail", [])
        stages = ["grass"] + [s for s in tail if s in STAGES and s != "grass"]
        self._append("请求继续草缓存断点：记录 {} 个缓存文件；后续阶段 {}。".format(
            int(checkpoint.get("cache_file_count", 0) or 0),
            " → ".join(STAGE_LABELS[s] for s in stages[1:]) or "无"))
        self.start_pipeline(stages)

    def _start_new_grass_checkpoint(self, fingerprint: str, fingerprint_details: dict, removed: int) -> None:
        # A fresh job starts after the old cache clear worker finished, so stats are zero.
        # Avoid a second synchronous directory walk before launching Skyrim.
        count, total = 0, 0
        self.grass_checkpoint = {
            "schema": 1,
            "status": "running",
            "created_at": time.time(),
            "updated_at": time.time(),
            "fingerprint": fingerprint,
            "fingerprint_method": "cached_snapshot_v2",
            "fingerprint_details": fingerprint_details,
            "policy_signature": self._grass_policy_signature(),
            "original_mod_states": dict(self.saved_mod_states),
            "restart_count": 0,
            "resume_base_restart_count": 0,
            "no_progress_restarts": 0,
            "cache_file_count": count,
            "cache_total_bytes": total,
            "marker_path": self._grass_marker_path(),
            "pipeline_tail": list(self.pending),
            "detail": "fresh grass cache job; removed_old_partial={}".format(removed),
        }
        self.store.save_json(self.store.grass_checkpoint_path, self.grass_checkpoint)

    def _grass_cache_dir(self) -> str:
        root = self._grass_output_root()
        return os.path.join(root, "Grass") if root else ""

    def _start_grass_fs_job(self, operation: str, context: dict) -> bool:
        if self._grass_fs_thread is not None:
            self._append("⚠ 草缓存文件系统任务仍在运行，忽略重复请求：{}".format(operation))
            return False
        grass_root = self._grass_cache_dir()
        self._grass_fs_context = dict(context or {})
        thread = QtCore.QThread(self)
        worker = GrassFilesystemWorker(operation, grass_root)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._append)
        worker.finished.connect(self._on_grass_fs_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._grass_fs_thread = thread
        self._grass_fs_worker = worker
        thread.start()
        return True

    def _on_grass_fs_finished(self, operation: str, result, error: str):
        context = dict(self._grass_fs_context or {})
        self._grass_fs_context = None
        self._grass_fs_thread = None
        self._grass_fs_worker = None
        if error:
            self._append("❌ 草缓存后台文件任务失败：{}".format(error))
            if context.get("action") == "fresh_clear":
                self._restore_mod_policy(False)
                self._stage_failed("清理旧草缓存失败：{}".format(error), allow_retry=False)
            elif context.get("action") == "post_exit_stats":
                self._pause_grass_with_error("统计草缓存进度失败：{}".format(error))
            return

        if operation == "clear" and context.get("action") == "fresh_clear":
            self._finish_fresh_grass_prepare(int((result or {}).get("removed", 0) or 0), context)
            return
        if operation == "clear" and context.get("action") == "discard_clear":
            self._finish_discard_grass_checkpoint(int((result or {}).get("removed", 0) or 0), context)
            return
        if operation == "stats" and context.get("action") == "post_exit_stats":
            self._finish_grass_post_exit_stats(
                int((result or {}).get("count", 0) or 0),
                int((result or {}).get("total", 0) or 0),
                int(context.get("exit_code", -1)))
            return

    def _begin_fresh_grass_prepare(self, retry: bool, provider: str, game_short: str, marker: str):
        self.grass_resume_mode = False
        self.grass_restart_count = 0
        self.grass_started_at = time.time()
        self.grass_abort_requested = False
        self.table.item(self.stage_rows["grass"], 4).setText("准备中…")
        self.btn_abort_grass.setEnabled(False)
        self._append("Grass Cache：后台清理旧 Overwrite/Grass 缓存；界面不会被大量小文件删除阻塞。")
        ok = self._start_grass_fs_job("clear", {
            "action": "fresh_clear", "retry": bool(retry), "provider": provider,
            "game_short": game_short, "marker": marker,
        })
        if not ok:
            self._restore_mod_policy(False)
            self._stage_failed("无法启动草缓存后台清理任务", allow_retry=False)

    def _finish_fresh_grass_prepare(self, removed: int, context: dict):
        if self.grass_abort_requested:
            self._restore_mod_policy(False)
            self.pipeline_running = False
            self.pending = []
            self._set_controls(True)
            self._append("草缓存准备阶段已取消；没有启动 Skyrim。")
            return
        marker = str(context.get("marker", "") or self._grass_marker_path())
        try:
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            Path(marker).touch(exist_ok=True)
            if not os.path.isfile(marker):
                raise RuntimeError("PrecacheGrass.txt 创建后仍不可见：{}".format(marker))
            fingerprint, details = self._grass_environment_fingerprint()
            self._start_new_grass_checkpoint(fingerprint, details, removed)
            self._append("内置 Grass Precacher：检测到 {}；游戏 {}；后台已清理旧缓存 {} 个。".format(
                context.get("provider", "NGIO"), context.get("game_short", "未知") or "未知", removed))
            self._append("草缓存触发标记已确认存在：{}".format(marker))
            self._append("断点文件：{}".format(self.store.grass_checkpoint_path))
            self._append("Grass Cache 准备 3/3：准备通过 MO2 启动 SKSE…")
            self.btn_abort_grass.setEnabled(True)
            # Give Qt one event-loop turn to repaint the UI before MO2 launches SKSE.
            QtCore.QTimer.singleShot(0, self._launch_grass_game_once)
        except Exception as e:
            self._restore_mod_policy(False)
            self._cleanup_grass_marker()
            self._stage_failed("草缓存准备失败：{}".format(e), allow_retry=False)

    def _finish_grass_post_exit_stats(self, current_count: int, total: int, code: int):
        if self.grass_abort_requested:
            self._finalize_grass_pause("用户暂停")
            return
        checkpoint = dict(self.grass_checkpoint or self.store.load_json(self.store.grass_checkpoint_path, {}))
        before_count = int(checkpoint.get("cache_file_count", 0) or 0)
        no_progress = int(checkpoint.get("no_progress_restarts", 0) or 0)
        no_progress = 0 if current_count > before_count else no_progress + 1
        checkpoint["status"] = "running"
        checkpoint["updated_at"] = time.time()
        checkpoint["restart_count"] = int(self.grass_restart_count)
        checkpoint["cache_file_count"] = current_count
        checkpoint["cache_total_bytes"] = total
        checkpoint["no_progress_restarts"] = no_progress
        checkpoint["detail"] = "game exited code {}; marker remains".format(code)
        checkpoint["pipeline_tail"] = list(self.pending)
        self.grass_checkpoint = checkpoint
        self.store.save_json(self.store.grass_checkpoint_path, checkpoint)
        elapsed = int(time.time() - self.grass_started_at) if self.grass_started_at else 0
        self._append("Grass Cache：游戏已退出（退出码 {}），PrecacheGrass.txt 仍存在；当前已有 {} 个缓存文件，{} 秒后自动续跑。累计本会话 {} 分钟。".format(
            code, current_count, GRASS_RESTART_DELAY_MS // 1000, elapsed // 60))
        if no_progress >= GRASS_MAX_NO_PROGRESS_RESTARTS:
            self._pause_grass_with_error("连续 {} 次 Skyrim 运行没有新增草缓存文件；可能 NGIO 未进入预缓存、配置错误或存在冲突".format(no_progress))
            return
        self.grass_restart_timer = QtCore.QTimer(self)
        self.grass_restart_timer.setSingleShot(True)
        self.grass_restart_timer.timeout.connect(self._launch_grass_game_once)
        self.grass_restart_timer.start(GRASS_RESTART_DELAY_MS)

    def _launch_builtin_grass_precache(self, retry: bool):
        try:
            present, provider = self._grass_plugin_present()
            if not present:
                raise RuntimeError("未检测到 NGIO-NG.dll 或 GrassControl.dll；请先启用草缓存生成插件")
            skse_path, game_short = self._grass_skse_path()
            if not skse_path or not os.path.isfile(skse_path):
                raise RuntimeError("未找到 SKSE 启动器：{}".format(skse_path or "(空路径)"))
            marker = self._grass_marker_path()
            if not marker:
                raise RuntimeError("无法确定游戏目录 / PrecacheGrass.txt 路径")
            output_root = self._grass_output_root()
            if not output_root:
                raise RuntimeError("无法确定 MO2 Overwrite 目录")

            existing = self.store.load_json(self.store.grass_checkpoint_path, {})
            resumable = self._grass_checkpoint_is_resumable(existing)
            policy_matches = existing.get("policy_signature") == self._grass_policy_signature() if resumable else False

            # Resume path: reuse the original MOD states captured when the checkpoint was created,
            # re-apply the exact same temporary stage policy, then verify the environment fingerprint.
            if resumable and policy_matches and os.path.isfile(marker):
                saved = existing.get("original_mod_states", {})
                self.saved_mod_states = dict(saved) if isinstance(saved, dict) else {}
                if isinstance(saved, dict):
                    for name, active in saved.items():
                        if str(name) and str(name) not in self.pipeline_original_mod_states:
                            self.pipeline_original_mod_states[str(name)] = bool(active)
                # Critical: do not clear checkpoint.original_mod_states while
                # re-applying the temporary Grass policy.
                self._apply_stage_mod_policy(retry, preserve_saved=True)
                fingerprint, details = self._grass_environment_fingerprint()
                method = str(existing.get("fingerprint_method", "legacy_v1"))
                compatible = fingerprint == existing.get("fingerprint") if method == "cached_snapshot_v2" else False
                compat_detail = "环境指纹一致"
                if method != "cached_snapshot_v2":
                    compatible, compat_detail = self._legacy_grass_checkpoint_fast_compatible(existing)
                if compatible:
                    self.grass_checkpoint = existing
                    # Upgrade old checkpoints to the v1.7.2 cached-snapshot method so future
                    # resumes never need the legacy directory-heavy fingerprint.
                    self.grass_checkpoint["fingerprint"] = fingerprint
                    self.grass_checkpoint["fingerprint_method"] = "cached_snapshot_v2"
                    self.grass_checkpoint["fingerprint_details"] = details
                    self.grass_resume_mode = True
                    self.grass_restart_count = int(existing.get("restart_count", 0) or 0)
                    self.grass_checkpoint["resume_base_restart_count"] = self.grass_restart_count
                    self.grass_checkpoint["no_progress_restarts"] = 0
                    self.grass_started_at = time.time()
                    self.grass_abort_requested = False
                    count = int(existing.get("cache_file_count", 0) or 0)
                    total = int(existing.get("cache_total_bytes", 0) or 0)
                    self._append("✅ 草缓存断点有效：{}；保留已记录 {} 个缓存文件（约 {:.1f} MB），继续生成。".format(compat_detail, count, total / 1024 / 1024))
                    self._save_grass_checkpoint("running", "resumed checkpoint", refresh_stats=False)
                    self.btn_abort_grass.setEnabled(True)
                    QtCore.QTimer.singleShot(0, self._launch_grass_game_once)
                    return
                self._append("⚠ 草缓存断点环境指纹已变化；为避免混用旧缓存，将恢复原 MOD 状态并从头生成。")
                self._restore_checkpoint_mod_states(existing)
                self.saved_mod_states = {}
                self._cleanup_grass_marker()
                existing["status"] = "invalidated"
                existing["updated_at"] = time.time()
                existing["detail"] = "fingerprint mismatch"
                self.store.save_json(self.store.grass_checkpoint_path, existing)
            elif resumable:
                # Policy change or missing marker means the partial cache cannot be trusted for automatic resume.
                reason = "流水线/草地预设发生变化" if not policy_matches else "PrecacheGrass.txt 缺失"
                self._append("⚠ 检测到旧草缓存断点，但{}；自动作废并从头生成。".format(reason))
                self._restore_checkpoint_mod_states(existing)
                self.saved_mod_states = {}
                self._cleanup_grass_marker()
                existing["status"] = "invalidated"
                existing["updated_at"] = time.time()
                existing["detail"] = reason
                self.store.save_json(self.store.grass_checkpoint_path, existing)

            # Fresh job path. MO2 API calls stay on the UI thread, but large filesystem
            # cleanup runs in a pure worker thread so the dialog remains responsive.
            self._append("Grass Cache 准备 1/3：应用阶段 MOD 规则…")
            self._apply_stage_mod_policy(retry)
            self._append("Grass Cache 准备 2/3：MOD 规则已应用；准备后台清理旧缓存…")
            self._begin_fresh_grass_prepare(retry, provider, game_short, marker)
        except Exception as e:
            # If no resumable checkpoint exists yet, this is a normal launch failure.
            checkpoint = self.store.load_json(self.store.grass_checkpoint_path, {})
            if self._grass_checkpoint_is_resumable(checkpoint):
                self._pause_grass_with_error("草缓存内置启动失败：{}".format(e))
            else:
                self._restore_mod_policy(False)
                self._cleanup_grass_marker()
                self._stage_failed("草缓存内置启动失败：{}".format(e), allow_retry=not retry)

    def _launch_grass_game_once(self):
        if self.grass_abort_requested:
            return
        session_restarts = int(self.grass_restart_count) - int((self.grass_checkpoint or {}).get("resume_base_restart_count", 0) or 0)
        if session_restarts >= GRASS_MAX_RESTARTS:
            self._pause_grass_with_error("本次会话自动重启次数达到安全上限 {} 次".format(GRASS_MAX_RESTARTS))
            return
        if self.grass_started_at and time.time() - self.grass_started_at > GRASS_MAX_RUNTIME_SECONDS:
            self._pause_grass_with_error("本次草缓存会话运行超过安全上限 {} 小时".format(GRASS_MAX_RUNTIME_SECONDS // 3600))
            return
        skse_path, _ = self._grass_skse_path()
        marker = self._grass_marker_path()
        if not marker or not os.path.isfile(marker):
            self._pause_grass_with_error("启动 SKSE 前 PrecacheGrass.txt 不存在；断点已暂停")
            return
        # Capture existing game PIDs BEFORE starting SKSE. The loader can spawn Skyrim
        # quickly enough that a post-launch snapshot would accidentally classify the new
        # game as a pre-existing process.
        prelaunch_processes = _snapshot_processes()
        baseline_game_pids = {
            pid for pid, (_ppid, name) in prelaunch_processes.items()
            if name in {"skyrimse.exe", "skyrimvr.exe"}
        }
        try:
            handle = self.organizer.startApplication(skse_path, ["-forcesteamloader"])
            if _handle_value(handle) <= 0 or _handle_value(handle) == INVALID_HANDLE_VALUE:
                raise RuntimeError("MO2 startApplication 返回无效句柄 {}".format(handle))
        except Exception as e:
            self._pause_grass_with_error("MO2 无法启动 SKSE：{}".format(e))
            return
        self.grass_restart_count += 1
        if self.grass_checkpoint and "resume_base_restart_count" not in self.grass_checkpoint:
            self.grass_checkpoint["resume_base_restart_count"] = self.grass_restart_count - 1
        self._save_grass_checkpoint("running", "Skyrim launched", refresh_stats=False)
        self.table.item(self.stage_rows["grass"], 4).setText("运行中（累计第 {} 次启动）".format(self.grass_restart_count))
        self._append("Grass Cache：累计第 {} 次启动 Skyrim。".format(self.grass_restart_count))
        self._begin_grass_process_monitor(handle, skse_path, baseline_game_pids)

    def _grass_game_finished(self, code: int):
        if self.grass_abort_requested:
            self._finalize_grass_pause("用户暂停")
            return
        marker = self._grass_marker_path()
        if marker and os.path.exists(marker):
            self.table.item(self.stage_rows["grass"], 4).setText("读取草缓存进度…")
            self._append("Grass Cache：Skyrim 已退出；后台统计新增缓存，不阻塞 MO2 界面。")
            if not self._start_grass_fs_job("stats", {"action": "post_exit_stats", "exit_code": int(code)}):
                self._pause_grass_with_error("无法启动草缓存进度统计任务")
            return

        output_root = self._grass_output_root()
        if not self._output_has_files("grass", output_root):
            self._pause_grass_with_error("PrecacheGrass.txt 已消失，但 MO2 Overwrite 中没有找到 .cgid/.gid 草缓存")
            return
        copy_ok, copy_detail = self._copy_stage_output("grass")
        if not copy_ok:
            self._pause_grass_with_error(copy_detail)
            return
        self._restore_mod_policy(True)
        self.btn_abort_grass.setEnabled(False)
        self.table.item(self.stage_rows["grass"], 4).setText("完成")
        elapsed = int(time.time() - self.grass_started_at) if self.grass_started_at else 0
        self._append("✅ Grass Cache 草地缓存完成：累计启动 {} 次，本会话耗时约 {} 分钟。{}".format(self.grass_restart_count, max(1, elapsed // 60), copy_detail))
        self._save_grass_checkpoint("success", "built-in GrassPrecacher completed", refresh_stats=False)
        self.store.save_json(self.store.state_path, {
            "last_stage": "grass", "status": "success", "at": time.time(),
            "detail": "built-in GrassPrecacher completed; restarts={}".format(self.grass_restart_count),
        })
        self._run_next()

    def _cleanup_grass_marker(self):
        marker = self._grass_marker_path()
        if marker and os.path.isfile(marker):
            try:
                os.remove(marker)
            except OSError:
                pass

    def _pause_grass_with_error(self, detail: str):
        # Deliberately KEEP PrecacheGrass.txt and partial .cgid/.gid files. This is the
        # persistent error checkpoint: reopening MO2 and pressing Resume can continue.
        self._save_grass_checkpoint("paused_error", detail, refresh_stats=False)
        self.btn_abort_grass.setEnabled(False)
        self.pipeline_running = False
        self.pending = []
        self.table.item(self.stage_rows["grass"], 4).setText("已暂停（可继续）")
        self._set_controls(True)
        self._append("⏸ Grass Cache 错误暂停：{}。断点、PrecacheGrass.txt 和部分缓存均已保留。".format(detail))
        QtWidgets.QMessageBox.critical(
            self, "草缓存已暂停",
            "草缓存发生错误，后续 LOD 阶段没有执行。\n\n{}\n\n当前进度已保留。修复问题后点击【继续草缓存】。".format(detail))

    def _finalize_grass_pause(self, reason: str = "用户暂停"):
        # Do not restore temporary MOD states and do not remove the marker: both are part of
        # the exact generation checkpoint. Discard or successful completion will restore them.
        self._save_grass_checkpoint("paused", reason, refresh_stats=False)
        self.pipeline_running = False
        self.pending = []
        self.table.item(self.stage_rows["grass"], 4).setText("已暂停")
        self.btn_abort_grass.setEnabled(False)
        self._set_controls(True)
        self._append("⏸ 草缓存已暂停，当前进度已保留。需要继续时点击【继续草缓存】。")

    def _abort_grass_precache(self):
        if not self.pipeline_running or self.current_stage != "grass":
            return
        self.grass_abort_requested = True
        # Preserve the remaining pipeline stages in checkpoint before clearing in-memory queue.
        self._save_grass_checkpoint("paused", "pause requested", refresh_stats=False)
        if self.grass_restart_timer is not None:
            try:
                self.grass_restart_timer.stop()
            except Exception:
                pass
        self.btn_abort_grass.setEnabled(False)
        self._append("已请求暂停草缓存自动循环；不会删除 PrecacheGrass.txt 或已生成缓存。")
        waiting_for_process = bool(self._active_process and self._active_process.get("kind") == "grass")
        if waiting_for_process:
            self._append("当前 Skyrim 仍在运行，请正常关闭；关闭后星黎会进入断点暂停，不再自动重启。")
        else:
            self._finalize_grass_pause("用户暂停")

    def closeEvent(self, event):
        if self._grass_fs_thread is not None:
            self._append("后台草缓存文件任务仍在运行；完成后才能关闭窗口，避免中断清理/统计。")
            event.ignore()
            return
        # Never allow an external LOD stage monitor to be orphaned: losing the
        # completion callback would also lose the guaranteed MOD-state restore.
        if self._active_process and self._active_process.get("kind") != "grass":
            QtWidgets.QMessageBox.information(
                self, "LOD 正在生成",
                "当前生成工具仍在运行。请等待本阶段完成后再关闭窗口，星黎会自动恢复临时 MOD 状态。")
            event.ignore()
            return
        # Grass is resumable: closing preserves its checkpoint and exact temporary
        # policy; successful completion/discard will restore the checkpoint baseline.
        if self._active_process:
            if self._active_process.get("kind") == "grass":
                self._save_grass_checkpoint("interrupted", "LOD dialog closed while Skyrim was running", refresh_stats=False)
            self._clear_active_process(close_handle=True)
        super().closeEvent(event)

    def _stage_process_finished(self, code: int, retry: bool):
        stage = self.current_stage
        ok, detail = self._verify_stage(stage, code)
        if not ok:
            self._restore_mod_policy(False)
            self._stage_failed(detail, allow_retry=not retry)
            return
        copy_ok, copy_detail = self._copy_stage_output(stage)
        if not copy_ok:
            self._restore_mod_policy(False)
            self._stage_failed(copy_detail, allow_retry=False)
            return
        self._restore_mod_policy(True)
        self.table.item(self.stage_rows[stage], 4).setText("完成")
        self._append("✅ {} 完成。{}".format(STAGE_LABELS[stage], copy_detail))
        self.store.save_json(self.store.state_path, {
            "last_stage": stage, "status": "success", "at": time.time(), "detail": detail,
        })
        self._run_next()

    def _stage_failed(self, detail: str, allow_retry: bool):
        stage = self.current_stage
        c = self.configs[stage]
        self._append("❌ {} 失败：{}".format(STAGE_LABELS[stage], detail))
        if allow_retry and c.auto_retry and (c.retry_enable_mods.strip() or c.retry_disable_mods.strip()):
            self.current_attempt += 1
            self._append("检测到失败，自动应用调试 MOD 规则并重试一次。")
            self._launch_current_stage(retry=True)
            return
        self._restore_pipeline_mod_states("流水线失败停止")
        self.table.item(self.stage_rows[stage], 4).setText("失败，已停止")
        self.store.save_json(self.store.state_path, {
            "last_stage": stage, "status": "failed", "at": time.time(), "detail": detail,
        })
        self.pipeline_running = False
        self.pending = []
        self._set_controls(True)
        QtWidgets.QMessageBox.critical(self, "LOD 生成已停止", "{} 失败，后续阶段没有继续执行。\n\n{}\n\n请修复后重新运行推荐阶段。".format(STAGE_LABELS[stage], detail))

    def _find_latest_log(self, c: StageConfig) -> str:
        base = c.log_dir.strip()
        if not base or not os.path.isdir(base) or not c.log_pattern.strip():
            return ""
        import glob
        matches = glob.glob(os.path.join(base, c.log_pattern.strip()))
        matches = [p for p in matches if os.path.isfile(p)]
        if not matches:
            return ""
        return max(matches, key=lambda p: os.path.getmtime(p))

    def _verify_stage(self, stage: str, exit_code: int) -> Tuple[bool, str]:
        c = self.configs[stage]
        if exit_code != 0:
            return False, "进程退出码 {}".format(exit_code)
        log_path = self._find_latest_log(c)
        if log_path:
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                for pattern in FATAL_PATTERNS:
                    m = pattern.search(text)
                    if m:
                        return False, "日志检测到错误：{}；日志 {}".format(m.group(0), log_path)
                if c.success_marker.strip() and c.success_marker.strip().casefold() not in text.casefold():
                    return False, "没有在最新日志中找到成功标记“{}”：{}".format(c.success_marker, log_path)
            except OSError as e:
                return False, "无法读取验证日志：{}".format(e)
        elif c.success_marker.strip():
            return False, "已配置成功标记，但没有找到匹配日志；请检查 LogDir / LogPattern"

        if c.output_source.strip() and not self._output_has_files(stage, c.output_source.strip()):
            return False, "输出目录为空或不存在：{}".format(c.output_source)
        return True, "退出码和日志/输出检查通过" + ("；日志 " + log_path if log_path else "")

    @staticmethod
    def _output_has_files(stage: str, root: str) -> bool:
        if not os.path.isdir(root):
            return False
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if stage == "grass" and not fn.lower().endswith((".cgid", ".gid")):
                    continue
                return True
        return False

    def _mods_root(self) -> str:
        for attr in ("modsPath",):
            if hasattr(self.organizer, attr):
                try:
                    p = str(getattr(self.organizer, attr)())
                    if p: return p
                except Exception:
                    pass
        # Derive from any managed mod folder.
        try:
            ml = self.organizer.modList()
            names = list(ml.allMods()) if hasattr(ml, "allMods") else list(ml.modNames())
            for raw in names:
                try:
                    mod = ml.getMod(str(raw))
                    p = str(mod.absolutePath()) if mod else ""
                    if p and os.path.isdir(p):
                        return os.path.dirname(p)
                except Exception:
                    continue
        except Exception:
            pass
        return ""

    def _copy_stage_output(self, stage: str) -> Tuple[bool, str]:
        c = self.configs[stage]
        if not c.copy_output:
            return True, "未启用自动复制输出"
        src = self._grass_output_root() if stage == "grass" else c.output_source.strip()
        name = c.output_mod.strip()
        if not src or not name:
            return True, "未设置输出目录或输出，跳过自动复制"
        if not os.path.isdir(src):
            return False, "输出源目录不存在：{}".format(src)
        mods_root = self._mods_root()
        if not mods_root:
            return False, "无法确定 MO2 mods 目录"
        dst = os.path.join(mods_root, name)
        try:
            os.makedirs(dst, exist_ok=True)
            # Replace destination contents to avoid stale generated assets surviving an update.
            for item in os.listdir(dst):
                p = os.path.join(dst, item)
                if os.path.isdir(p) and not os.path.islink(p): shutil.rmtree(p)
                else: os.remove(p)

            copied = 0
            if stage == "grass":
                # Preserve path relative to the configured source. Users can point OutputSource
                # at Overwrite or directly at Overwrite/Grass depending on their cache setup.
                for root, _dirs, files in os.walk(src):
                    for fn in files:
                        if not fn.lower().endswith((".cgid", ".gid")):
                            continue
                        sp = os.path.join(root, fn)
                        rel = os.path.relpath(sp, src)
                        # If source itself is the Grass folder, ensure MO2 mod gets Data/Grass semantics.
                        if os.path.basename(os.path.normpath(src)).casefold() == "grass":
                            rel = os.path.join("Grass", rel)
                        dp = os.path.join(dst, rel)
                        os.makedirs(os.path.dirname(dp), exist_ok=True)
                        shutil.copy2(sp, dp); copied += 1
            else:
                for item in os.listdir(src):
                    sp = os.path.join(src, item); dp = os.path.join(dst, item)
                    if os.path.isdir(sp): shutil.copytree(sp, dp, dirs_exist_ok=True)
                    else: shutil.copy2(sp, dp)
                    copied += 1
            if copied == 0:
                return False, "没有找到可复制的生成文件：{}".format(src)
            if c.clear_output_source_after_copy:
                for item in os.listdir(src):
                    p = os.path.join(src, item)
                    if os.path.isdir(p) and not os.path.islink(p): shutil.rmtree(p)
                    else: os.remove(p)
            try:
                if hasattr(self.organizer, "refresh"):
                    self.organizer.refresh(True)
            except Exception:
                pass
            self._pipeline_created_output_mods.add(name)
            # Activation is handled by _restore_mod_policy(True): it stays available
            # for downstream stages, then the pipeline final guard restores the exact
            # pre-run state for pre-existing output MODs.
            return True, "已同步到 MO2 MOD：{}".format(name)
        except Exception as e:
            logger.exception("LOD output copy failed")
            return False, "复制输出失败：{}".format(e)

    def open_settings(self):
        dlg = SettingsDialog(self, self.organizer, self.configs, self.detection_mode)
        if dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec():
            self.configs = dlg.configs
            self.detection_mode = dlg.mode
            self.store.save_config(self.detection_mode, self.configs, self.quality_preset, self.shader_mode, self.auto_quality)
            idx = self.mode_combo.findData(self.detection_mode)
            if idx >= 0: self.mode_combo.setCurrentIndex(idx)
            self._append("流水线设置已保存。")
            self.analyze_changes()


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent, organizer, configs: Dict[str, StageConfig], mode: str):
        super().__init__(parent)
        self.organizer = organizer
        self.configs = {k: StageConfig(**vars(v)) for k, v in configs.items()}
        self.mode = mode
        self.widgets = {}
        self.setWindowTitle("LOD 生成设置")
        self.resize(900, 700)
        layout = QtWidgets.QVBoxLayout(self)
        help_label = QtWidgets.QLabel(
            "草缓存无需额外设置程序路径或输出目录。xLODGen、TexGen 和 DynDOLOD 可在下方分别配置。\n"
            "每个阶段运行前需要启用或禁用的 MOD，可以直接从模组列表或 MOD 组中选择。"
        )
        help_label.setWordWrap(True); layout.addWidget(help_label)
        tabs = QtWidgets.QTabWidget(); layout.addWidget(tabs, 1)
        for stage in STAGES:
            tabs.addTab(self._stage_tab(stage), STAGE_LABELS[stage])
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _line_with_browse(self, initial: str, directory: bool = False):
        w = QtWidgets.QWidget(); l = QtWidgets.QHBoxLayout(w); l.setContentsMargins(0,0,0,0)
        edit = QtWidgets.QLineEdit(initial); btn = QtWidgets.QPushButton("浏览")
        def browse():
            if directory:
                p = QtWidgets.QFileDialog.getExistingDirectory(self, "选择目录", edit.text() or os.path.expanduser("~"))
            else:
                p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择程序", edit.text() or os.path.expanduser("~"), "Executable (*.exe);;All files (*)")
            if p: edit.setText(p)
        btn.clicked.connect(browse); l.addWidget(edit, 1); l.addWidget(btn)
        return w, edit

    def _mod_picker(self, initial: str, title: str):
        w = QtWidgets.QWidget()
        l = QtWidgets.QHBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        edit = QtWidgets.QLineEdit(initial)
        edit.setPlaceholderText("可手动输入，也可从列表选择")
        btn = QtWidgets.QPushButton("从模组列表选择…")

        def choose():
            current = [x.strip() for x in re.split(r"[;\n]+", edit.text()) if x.strip()]
            selected = mod_manager.choose_mods(self, self.organizer, current, title)
            if selected is not None:
                edit.setText(";".join(selected))

        btn.clicked.connect(choose)
        l.addWidget(edit, 1)
        l.addWidget(btn)
        return w, edit

    def _group_picker(self, initial: str, title: str):
        w = QtWidgets.QWidget()
        l = QtWidgets.QHBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        edit = QtWidgets.QLineEdit(initial)
        edit.setPlaceholderText("选择 MOD 组")
        btn = QtWidgets.QPushButton("选择 MOD 组…")

        def choose():
            current = [x.strip() for x in re.split(r"[;\n]+", edit.text()) if x.strip()]
            selected = mod_manager.choose_mod_groups(self, self.organizer, current, title)
            if selected is not None:
                edit.setText(";".join(selected))

        btn.clicked.connect(choose)
        l.addWidget(edit, 1)
        l.addWidget(btn)
        return w, edit

    def _stage_tab(self, stage: str):
        c = self.configs[stage]
        w = QtWidgets.QWidget(); form = QtWidgets.QFormLayout(w)

        if stage == "grass":
            built_in = QtWidgets.QLabel(
                "草缓存已内置，无需设置程序路径或输出目录。"
                "星黎会自动启动游戏并在需要时继续生成。\n"
                "完成后会自动整理到输出 MOD。"
            )
            built_in.setWordWrap(True)
            built_in.setStyleSheet("QLabel{background:#e8f5e9;border:1px solid #81c784;padding:8px;border-radius:4px;}")
            form.addRow("草缓存:", built_in)
            exe = QtWidgets.QLineEdit(""); args = QtWidgets.QLineEdit(""); cwd = QtWidgets.QLineEdit("")
            out = QtWidgets.QLineEdit(""); log_dir = QtWidgets.QLineEdit(""); log_pat = QtWidgets.QLineEdit(""); marker = QtWidgets.QLineEdit("")
        else:
            exe_w, exe = self._line_with_browse(c.exe, False); form.addRow("程序:", exe_w)
            args = QtWidgets.QLineEdit(c.args); form.addRow("启动参数:", args)
            cwd_w, cwd = self._line_with_browse(c.cwd, True); form.addRow("工作目录:", cwd_w)
            out_w, out = self._line_with_browse(c.output_source, True); form.addRow("输出目录:", out_w)
            log_w, log_dir = self._line_with_browse(c.log_dir, True); form.addRow("日志目录:", log_w)
            log_pat = QtWidgets.QLineEdit(c.log_pattern); form.addRow("日志文件名称规则:", log_pat)
            marker = QtWidgets.QLineEdit(c.success_marker); form.addRow("完成标记:", marker)

        out_mod = QtWidgets.QLineEdit(c.output_mod); form.addRow("保存到 MO2 输出 MOD:", out_mod)
        en_w, en = self._mod_picker(c.enable_mods, "{}：正常运行前启用 MOD".format(STAGE_LABELS[stage])); form.addRow("运行前启用 MOD:", en_w)
        eng_w, eng = self._group_picker(c.enable_groups, "{}：正常运行前启用 MOD 组".format(STAGE_LABELS[stage])); form.addRow("运行前启用 MOD 组:", eng_w)
        dis_w, dis = self._mod_picker(c.disable_mods, "{}：正常运行前禁用 MOD".format(STAGE_LABELS[stage])); form.addRow("运行前禁用 MOD:", dis_w)
        disg_w, disg = self._group_picker(c.disable_groups, "{}：正常运行前禁用 MOD 组".format(STAGE_LABELS[stage])); form.addRow("运行前禁用 MOD 组:", disg_w)
        ren_w, ren = self._mod_picker(c.retry_enable_mods, "{}：失败重试启用 MOD".format(STAGE_LABELS[stage])); form.addRow("备用设置：启用 MOD:", ren_w)
        reng_w, reng = self._group_picker(c.retry_enable_groups, "{}：失败重试启用 MOD 组".format(STAGE_LABELS[stage])); form.addRow("备用设置：启用 MOD 组:", reng_w)
        rdis_w, rdis = self._mod_picker(c.retry_disable_mods, "{}：失败重试禁用 MOD".format(STAGE_LABELS[stage])); form.addRow("备用设置：禁用 MOD:", rdis_w)
        rdisg_w, rdisg = self._group_picker(c.retry_disable_groups, "{}：失败重试禁用 MOD 组".format(STAGE_LABELS[stage])); form.addRow("备用设置：禁用 MOD 组:", rdisg_w)
        retry = QtWidgets.QCheckBox("首次运行失败时使用备用 MOD 设置重试一次"); retry.setChecked(c.auto_retry); form.addRow("", retry)
        copy_out = QtWidgets.QCheckBox("生成成功后自动复制到 MO2 输出 MOD"); copy_out.setChecked(True if stage == "grass" else c.copy_output); form.addRow("", copy_out)
        if stage == "grass":
            copy_out.setEnabled(False)
        clear = QtWidgets.QCheckBox("复制后清空临时输出目录"); clear.setChecked(False if stage == "grass" else c.clear_output_source_after_copy)
        if stage != "grass":
            form.addRow("", clear)
        self.widgets[stage] = (exe,args,cwd,out,out_mod,log_dir,log_pat,marker,en,eng,dis,disg,ren,reng,rdis,rdisg,retry,copy_out,clear)
        return w

    def _save(self):
        for stage, ws in self.widgets.items():
            c = self.configs[stage]
            (exe,args,cwd,out,out_mod,log_dir,log_pat,marker,en,eng,dis,disg,ren,reng,rdis,rdisg,retry,copy_out,clear) = ws
            if stage == "grass":
                c.exe=""; c.args=""; c.cwd=""; c.output_source=""; c.log_dir=""; c.log_pattern=""; c.success_marker=""
            else:
                c.exe=exe.text().strip(); c.args=args.text().strip(); c.cwd=cwd.text().strip(); c.output_source=out.text().strip()
                c.log_dir=log_dir.text().strip(); c.log_pattern=log_pat.text().strip(); c.success_marker=marker.text().strip()
            c.output_mod=out_mod.text().strip()
            c.enable_mods=en.text().strip(); c.enable_groups=eng.text().strip(); c.disable_mods=dis.text().strip(); c.disable_groups=disg.text().strip(); c.retry_enable_mods=ren.text().strip(); c.retry_enable_groups=reng.text().strip(); c.retry_disable_mods=rdis.text().strip(); c.retry_disable_groups=rdisg.text().strip()
            c.auto_retry=retry.isChecked(); c.copy_output=True if stage == "grass" else copy_out.isChecked(); c.clear_output_source_after_copy=False if stage == "grass" else clear.isChecked()

        group_store = ModGroupStore(self.organizer)
        known_groups = set(group_store.list_names())
        missing = []
        for stage, c in self.configs.items():
            for field_name, label in (("enable_groups", "启用组"), ("disable_groups", "禁用组"), ("retry_enable_groups", "重试启用组"), ("retry_disable_groups", "重试禁用组")):
                for name in [x.strip() for x in re.split(r"[;,\n]+", getattr(c, field_name, "") or "") if x.strip()]:
                    if name not in known_groups:
                        missing.append("{} / {}：{}".format(STAGE_LABELS[stage], label, name))
        if missing:
            QtWidgets.QMessageBox.warning(self, "LOD MOD 组配置", "以下 MOD 组不存在，请先在模组管理中心创建或修正名称：\n\n{}".format("\n".join(missing[:30])))
            return
        self.accept()


def show_lod_pipeline(parent, organizer, plugin_dir: str):
    dlg = PipelineDialog(parent, organizer, plugin_dir)
    if hasattr(dlg, "exec_"):
        return dlg.exec_()
    return dlg.exec()
