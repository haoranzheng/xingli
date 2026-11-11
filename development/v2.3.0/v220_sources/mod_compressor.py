# -*- coding: utf-8 -*-
"""
Mod Compressor - 星黎整合小工具内置模组压缩模块
兼容目标：MO2 2.4.4 (Python 3.8 / PyQt5)，并保留 MO2 2.5.x / Qt6 兼容
集成版本 4.1 - 新手/高级双模式
"""

import os
import sys
import json
import time
import copy
import subprocess
import platform
import re
import datetime as dt
from typing import Optional, Tuple, List, Dict, Any, Set
from dataclasses import dataclass, field
from enum import IntEnum, auto


from .mod_state_service import ModStateService
from .stability import get_logger

logger = get_logger("mod_compressor")


def _prime_windows_dll_search_path():
    """
    辅助嵌入式Python构建查找可选的运行时DLL（如libffi）
    在旧版Python或非Windows平台上为空操作
    """
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return []

    module = sys.modules.get(__name__)
    module_file = globals().get("__file__", "") or getattr(module, "__file__", "")
    module_spec = globals().get("__spec__", None)
    module_origin = getattr(module_spec, "origin", "") if module_spec is not None else ""

    seeds = [
        module_file,
        module_origin,
        getattr(sys, "executable", ""),
        getattr(sys, "_base_executable", ""),
        getattr(sys, "prefix", ""),
        getattr(sys, "base_prefix", ""),
        getattr(sys, "exec_prefix", ""),
    ]

    handles = []
    seen = set()
    suffixes = (
        "",
        "DLLs",
        "dlls",
        "Python",
        os.path.join("Python", "DLLs"),
        "python",
        os.path.join("python", "DLLs"),
    )

    for seed in seeds:
        seed = str(seed or "").strip()
        if not seed:
            continue

        seed_dir = seed if os.path.isdir(seed) else os.path.dirname(seed)
        if not seed_dir:
            continue

        for suffix in suffixes:
            candidate = os.path.normpath(os.path.join(seed_dir, suffix))
            key = candidate.casefold()
            if key in seen or not os.path.isdir(candidate):
                continue

            seen.add(key)
            try:
                handles.append(os.add_dll_directory(candidate))
            except (FileNotFoundError, OSError):
                pass

    return handles


_DLL_DIRECTORY_HANDLES = _prime_windows_dll_search_path()

CTYPES_IMPORT_ERROR = ""
try:
    import ctypes
    from ctypes import wintypes
except ImportError as exc:
    ctypes = None
    wintypes = None
    CTYPES_IMPORT_ERROR = str(exc)

import mobase

# ---- Qt compatibility: MO2 2.4.4(PyQt5) / 2.5.x(PyQt6) ----
QT6 = False
try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    from PyQt6.QtCore import Qt
    QT6 = True
except ImportError:
    from PyQt5 import QtCore, QtGui, QtWidgets
    from PyQt5.QtCore import Qt


# ---- Qt compatibility ----
if QT6:
    CHECKED = Qt.CheckState.Checked
    UNCHECKED = Qt.CheckState.Unchecked
    SELECT_ROWS = QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
    NO_EDIT = QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
    INTERACTIVE = QtWidgets.QHeaderView.ResizeMode.Interactive
    RESIZE_TO_CONTENTS = QtWidgets.QHeaderView.ResizeMode.ResizeToContents
    STRETCH = QtWidgets.QHeaderView.ResizeMode.Stretch
    YES_BTN = QtWidgets.QMessageBox.StandardButton.Yes
    QUEUED_CONNECTION = Qt.ConnectionType.QueuedConnection
    USER_ROLE = Qt.ItemDataRole.UserRole
    ALIGN_CENTER = Qt.AlignmentFlag.AlignCenter
    ALIGN_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
else:
    CHECKED = Qt.Checked
    UNCHECKED = Qt.Unchecked
    SELECT_ROWS = QtWidgets.QAbstractItemView.SelectRows
    NO_EDIT = QtWidgets.QAbstractItemView.NoEditTriggers
    INTERACTIVE = QtWidgets.QHeaderView.Interactive
    RESIZE_TO_CONTENTS = QtWidgets.QHeaderView.ResizeToContents
    STRETCH = QtWidgets.QHeaderView.Stretch
    YES_BTN = QtWidgets.QMessageBox.Yes
    QUEUED_CONNECTION = Qt.QueuedConnection
    USER_ROLE = Qt.UserRole
    ALIGN_CENTER = Qt.AlignCenter
    ALIGN_RIGHT = Qt.AlignRight | Qt.AlignVCenter

MOD_NAME_ROLE = int(USER_ROLE)
SORT_ROLE = MOD_NAME_ROLE + 1


# ---- ModState check (centralized) ----
MOD_STATE_ACTIVE = ModStateService.resolve_flag("ACTIVE")


# ---- Constants ----
class TargetType(IntEnum):
    TEXTURES = auto()
    MESHES = auto()
    SOUNDS = auto()
    LOD = auto()
    ANIMATIONS = auto()
    ALL = auto()


TARGET_NAMES = {
    TargetType.TEXTURES: "纹理",
    TargetType.MESHES: "模型",
    TargetType.SOUNDS: "音频",
    TargetType.LOD: "LOD/DynDOLOD",
    TargetType.ANIMATIONS: "动画",
    TargetType.ALL: "整个模组",
}

# Directories to search for each target type
# Case-insensitive matching will be used
TARGET_DIRS = {
    TargetType.TEXTURES: [
        "textures",
    ],
    TargetType.MESHES: [
        "meshes",
    ],
    TargetType.SOUNDS: [
        "sound",
        "sounds",
        "music",
    ],
    TargetType.LOD: [
        "dyndolod",
        "lod",
        "terrain",
        "grass",
        "xlodgen",
        "texgen",
        "texgen_output",
        "dyndolod_output",
        "lodgen",
        "lodgen_output",
        "occlusion",
        "billboards",
    ],
    TargetType.ANIMATIONS: [
        "animations",
        "meshes/actors",
        "skse/plugins/oar",
        "skse/plugins/dar",
        "nemesis_engine",
        "pandora_output",
    ],
}

LOD_NESTED_PATHS = [
    "textures/terrain",
    "textures/lod",
    "textures/actors",
    "meshes/terrain",
    "meshes/lod",
    "lodsettings",
    "grass",
    "billboards",
]

# 已压缩或无需压缩的扩展名
IGNORE_EXTENSIONS: Set[str] = {
    # Archives (already compressed)
    ".bsa", ".ba2", ".zip", ".7z", ".rar", ".gz", ".xz", ".bz2",
    # Plugin files (small, critical)
    ".esp", ".esm", ".esl",
    # MO2 metadata
    ".fomod", ".meta", ".mohidden",
    # Config and docs
    ".txt", ".json", ".ini", ".cfg", ".xml", ".yaml", ".yml",
    ".md", ".log", ".pdf", ".doc", ".docx", ".rtf",
    # Images (already compressed)
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    # Executables (usually already compressed)
    ".exe", ".dll",
    # Source code
    ".psc", ".py", ".lua", ".js",
}

# 压缩效果好的扩展名
COMPRESSIBLE_EXTENSIONS: Set[str] = {
    # Textures
    ".dds", ".tga", ".bmp",
    # Meshes
    ".nif", ".btr", ".bto", ".tri",
    # LOD
    ".lod", ".lst", ".btd", ".fuz",
    # Audio (uncompressed)
    ".wav", ".xwm", ".lip",
    # Animations
    ".hkx", ".kf",
    # Other
    ".pex", ".seq", ".swf",
}

# 最小扫描文件大小（字节）
# LOD 文件通常较小，因此降低阈值
MIN_FILE_SIZE = 256

# 纳入压缩考虑的最小文件大小
MIN_COMPRESS_SIZE = 1024

# ---- 文件属性 WinAPI 常量 ----
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
INVALID_FILE_SIZE = 0xFFFFFFFF
FILE_ATTRIBUTE_COMPRESSED = 0x800
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_SPARSE_FILE = 0x200
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
IO_REPARSE_TAG_WOF = 0x80000017

COMPRESSION_ALGORITHMS = {
    "xpress4k": "XPRESS4K（最快，~1.5-2倍）",
    "xpress8k": "XPRESS8K（均衡，~2-2.5倍）",
    "xpress16k": "XPRESS16K（较好，~2.5-3倍）",
    "lzx": "LZX（最大压缩，~3-4倍）",
}

UNMANAGED_PREFIX = "未托管: "

# ---- 游戏检测 ----
SUPPORTED_GAME_TOKENS = [
    "skyrimse", "skyrimspecialedition", "skyrimvr",
    "fallout4", "fallout4vr",
    "fallout3", "fallout3vr",
    "falloutnv", "falloutnewvegas",
    "oblivion",
    "starfield",
    "enderal",
    "enderalse",
    "nehrimese",
]

LOD_PATH_INDICATORS = [
    '/lod/', '\\lod\\',
    '/dyndolod/', '\\dyndolod\\',
    '/terrain/', '\\terrain\\',
    '/grass/', '\\grass\\',
    '/xlodgen/', '\\xlodgen\\',
    '/texgen/', '\\texgen\\',
    '/billboards/', '\\billboards\\',
    '/occlusion/', '\\occlusion\\',
    '/lodsettings/', '\\lodsettings\\',
    '/visinfo/', '\\visinfo\\',
    '/vis/', '\\vis\\',
]

_current_game_token: str = ""


def _normalize_token(token: str) -> str:
    return token.replace("-", "").replace("_", "").replace(" ", "").lower()


def _detect_game(organizer) -> str:
    global _current_game_token
    try:
        game_name = str(organizer.managedGame().gameName()).lower()
        game_short = str(organizer.managedGame().gameShortName()).lower()
    except Exception:
        game_name = ""
        game_short = ""

    for token in SUPPORTED_GAME_TOKENS:
        nt = _normalize_token(token)
        if nt in _normalize_token(game_name) or nt in _normalize_token(game_short):
            _current_game_token = token
            return token

    _current_game_token = "generic"
    return "generic"


def _get_game_name(organizer) -> str:
    try:
        return str(organizer.managedGame().gameName())
    except Exception:
        return "Game"

# ---- WinAPI ----
kernel32 = None
GetCompressedFileSizeW = None
GetFileAttributesW = None

if ctypes is not None and wintypes is not None:
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        GetCompressedFileSizeW = kernel32.GetCompressedFileSizeW
        GetCompressedFileSizeW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        GetCompressedFileSizeW.restype = wintypes.DWORD

        GetFileAttributesW = kernel32.GetFileAttributesW
        GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
        GetFileAttributesW.restype = wintypes.DWORD
    except Exception as exc:
        CTYPES_IMPORT_ERROR = CTYPES_IMPORT_ERROR or f"{type(exc).__name__}: {exc}"
        kernel32 = None
        GetCompressedFileSizeW = None
        GetFileAttributesW = None


# ---- 数据类 ----
@dataclass
class ModInfo:
    file_count: int = 0
    total_size: int = 0
    disk_size: int = 0
    last_mtime: float = 0.0
    compressed: bool = False
    ratio: float = 1.0
    algorithm: str = ""
    compressed_at: str = ""
    scanned_at: str = ""
    # 各类别大小明细
    texture_size: int = 0
    mesh_size: int = 0
    lod_size: int = 0
    other_size: int = 0
    # 调试信息
    compressed_by_attr: int = 0
    compressed_by_size: int = 0
    compressed_by_wof: int = 0
    skipped_files: int = 0
    
    def to_dict(self) -> dict:
        return {
            "file_count": self.file_count,
            "total_size": self.total_size,
            "disk_size": self.disk_size,
            "last_mtime": self.last_mtime,
            "compressed": self.compressed,
            "ratio": self.ratio,
            "algorithm": self.algorithm,
            "compressed_at": self.compressed_at,
            "scanned_at": self.scanned_at,
            "texture_size": self.texture_size,
            "mesh_size": self.mesh_size,
            "lod_size": self.lod_size,
            "other_size": self.other_size,
            "compressed_by_wof": self.compressed_by_wof,
            "skipped_files": self.skipped_files,
        }
    
    @classmethod
    def from_dict(cls, d: dict) -> 'ModInfo':
        info = cls(
            file_count=d.get("file_count", 0),
            total_size=d.get("total_size", 0),
            disk_size=d.get("disk_size", 0),
            last_mtime=d.get("last_mtime", 0.0),
            compressed=d.get("compressed", False),
            ratio=d.get("ratio", 1.0),
            algorithm=d.get("algorithm", ""),
            compressed_at=d.get("compressed_at", ""),
            scanned_at=d.get("scanned_at", ""),
            texture_size=d.get("texture_size", 0),
            mesh_size=d.get("mesh_size", 0),
            lod_size=d.get("lod_size", 0),
            other_size=d.get("other_size", 0),
        )
        info.compressed_by_wof = d.get("compressed_by_wof", 0)
        info.skipped_files = d.get("skipped_files", 0)
        return info


@dataclass
class ModWorkload:
    root: str = ""
    operation_items: List[str] = field(default_factory=list)
    scan_files: List[str] = field(default_factory=list)
    is_foreign: bool = False


class SortableTableWidgetItem(QtWidgets.QTableWidgetItem):
    def __lt__(self, other):
        if not isinstance(other, QtWidgets.QTableWidgetItem):
            return super().__lt__(other)

        left = self.data(SORT_ROLE)
        right = other.data(SORT_ROLE)
        if left is None or right is None:
            return self.text().casefold() < other.text().casefold()

        try:
            return left < right
        except TypeError:
            return str(left).casefold() < str(right).casefold()


# ---- 辅助函数 ----
def _now_iso() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _format_size(size: int) -> str:
    if size < 0:
        return "—"
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _check_windows() -> Tuple[bool, str]:
    if platform.system() != "Windows":
        return False, "需要Windows系统"
    try:
        major = int(platform.version().split('.')[0])
        if major < 10:
            return False, "需要Windows 10或更高版本"
    except:
        pass
    return True, ""


def _filetree_continue():
    try:
        return mobase.IFileTree.WalkReturn.CONTINUE
    except AttributeError:
        return mobase.IFileTree.CONTINUE


def _normalize_tree_rel_path(rel_path: str) -> str:
    path = str(rel_path).replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    if path.lower().startswith("data/"):
        path = path[5:]
    return path


def _iter_mod_lookup_names(name: str) -> List[str]:
    names = [name]
    if name.startswith(UNMANAGED_PREFIX):
        stripped = name[len(UNMANAGED_PREFIX):].strip()
        if stripped and stripped not in names:
            names.append(stripped)
    return names


def _resolve_mod(organizer, name: str):
    mod_list = None
    try:
        mod_list = organizer.modList()
    except:
        pass

    for candidate in _iter_mod_lookup_names(name):
        if mod_list and hasattr(mod_list, "getMod"):
            try:
                mod = mod_list.getMod(candidate)
                if mod:
                    return mod
            except:
                pass

        if hasattr(organizer, "getMod"):
            try:
                mod = organizer.getMod(candidate)
                if mod:
                    return mod
            except:
                pass

    return None


def _get_game_data_path(organizer) -> str:
    try:
        game = organizer.managedGame()
    except:
        return ""

    if not game or not hasattr(game, "dataDirectory"):
        return ""

    try:
        data_dir = game.dataDirectory()
    except:
        return ""

    for attr in ("absolutePath", "path"):
        if not hasattr(data_dir, attr):
            continue
        try:
            path = getattr(data_dir, attr)()
        except TypeError:
            path = getattr(data_dir, attr)
        except:
            continue

        if path:
            return os.path.normpath(str(path))

    return ""


def _is_foreign_mod(mod, name: str) -> bool:
    if name.startswith(UNMANAGED_PREFIX):
        return True

    if mod and hasattr(mod, "isForeign"):
        try:
            return bool(mod.isForeign())
        except:
            pass

    return False


def _get_mod_path_from_organizer(organizer, name: str) -> str:
    mod = _resolve_mod(organizer, name)
    if not mod:
        return ""

    for attr in ("absolutePath", "path"):
        if not hasattr(mod, attr):
            continue
        try:
            p = getattr(mod, attr)()
        except TypeError:
            p = getattr(mod, attr)
        except:
            continue

        if p:
            return os.path.normpath(str(p))

    if _is_foreign_mod(mod, name):
        return _get_game_data_path(organizer)

    return ""


def _normalize_origin_name(name: str) -> str:
    text = str(name).strip()
    if text.startswith(UNMANAGED_PREFIX):
        text = text[len(UNMANAGED_PREFIX):].strip()
    return text.casefold()


def _get_file_origins(organizer, rel_path: str) -> List[str]:
    if not hasattr(organizer, "getFileOrigins"):
        return []

    try:
        origins = organizer.getFileOrigins(_normalize_tree_rel_path(rel_path))
    except:
        return []

    if origins is None:
        return []

    try:
        return [str(origin) for origin in origins if origin is not None]
    except TypeError:
        return [str(origins)] if origins else []


def _origin_matches_mod(origins: List[str], mod_name: str) -> bool:
    if not origins:
        return False

    wanted = {
        _normalize_origin_name(candidate)
        for candidate in _iter_mod_lookup_names(mod_name)
    }
    wanted.discard("")
    if not wanted:
        return False

    for origin in origins:
        if _normalize_origin_name(origin) in wanted:
            return True

    return False


def _get_foreign_scan_roots(base_root: str, targets: List[TargetType]) -> List[str]:
    if TargetType.ALL in targets:
        return [base_root]

    roots: List[str] = []
    seen: Set[str] = set()
    rel_roots: List[str] = []

    for target in targets:
        rel_roots.extend(TARGET_DIRS.get(target, []))

    if TargetType.LOD in targets:
        rel_roots.extend(LOD_NESTED_PATHS)

    for rel_root in rel_roots:
        full_root = os.path.normpath(os.path.join(base_root, rel_root))
        key = full_root.casefold()
        if key in seen or not os.path.isdir(full_root):
            continue
        seen.add(key)
        roots.append(full_root)

    return roots


def _collect_foreign_mod_file_paths(
    organizer, mod_name: str, base_root: str, targets: List[TargetType]
) -> List[str]:
    if not base_root or not os.path.isdir(base_root):
        return []

    files: List[str] = []
    seen: Set[str] = set()
    scan_roots = _get_foreign_scan_roots(base_root, targets)
    if not scan_roots:
        return []

    for scan_root in scan_roots:
        try:
            for root, dirs, filenames in os.walk(scan_root, followlinks=False):
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith('.') and not _is_symlink_or_junction(os.path.join(root, d))
                ]

                for filename in filenames:
                    full_path = os.path.join(root, filename)
                    key = full_path.casefold()
                    if key in seen or not os.path.isfile(full_path):
                        continue

                    try:
                        rel_path = os.path.relpath(full_path, base_root)
                    except ValueError:
                        continue

                    origins = _get_file_origins(organizer, rel_path)
                    if not _origin_matches_mod(origins, mod_name):
                        continue

                    seen.add(key)
                    files.append(full_path)
        except PermissionError:
            continue

    return files


def _matches_target_path(rel_path: str, targets: List[TargetType]) -> bool:
    rel_norm = _normalize_tree_rel_path(rel_path).casefold()
    if not rel_norm:
        return False

    if TargetType.ALL in targets:
        return True

    prefixes = set()
    for target in targets:
        for target_dir in TARGET_DIRS.get(target, []):
            prefixes.add(target_dir.casefold().strip("/"))

    if TargetType.LOD in targets:
        for nested_path in LOD_NESTED_PATHS:
            prefixes.add(nested_path.casefold().strip("/"))

    for prefix in prefixes:
        if rel_norm == prefix or rel_norm.startswith(prefix + "/"):
            return True

    return False


def _filter_file_paths_for_targets(
    file_paths: List[str], base_root: str, targets: List[TargetType]
) -> List[str]:
    if TargetType.ALL in targets:
        return list(file_paths)

    result: List[str] = []
    seen: Set[str] = set()

    for full_path in file_paths:
        try:
            rel_path = os.path.relpath(full_path, base_root)
        except ValueError:
            rel_path = os.path.basename(full_path)

        if not _matches_target_path(rel_path, targets):
            continue

        key = full_path.casefold()
        if key not in seen:
            seen.add(key)
            result.append(full_path)

    return result


def _resolve_mod_workload(
    organizer, name: str, targets: Optional[List[TargetType]] = None
) -> ModWorkload:
    targets = list(targets) if targets else [TargetType.ALL]
    mod = _resolve_mod(organizer, name)
    root = _get_mod_path_from_organizer(organizer, name)
    if not mod or not root:
        return ModWorkload(root=root)

    is_foreign = _is_foreign_mod(mod, name)
    if not is_foreign:
        return ModWorkload(
            root=root,
            operation_items=_find_target_dirs(root, targets),
            is_foreign=False,
        )

    data_root = _get_game_data_path(organizer) or root
    scan_files = _collect_foreign_mod_file_paths(organizer, name, data_root, [TargetType.ALL])
    return ModWorkload(
        root=data_root,
        operation_items=_collect_foreign_mod_file_paths(organizer, name, data_root, targets),
        scan_files=scan_files,
        is_foreign=True,
    )


def _describe_work_item(path: str, root: str) -> str:
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        rel = os.path.basename(path)

    if rel == ".":
        return "(entire mod)"
    return rel


def _has_winapi_physical_size() -> bool:
    return (
        ctypes is not None and
        wintypes is not None and
        GetCompressedFileSizeW is not None
    )


def _extract_stat_file_attributes(stat_info) -> int:
    try:
        return int(getattr(stat_info, "st_file_attributes"))
    except (AttributeError, TypeError, ValueError):
        return INVALID_FILE_ATTRIBUTES


def _extract_stat_reparse_tag(stat_info) -> int:
    try:
        return int(getattr(stat_info, "st_reparse_tag", 0))
    except (TypeError, ValueError):
        return 0


def _stat_no_follow(path: str):
    try:
        return os.stat(path, follow_symlinks=False)
    except TypeError:
        try:
            return os.lstat(path)
        except (AttributeError, OSError):
            return os.stat(path)
    except OSError:
        return None


def _get_physical_size(filepath: str) -> int:
    """获取文件实际占用的磁盘空间"""
    if not _has_winapi_physical_size():
        return -1

    try:
        high = wintypes.DWORD(0)
        low = GetCompressedFileSizeW(filepath, ctypes.byref(high))
        if low == INVALID_FILE_SIZE and ctypes.get_last_error() != 0:
            return -1
        return (high.value << 32) | low
    except:
        return -1


def _has_ntfs_compressed_attr(filepath: str, stat_info=None) -> bool:
    """检查文件是否具有 NTFS 压缩属性"""
    attrs = _extract_stat_file_attributes(stat_info)
    if attrs != INVALID_FILE_ATTRIBUTES:
        return bool(attrs & FILE_ATTRIBUTE_COMPRESSED)

    try:
        stat_info = os.stat(filepath)
        attrs = _extract_stat_file_attributes(stat_info)
        if attrs != INVALID_FILE_ATTRIBUTES:
            return bool(attrs & FILE_ATTRIBUTE_COMPRESSED)
    except:
        pass

    if GetFileAttributesW is None:
        return False

    try:
        attr = GetFileAttributesW(filepath)
        if attr == INVALID_FILE_ATTRIBUTES:
            return False
        return bool(attr & FILE_ATTRIBUTE_COMPRESSED)
    except:
        return False


def _is_wof_compressed(filepath: str, stat_info=None, physical_size: int = -1) -> bool:
    """检查文件是否具有 WOF 压缩（重解析点）或幽灵压缩"""
    try:
        if stat_info is None:
            stat_info = os.stat(filepath)

        # Check if it is a reparse point
        attrs = _extract_stat_file_attributes(stat_info)
        if attrs != INVALID_FILE_ATTRIBUTES:
            if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
                tag = _extract_stat_reparse_tag(stat_info)
                if tag == IO_REPARSE_TAG_WOF:
                    return True

            # Skip if NTFS compressed or sparse
            if attrs & (FILE_ATTRIBUTE_COMPRESSED | FILE_ATTRIBUTE_SPARSE_FILE):
                return False

        # Fallback: Check physical vs logical size
        logical = stat_info.st_size
        if logical > 1024:
            if physical_size == -1:
                physical_size = _get_physical_size(filepath)

            if physical_size != -1 and physical_size < logical * 0.9:
                return True

    except:
        pass
    return False


def _is_symlink_or_junction(path: str) -> bool:
    """检查路径是否为符号链接或交接点"""
    try:
        if os.path.islink(path):
            return True
        if hasattr(os.path, "isjunction") and os.path.isjunction(path):
            return True

        stat_info = _stat_no_follow(path)
        attrs = _extract_stat_file_attributes(stat_info)
        if attrs != INVALID_FILE_ATTRIBUTES:
            return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)

        if GetFileAttributesW is not None:
            attr = GetFileAttributesW(path)
            if attr != INVALID_FILE_ATTRIBUTES:
                return bool(attr & FILE_ATTRIBUTE_REPARSE_POINT)
    except:
        pass
    return False


def _get_file_category(filepath: str, ext: str) -> str:
    """根据路径和扩展名确定文件类别"""
    path_lower = filepath.lower()
    filename = os.path.basename(filepath).lower()

    # Check for LOD indicators in path (game-aware)
    for indicator in LOD_PATH_INDICATORS:
        if indicator in path_lower:
            return "lod"

    # Check for LOD filename patterns
    # DynDOLOD/xLODGen: tamriel.4.0.0.dds, tamriel.32.0.0.dds
    # Pattern: worldspace.level.x.y.extension
    lod_filename_patterns = [
        r'^\w+\.\d+\.\-?\d+\.\-?\d+\.',  # tamriel.4.0.0.dds
        r'_lod\d*\.',                      # tree_lod.nif, tree_lod0.dds
        r'_far\.',                         # mesh_far.nif
        r'\.lod$',                         # something.lod
    ]

    import re
    for pattern in lod_filename_patterns:
        if re.search(pattern, filename):
            return "lod"

    # Check extension
    if ext in {'.dds', '.tga', '.bmp'}:
        return "texture"
    elif ext in {'.nif', '.btr', '.bto', '.tri'}:
        return "mesh"
    elif ext in {'.lod', '.lst', '.btd', '.uvd'}:
        return "lod"

    return "other"


def _decode_compact_output(data: bytes) -> str:
    for enc in ("cp866", "cp1251", "utf-8"):
        try:
            return data.decode(enc)
        except:
            pass
    return data.decode("utf-8", errors="replace")


def _parse_compact_ratio(output: str) -> float:
    ratio_patterns = [
        r'(\d+[.,]\d+)\s*(?:to|:|\s)\s*1',
        r'(\d+[.,]\d+)\s*Đş\s*1',
    ]

    for pattern in ratio_patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1).replace(',', '.'))
            except:
                pass

    return 1.0


def _parse_compact_storage_sizes(output: str) -> Tuple[int, int]:
    best_pair = (0, 0)
    best_sum = 0

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "ratio" in line.casefold():
            continue

        numbers = []
        for token in re.findall(r"\d[\d\s\u00A0.,]*\d|\d", line):
            digits = re.sub(r"\D", "", token)
            if not digits:
                continue
            try:
                numbers.append(int(digits))
            except ValueError:
                continue

        if len(numbers) < 2:
            continue

        for idx in range(len(numbers) - 1):
            left = numbers[idx]
            right = numbers[idx + 1]
            pair_sum = left + right
            if pair_sum > best_sum:
                best_sum = pair_sum
                best_pair = (left, right)

    logical = max(best_pair)
    physical = min(best_pair)
    if logical <= 0 or physical <= 0:
        return 0, 0
    return logical, physical


def _run_compact_query_legacy(path: str) -> Tuple[bool, float, str]:
    """运行compact.exe检查压缩状态（旧版）"""
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        
        flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags |= subprocess.CREATE_NO_WINDOW
        
        args = ["compact.exe", "/q"]
        if os.path.isdir(path):
            args.append(f"/s:{path}")
        else:
            args.append(path)
        
        p = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=si,
            creationflags=flags
        )
        stdout, stderr = p.communicate(timeout=60)
        
        out = _decode_compact_output(stdout)
        
        if p.returncode != 0:
            return False, 1.0, out
        
        ratio = 1.0
        is_compressed = False
        
        ratio_patterns = [
            r'(\d+[.,]\d+)\s*(?:to|:|\s)\s*1',
            r'(\d+[.,]\d+)\s*к\s*1',
        ]
        
        for pattern in ratio_patterns:
            match = re.search(pattern, out, re.IGNORECASE)
            if match:
                try:
                    ratio_str = match.group(1).replace(',', '.')
                    ratio = float(ratio_str)
                    if ratio > 1.0:
                        is_compressed = True
                    break
                except:
                    pass
        
        compressed_lines = len(re.findall(r'^\s*C\s+', out, re.MULTILINE))
        if compressed_lines > 0:
            is_compressed = True
        
        return is_compressed, ratio, out
        
    except Exception as e:
        return False, 1.0, str(e)


def _iter_compact_query_batches(file_paths: List[str]):
    max_chars = 28000
    base_chars = len("compact.exe /q")
    batch: List[str] = []
    total_chars = base_chars

    for path in file_paths:
        if not path:
            continue

        path = os.path.normpath(path)
        path_chars = len(path) + 3

        if batch and total_chars + path_chars > max_chars:
            yield batch
            batch = [path]
            total_chars = base_chars + path_chars
            continue

        batch.append(path)
        total_chars += path_chars

    if batch:
        yield batch


def _run_compact_query_files(file_paths: List[str]) -> Tuple[bool, float, int, int]:
    total_logical = 0
    total_physical = 0
    any_compressed = False
    best_ratio = 1.0

    for batch in _iter_compact_query_batches(file_paths):
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0

            flags = 0
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                flags |= subprocess.CREATE_NO_WINDOW

            p = subprocess.Popen(
                ["compact.exe", "/q", *batch],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=si,
                creationflags=flags,
            )
            stdout, stderr = p.communicate(timeout=120)
            out = _decode_compact_output(stdout)
            if p.returncode != 0:
                continue

            ratio = _parse_compact_ratio(out)
            compressed = ratio > 1.0 or bool(re.findall(r'^\s*C\s+', out, re.MULTILINE))
            any_compressed = any_compressed or compressed
            best_ratio = max(best_ratio, ratio)

            logical, physical = _parse_compact_storage_sizes(out)
            if logical > 0 and physical > 0:
                total_logical += logical
                total_physical += physical
        except Exception:
            continue

    if total_logical > 0 and total_physical > 0:
        best_ratio = total_logical / total_physical
        any_compressed = any_compressed or best_ratio > 1.0

    return any_compressed, best_ratio, total_logical, total_physical


def _run_compact_query(path: str) -> Tuple[bool, float, str]:
    """运行compact.exe检查压缩状态"""
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0

        flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags |= subprocess.CREATE_NO_WINDOW

        args = ["compact.exe", "/q"]
        if os.path.isdir(path):
            args.append(f"/s:{path}")
        else:
            args.append(path)

        p = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=si,
            creationflags=flags
        )
        stdout, stderr = p.communicate(timeout=60)

        out = _decode_compact_output(stdout)

        if p.returncode != 0:
            return False, 1.0, out

        ratio = _parse_compact_ratio(out)
        is_compressed = ratio > 1.0

        compressed_lines = len(re.findall(r'^\s*C\s+', out, re.MULTILINE))
        if compressed_lines > 0:
            is_compressed = True

        return is_compressed, ratio, out

    except Exception as e:
        return False, 1.0, str(e)


def _scan_mod_fast(mod_root: str, use_compact_check: bool = False) -> ModInfo:
    """快速扫描模组，使用多种压缩检测方法"""
    info = ModInfo()
    
    collect_compact_query_files = use_compact_check or not _has_winapi_physical_size()
    compressed_by_attr = 0
    compressed_by_size = 0
    compressed_by_wof = 0
    compact_query_files: List[str] = []
    skipped = 0
    
    try:
        for root, dirs, files in os.walk(mod_root, followlinks=False):
            # 跳过隐藏目录和符号链接/交接点
            dirs[:] = [
                d for d in dirs 
                if not d.startswith('.') and not _is_symlink_or_junction(os.path.join(root, d))
            ]
            
            for filename in files:
                ext = os.path.splitext(filename)[1].lower()
                
                # Skip ignored extensions
                if ext in IGNORE_EXTENSIONS:
                    skipped += 1
                    continue
                
                filepath = os.path.join(root, filename)
                
                # Skip symlinks
                if os.path.islink(filepath):
                    skipped += 1
                    continue
                
                try:
                    stat = os.stat(filepath)
                    logical_size = stat.st_size
                    
                    # Skip very small files for counting but still track
                    if logical_size < MIN_FILE_SIZE:
                        skipped += 1
                        continue
                    
                    info.file_count += 1
                    info.total_size += logical_size
                    info.last_mtime = max(info.last_mtime, stat.st_mtime)
                    if collect_compact_query_files:
                        compact_query_files.append(filepath)
                    
                    # Categorize file
                    category = _get_file_category(filepath, ext)
                    if category == "texture":
                        info.texture_size += logical_size
                    elif category == "mesh":
                        info.mesh_size += logical_size
                    elif category == "lod":
                        info.lod_size += logical_size
                    else:
                        info.other_size += logical_size
                    
                    # Get physical size
                    physical = _get_physical_size(filepath)
                    
                    # Check NTFS attribute
                    if _has_ntfs_compressed_attr(filepath, stat):
                        compressed_by_attr += 1
                    
                    # Check WOF compression
                    if _is_wof_compressed(filepath, stat, physical):
                        compressed_by_wof += 1
                    
                    if physical > 0:
                        info.disk_size += physical
                        # Significant compression (>10% saving)
                        if physical < logical_size * 0.90:
                            compressed_by_size += 1
                    else:
                        info.disk_size += logical_size
                        
                except (OSError, PermissionError):
                    skipped += 1
                    continue
                    
    except PermissionError:
        pass
    
    info.compressed_by_attr = compressed_by_attr
    info.compressed_by_size = compressed_by_size
    info.compressed_by_wof = compressed_by_wof
    info.skipped_files = skipped
    
    # Calculate ratio
    if info.total_size > 0 and info.disk_size > 0:
        info.ratio = info.total_size / info.disk_size
    else:
        info.ratio = 1.0
    
    # Determine compression status
    has_ntfs_compression = compressed_by_attr > 0
    has_wof_compression = compressed_by_wof > 0
    
    # 基于大小的检测：需要节省>10%且多个文件
    min_threshold = max(1, info.file_count // 10)
    has_size_compression = (
        info.ratio > 1.10 and 
        compressed_by_size >= min_threshold
    )
    
    compact_says_compressed = False
    if compact_query_files and (
        not _has_winapi_physical_size() or
        (use_compact_check and not (has_ntfs_compression or has_wof_compression or has_size_compression))
    ):
        compact_says_compressed, compact_ratio, compact_total, compact_stored = _run_compact_query_files(compact_query_files)
        if compact_total > 0 and compact_stored > 0:
            info.disk_size = compact_stored
            info.ratio = info.total_size / compact_stored if info.total_size > 0 else compact_ratio
        elif compact_ratio > info.ratio:
            info.ratio = compact_ratio
    
    info.compressed = has_ntfs_compression or has_wof_compression or has_size_compression or compact_says_compressed
    info.scanned_at = _now_iso()
    
    return info


def _scan_file_paths(file_paths: List[str], use_compact_check: bool = False) -> ModInfo:
    info = ModInfo()

    collect_compact_query_files = use_compact_check or not _has_winapi_physical_size()
    compressed_by_attr = 0
    compressed_by_size = 0
    compressed_by_wof = 0
    compact_query_files: List[str] = []
    skipped = 0

    for filepath in file_paths:
        ext = os.path.splitext(filepath)[1].lower()

        if ext in IGNORE_EXTENSIONS:
            skipped += 1
            continue

        if os.path.islink(filepath):
            skipped += 1
            continue

        try:
            stat = os.stat(filepath)
            logical_size = stat.st_size

            if logical_size < MIN_FILE_SIZE:
                skipped += 1
                continue

            info.file_count += 1
            info.total_size += logical_size
            info.last_mtime = max(info.last_mtime, stat.st_mtime)
            if collect_compact_query_files:
                compact_query_files.append(filepath)

            category = _get_file_category(filepath, ext)
            if category == "texture":
                info.texture_size += logical_size
            elif category == "mesh":
                info.mesh_size += logical_size
            elif category == "lod":
                info.lod_size += logical_size
            else:
                info.other_size += logical_size

            physical = _get_physical_size(filepath)

            if _has_ntfs_compressed_attr(filepath, stat):
                compressed_by_attr += 1

            if _is_wof_compressed(filepath, stat, physical):
                compressed_by_wof += 1

            if physical > 0:
                info.disk_size += physical
                if physical < logical_size * 0.90:
                    compressed_by_size += 1
            else:
                info.disk_size += logical_size

        except (OSError, PermissionError):
            skipped += 1
            continue

    info.compressed_by_attr = compressed_by_attr
    info.compressed_by_size = compressed_by_size
    info.compressed_by_wof = compressed_by_wof
    info.skipped_files = skipped

    if info.total_size > 0 and info.disk_size > 0:
        info.ratio = info.total_size / info.disk_size
    else:
        info.ratio = 1.0

    has_ntfs_compression = compressed_by_attr > 0
    has_wof_compression = compressed_by_wof > 0
    min_threshold = max(1, info.file_count // 10)
    has_size_compression = (
        info.ratio > 1.10 and
        compressed_by_size >= min_threshold
    )

    compact_says_compressed = False
    if compact_query_files and (
        not _has_winapi_physical_size() or
        (use_compact_check and not (has_ntfs_compression or has_wof_compression or has_size_compression))
    ):
        compact_says_compressed, compact_ratio, compact_total, compact_stored = _run_compact_query_files(compact_query_files)
        if compact_total > 0 and compact_stored > 0:
            info.disk_size = compact_stored
            info.ratio = info.total_size / compact_stored if info.total_size > 0 else compact_ratio
        elif compact_ratio > info.ratio:
            info.ratio = compact_ratio

    info.compressed = has_ntfs_compression or has_wof_compression or has_size_compression or compact_says_compressed
    info.scanned_at = _now_iso()

    return info


def _scan_mod_workload(workload: ModWorkload, use_compact_check: bool = False) -> ModInfo:
    if workload.is_foreign:
        return _scan_file_paths(workload.scan_files, use_compact_check=use_compact_check)
    return _scan_mod_fast(workload.root, use_compact_check=use_compact_check)


def _verify_decompression(mod_root: str) -> Tuple[bool, float]:
    """验证文件是否已成功解压"""
    total_logical = 0
    total_physical = 0
    compressed_files = 0
    checked = 0
    collect_compact_query_files = not _has_winapi_physical_size()
    checked_paths: List[str] = []
    
    try:
        for root, dirs, files in os.walk(mod_root, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            
            for filename in files:
                ext = os.path.splitext(filename)[1].lower()
                if ext in IGNORE_EXTENSIONS:
                    continue
                
                filepath = os.path.join(root, filename)
                
                if os.path.islink(filepath):
                    continue
                
                try:
                    stat = os.stat(filepath)
                    if stat.st_size < MIN_FILE_SIZE:
                        continue
                    
                    checked += 1
                    logical = stat.st_size
                    if collect_compact_query_files:
                        checked_paths.append(filepath)
                    physical = _get_physical_size(filepath)
                    
                    if physical <= 0:
                        physical = logical
                    
                    total_logical += logical
                    total_physical += physical
                    
                    if _has_ntfs_compressed_attr(filepath, stat):
                        compressed_files += 1
                    elif _is_wof_compressed(filepath, stat, physical):
                        compressed_files += 1
                    elif physical < logical * 0.90:
                        compressed_files += 1
                    
                    if checked >= 200:
                        break
                        
                except (OSError, PermissionError):
                    continue
            
            if checked >= 200:
                break
                
    except PermissionError:
        pass
    
    ratio = total_logical / total_physical if total_physical > 0 else 1.0

    if checked_paths and not _has_winapi_physical_size():
        compact_says_compressed, compact_ratio, compact_total, compact_stored = _run_compact_query_files(checked_paths)
        if compact_total > 0 and compact_stored > 0:
            ratio = compact_total / compact_stored
        elif compact_ratio > ratio:
            ratio = compact_ratio

        if compact_says_compressed and ratio >= 1.10:
            compressed_files = max(compressed_files, max(1, checked // 10))
    
    is_decompressed = (
        ratio < 1.10 and 
        compressed_files < max(1, checked // 10)
    )
    
    return is_decompressed, ratio


def _verify_decompression_files(file_paths: List[str]) -> Tuple[bool, float]:
    total_logical = 0
    total_physical = 0
    compressed_files = 0
    checked = 0
    collect_compact_query_files = not _has_winapi_physical_size()
    checked_paths: List[str] = []

    for filepath in file_paths:
        ext = os.path.splitext(filepath)[1].lower()
        if ext in IGNORE_EXTENSIONS:
            continue

        if os.path.islink(filepath):
            continue

        try:
            stat = os.stat(filepath)
            if stat.st_size < MIN_FILE_SIZE:
                continue

            checked += 1
            total_logical += stat.st_size
            if collect_compact_query_files:
                checked_paths.append(filepath)

            physical = _get_physical_size(filepath)
            if physical > 0:
                total_physical += physical
            else:
                total_physical += stat.st_size
                physical = stat.st_size

            if _has_ntfs_compressed_attr(filepath, stat):
                compressed_files += 1
            elif _is_wof_compressed(filepath, stat, physical):
                compressed_files += 1
            elif physical < stat.st_size * 0.90:
                compressed_files += 1

        except (OSError, PermissionError):
            continue

    if total_logical > 0 and total_physical > 0:
        ratio = total_logical / total_physical
    else:
        ratio = 1.0

    if checked_paths and not _has_winapi_physical_size():
        compact_says_compressed, compact_ratio, compact_total, compact_stored = _run_compact_query_files(checked_paths)
        if compact_total > 0 and compact_stored > 0:
            ratio = compact_total / compact_stored
        elif compact_ratio > ratio:
            ratio = compact_ratio

        if compact_says_compressed and ratio >= 1.10:
            compressed_files = max(compressed_files, max(1, checked // 10))

    is_decompressed = (
        ratio < 1.10 and
        compressed_files < max(1, checked // 10)
    )

    return is_decompressed, ratio


def _verify_decompression_workload(workload: ModWorkload) -> Tuple[bool, float]:
    if workload.is_foreign:
        return _verify_decompression_files(workload.scan_files)
    return _verify_decompression(workload.root)


def _find_target_dirs(mod_root: str, targets: List[TargetType]) -> List[str]:
    """Find target directories including nested LOD paths"""
    if TargetType.ALL in targets:
        return [mod_root]

    result = []
    seen = set()

    roots = [mod_root]
    data_path = os.path.join(mod_root, "Data")
    if os.path.isdir(data_path):
        roots.append(data_path)

    # 标准目标路径
    target_names = set()
    for target in targets:
        dirs = TARGET_DIRS.get(target, [])
        for d in dirs:
            target_names.add(d.lower())

    # === 特殊 LOD 嵌套路径（游戏感知）===
    lod_nested_paths = []
    if TargetType.LOD in targets:
        lod_nested_paths = list(LOD_NESTED_PATHS)

    for base in roots:
        try:
            # 搜索一级文件夹
            for item in os.listdir(base):
                item_path = os.path.join(base, item)
                if not os.path.isdir(item_path):
                    continue
                if _is_symlink_or_junction(item_path):
                    continue

                item_lower = item.lower()
                key = item_path.lower()

                if item_lower in target_names and key not in seen:
                    seen.add(key)
                    result.append(item_path)

            # === 添加：搜索嵌套 LOD 路径 ===
            for nested in lod_nested_paths:
                nested_path = os.path.join(base, nested)
                nested_key = nested_path.lower()

                if os.path.isdir(nested_path) and nested_key not in seen:
                    seen.add(nested_key)
                    result.append(nested_path)

                    # 同时添加所有子文件夹（世界空间）
                    # textures/terrain/tamriel/, textures/terrain/blackreach/ 等
                    if "terrain" in nested.lower():
                        try:
                            for ws in os.listdir(nested_path):
                                ws_path = os.path.join(nested_path, ws)
                                ws_key = ws_path.lower()
                                if os.path.isdir(ws_path) and ws_key not in seen:
                                    seen.add(ws_key)
                                    result.append(ws_path)
                        except (OSError, PermissionError):
                            pass

        except (OSError, PermissionError):
            continue

    # 回退：如果未找到任何内容，检查模组名称
    if not result:
        mod_name = os.path.basename(mod_root).lower()
        lod_keywords = ['dyndolod', 'xlodgen', 'texgen', 'lodgen', 'terrain', 'grass']
        for kw in lod_keywords:
            if kw in mod_name:
                return [mod_root]

    return result


# ---- State Manager ----
class StateManager:
    def __init__(self, path: str):
        self._path = path
        self._data: Dict[str, dict] = {}
    
    def load(self) -> None:
        if not self._path or not os.path.isfile(self._path):
            self._data = {}
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except:
            self._data = {}
    
    def save(self) -> bool:
        if not self._path:
            return False
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = f"{self._path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
            return True
        except:
            return False
    
    def get(self, mod_name: str) -> ModInfo:
        data = self._data.get(mod_name, {})
        return ModInfo.from_dict(data)
    
    def set(self, mod_name: str, info: ModInfo) -> None:
        self._data[mod_name] = info.to_dict()
    
    def get_all_data(self) -> Dict[str, dict]:
        return copy.deepcopy(self._data)
    
    def update_all(self, data: Dict[str, dict]) -> None:
        self._data = copy.deepcopy(data)
    
    def remove(self, mod_name: str) -> None:
        self._data.pop(mod_name, None)


# ---- Compact Runner ----
class CompactRunner:
    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._cancelled = False
    
    def cancel(self) -> None:
        self._cancelled = True
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except:
                try:
                    self._process.kill()
                except:
                    pass
    
    def _run(self, args: List[str], timeout: int = 3600) -> Tuple[int, str, str]:
        if self._cancelled:
            return -1, "", "已取消"
        
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        
        flags = BELOW_NORMAL_PRIORITY_CLASS
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags |= subprocess.CREATE_NO_WINDOW
        
        try:
            self._process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=si,
                creationflags=flags
            )
            stdout, stderr = self._process.communicate(timeout=timeout)
            
            out, err = "", ""
            for enc in ("cp866", "cp1251", "utf-8"):
                try:
                    out = stdout.decode(enc)
                    err = stderr.decode(enc)
                    break
                except:
                    pass
            
            return self._process.returncode, out, err
            
        except subprocess.TimeoutExpired:
            self._process.kill()
            return -1, "", "超时"
        except Exception as e:
            return -1, "", str(e)
        finally:
            self._process = None

    def _target_args(self, path: str) -> List[str]:
        if os.path.isdir(path):
            return [f"/s:{path}"]
        return [path]
    
    def compress(self, path: str, algorithm: str) -> Tuple[int, str, str]:
        args = ["compact.exe", "/c", "/i", "/q", f"/exe:{algorithm}", *self._target_args(path)]
        return self._run(args)
    
    def decompress(self, path: str) -> Tuple[int, str, str]:
        """
        解压所有压缩类型（NTFS + WOF）
        """
        if self._cancelled:
            return -1, "", "已取消"
        
        results = []
        
        # Step 1: WOF/LZX decompression
        # 需要指定任意 /EXE 算法进行解压
        # 使用与压缩时相同的算法，或 xpress4k 作为回退
        for algo in ["xpress4k", "xpress8k", "xpress16k", "lzx"]:
            if self._cancelled:
                return -1, "", "已取消"
            
            args = ["compact.exe", "/u", f"/exe:{algo}", "/i", "/q", *self._target_args(path)]
            code, out, err = self._run(args, timeout=1800)
            results.append(f"WOF ({algo}): code={code}")
            
            # 如果成功解压则无需尝试其他算法
            # 但 compact 不会报告处理了多少文件，所以尝试所有算法
        
        # Step 2: Standard NTFS decompression（针对 NTFS 压缩而非 WOF 的文件）
        if self._cancelled:
            return -1, "", "已取消"
        
        args = ["compact.exe", "/u", "/i", "/q", *self._target_args(path)]
        code, out, err = self._run(args, timeout=1800)
        results.append(f"NTFS: code={code}")
        
        # 结果验证
        all_output = "\n".join(results)
        
        return 0, all_output, ""

# ---- Worker ----
class CompressionWorker(QtCore.QObject):
    log = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(int, int, str)
    finished = QtCore.pyqtSignal(bool, str, dict)
    
    def __init__(self, organizer, state_data: Dict[str, dict],
                 mod_names: List[str], mode: str,
                 targets: List[TargetType], algorithm: str):
        super().__init__()
        self._organizer = organizer
        self._state_data = copy.deepcopy(state_data)
        self._mod_names = list(mod_names)
        self._mode = mode
        self._targets = targets
        self._algorithm = algorithm
        self._cancelled = False
        self._runner: Optional[CompactRunner] = None
    
    def cancel(self) -> None:
        self._cancelled = True
        if self._runner:
            self._runner.cancel()
    
    def _get_mod_path(self, name: str) -> str:
        return _get_mod_path_from_organizer(self._organizer, name)
    
    @QtCore.pyqtSlot()
    def run(self) -> None:
        t0 = time.time()
        success = 0
        errors = 0
        skipped = 0
        total = len(self._mod_names)
        
        self._runner = CompactRunner()
        
        action = "压缩" if self._mode == "compress" else "解压"
        self.log.emit(f"{'='*50}")
        self.log.emit(f"{action}: 共 {total} 个模组")
        if self._mode == "compress":
            self.log.emit(f"算法: {self._algorithm}")
        
        target_strs = [TARGET_NAMES.get(t, str(t)) for t in self._targets]
        self.log.emit(f"目标: {', '.join(target_strs)}")
        self.log.emit(f"{'='*50}")
        
        for i, name in enumerate(self._mod_names):
            if self._cancelled:
                self.log.emit(">>> 用户已取消")
                break
            
            self.progress.emit(i + 1, total, name)
            
            workload = _resolve_mod_workload(self._organizer, name, self._targets)
            path = workload.root
            if not path or not os.path.isdir(path):
                self.log.emit(f"[错误] {name}: 未找到路径")
                errors += 1
                continue
            
            items = workload.operation_items
            if not items:
                if workload.is_foreign:
                    self.log.emit(f"[跳过] {name}: 未找到匹配文件")
                else:
                    self.log.emit(f"[跳过] {name}: 未找到目标文件夹")
                skipped += 1
                continue

            if workload.is_foreign:
                self.log.emit(f"  [信息] {name}: 未托管模组，正在处理 {len(items)} 个文件")
            
            mod_ok = True
            processed_size = 0
            
            for item_index, d in enumerate(items, start=1):
                if self._cancelled:
                    break
                
                rel = _describe_work_item(d, path)
                if workload.is_foreign and (item_index == 1 or item_index % 25 == 0 or item_index == len(items)):
                    self.progress.emit(i + 1, total, f"{name} [{item_index}/{len(items)}]")
                
                if self._mode == "compress":
                    code, out, err = self._runner.compress(d, self._algorithm)
                    action_sym = "C"
                else:
                    code, out, err = self._runner.decompress(d)
                    action_sym = "U"
                
                if code == 0:
                    if workload.is_foreign:
                        if item_index == 1 or item_index % 100 == 0 or item_index == len(items):
                            self.log.emit(f"  [{action_sym}] {item_index}/{len(items)} - {rel}")
                    else:
                        self.log.emit(f"  [{action_sym}] {rel} - 完成")
                else:
                    self.log.emit(f"  [错误] {rel}: 代码={code}")
                    if err:
                        self.log.emit(f"        {err[:100]}")
                    mod_ok = False
            
            if self._cancelled:
                continue
            
            # 更新状态
            if mod_ok:
                if self._mode == "compress":
                    info = _scan_mod_workload(workload)
                    info.compressed = True
                    info.algorithm = self._algorithm
                    info.compressed_at = _now_iso()
                    self._state_data[name] = info.to_dict()
                    
                    saved = info.total_size - info.disk_size
                    self.log.emit(f"[完成] {name}: {info.ratio:.2f}倍, 节省 {_format_size(saved)}")
                    success += 1
                    
                else:  # decompress
                    is_decompressed, ratio = _verify_decompression_workload(workload)
                    info = _scan_mod_workload(workload)
                    
                    if is_decompressed or ratio < 1.15:
                        info.compressed = False
                        info.algorithm = ""
                        info.compressed_at = ""
                        self.log.emit(f"[完成] {name}: 已解压 (压缩比: {ratio:.2f})")
                        success += 1
                    else:
                        info.compressed = True
                        self.log.emit(f"[警告] {name}: 可能仍处于压缩状态 (压缩比: {ratio:.2f})")
                        success += 1
                    
                    self._state_data[name] = info.to_dict()
            else:
                info = _scan_mod_workload(workload)
                self._state_data[name] = info.to_dict()
                errors += 1
        
        dt = time.time() - t0
        self.log.emit(f"{'='*50}")
        summary = f"完成于 {dt:.1f}秒 | 成功: {success} | 错误: {errors} | 跳过: {skipped}"
        self.log.emit(summary)
        
        self.finished.emit(errors == 0, summary, self._state_data)


# ---- Dialog ----
class ModCompressorDialog(QtWidgets.QDialog):
    def __init__(self, parent, organizer, state_manager: StateManager, experience_mode: str = "advanced"):
        super().__init__(parent)
        self._experience_mode = "novice" if str(experience_mode).lower() == "novice" else "advanced"
        if self._experience_mode == "novice":
            self.setWindowTitle("星黎空间优化 - 新手模式")
            self.resize(920, 720)
        else:
            self.setWindowTitle("星黎模组压缩工具 - 高级模式")
            self.resize(1100, 800)
        
        self._organizer = organizer
        self._mod_state = ModStateService(organizer)
        self._state = state_manager
        self._mods: List[Dict[str, Any]] = []
        self._worker: Optional[CompressionWorker] = None
        self._thread: Optional[QtCore.QThread] = None
        self._elapsed_seconds = 0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_timer_tick)
        self._operation_started = False
        
        self._build_ui()
        self._load_options()
        self._apply_experience_mode()
        self._load_mods()
        self._apply_filter()
    
    def closeEvent(self, event):
        self._save_options()
        self._cancel_job()
        super().closeEvent(event)
    
    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        
        # Mode guidance
        self.mode_hint = QtWidgets.QLabel()
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setStyleSheet(
            "QLabel { background:#eef5ff; border:1px solid #b7c9e8; border-radius:6px; padding:8px; }"
        )
        layout.addWidget(self.mode_hint)

        # Stats bar
        stats_layout = QtWidgets.QHBoxLayout()
        self.lblStats = QtWidgets.QLabel("加载中...")
        self.lblStats.setStyleSheet("color: #666; font-size: 11px;")
        stats_layout.addWidget(self.lblStats)
        stats_layout.addStretch()
        self.lblSelected = QtWidgets.QLabel("已选: 0")
        self.lblSelected.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 11px;")
        stats_layout.addWidget(self.lblSelected)
        self.lblTimer = QtWidgets.QLabel("")
        self.lblTimer.setStyleSheet("color: #888; font-size: 11px; font-family: Consolas;")
        stats_layout.addWidget(self.lblTimer)
        layout.addLayout(stats_layout)
        
        # Filters
        filter_box = QtWidgets.QHBoxLayout()
        
        self.chkActive = QtWidgets.QCheckBox("仅已启用")
        self.chkActive.stateChanged.connect(self._apply_filter)
        
        self.chkCompressed = QtWidgets.QCheckBox("已压缩")
        self.chkCompressed.stateChanged.connect(self._apply_filter)
        
        self.chkUncompressed = QtWidgets.QCheckBox("未压缩")
        self.chkUncompressed.stateChanged.connect(self._apply_filter)
        
        self.chkHasLOD = QtWidgets.QCheckBox("包含 LOD")
        self.chkHasLOD.stateChanged.connect(self._apply_filter)
        
        self.editSearch = QtWidgets.QLineEdit()
        self.editSearch.setPlaceholderText("搜索模组...")
        self.editSearch.setClearButtonEnabled(True)
        self.editSearch.textChanged.connect(self._apply_filter)
        
        filter_box.addWidget(self.chkActive)
        filter_box.addWidget(self.chkCompressed)
        filter_box.addWidget(self.chkUncompressed)
        filter_box.addWidget(self.chkHasLOD)
        filter_box.addStretch()
        filter_box.addWidget(self.editSearch, 1)
        layout.addLayout(filter_box)
        
        # Table
        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "✓", "模组", "启用", "状态", "压缩比", "大小", "节省"
        ])
        
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, RESIZE_TO_CONTENTS)
        hdr.setSectionResizeMode(1, INTERACTIVE)
        hdr.setSectionResizeMode(2, RESIZE_TO_CONTENTS)
        hdr.setSectionResizeMode(3, INTERACTIVE)
        hdr.setSectionResizeMode(4, RESIZE_TO_CONTENTS)
        hdr.setSectionResizeMode(5, INTERACTIVE)
        hdr.setSectionResizeMode(6, INTERACTIVE)
        self.table.setColumnWidth(1, 360)
        self.table.setColumnWidth(3, 140)
        self.table.setColumnWidth(5, 110)
        self.table.setColumnWidth(6, 110)
        
        self.table.setSelectionBehavior(SELECT_ROWS)
        self.table.setEditTriggers(NO_EDIT)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        layout.addWidget(self.table, 3)
        
        # Options
        self.opts = QtWidgets.QGroupBox("压缩选项")
        opts_layout = QtWidgets.QHBoxLayout(self.opts)
        
        opts_layout.addWidget(QtWidgets.QLabel("目标:"))
        
        self.chkTextures = QtWidgets.QCheckBox("纹理")
        self.chkTextures.setChecked(True)
        self.chkTextures.setToolTip("压缩纹理文件。")
        self.chkTextures.stateChanged.connect(self._save_options)
        
        self.chkMeshes = QtWidgets.QCheckBox("模型")
        self.chkMeshes.setChecked(True)
        self.chkMeshes.setToolTip("压缩模型文件。")
        self.chkMeshes.stateChanged.connect(self._save_options)
        
        self.chkSounds = QtWidgets.QCheckBox("音频")
        self.chkSounds.setToolTip("压缩音频和音乐文件。")
        self.chkSounds.stateChanged.connect(self._save_options)
        
        self.chkLOD = QtWidgets.QCheckBox("LOD/DynDOLOD")
        self.chkLOD.setChecked(True)
        self.chkLOD.setToolTip("压缩 LOD、地形和草地生成文件。")
        self.chkLOD.setStyleSheet("font-weight: bold;")
        self.chkLOD.stateChanged.connect(self._save_options)
        
        self.chkAnimations = QtWidgets.QCheckBox("动画")
        self.chkAnimations.setToolTip("压缩动画相关文件。")
        self.chkAnimations.stateChanged.connect(self._save_options)
        
        self.chkAll = QtWidgets.QCheckBox("整个模组")
        self.chkAll.setToolTip("压缩模组文件夹内的所有内容")
        self.chkAll.stateChanged.connect(self._on_entire_mod_toggled)
        self.chkAll.stateChanged.connect(self._save_options)
        
        opts_layout.addWidget(self.chkTextures)
        opts_layout.addWidget(self.chkMeshes)
        opts_layout.addWidget(self.chkSounds)
        opts_layout.addWidget(self.chkLOD)
        opts_layout.addWidget(self.chkAnimations)
        opts_layout.addWidget(self.chkAll)
        
        opts_layout.addSpacing(20)
        opts_layout.addWidget(QtWidgets.QLabel("算法:"))
        
        self.cmbAlgo = QtWidgets.QComboBox()
        for k, v in COMPRESSION_ALGORITHMS.items():
            self.cmbAlgo.addItem(v, k)
        self.cmbAlgo.setCurrentIndex(1)  # xpress8k
        self.cmbAlgo.setToolTip("LZX：压缩率更高、速度较慢\nXPRESS：速度更快")
        self.cmbAlgo.currentIndexChanged.connect(self._save_options)
        opts_layout.addWidget(self.cmbAlgo)
        
        opts_layout.addStretch()
        layout.addWidget(self.opts)
        
        # Selection buttons
        sel_box = QtWidgets.QHBoxLayout()
        
        self.btnSelectAll = QtWidgets.QPushButton("全选")
        self.btnSelectAll.clicked.connect(lambda: self._select_all(True))
        
        self.btnSelectNone = QtWidgets.QPushButton("取消选择")
        self.btnSelectNone.clicked.connect(lambda: self._select_all(False))
        
        self.btnSelectCompressed = QtWidgets.QPushButton("选已压缩")
        self.btnSelectCompressed.clicked.connect(self._select_compressed)
        
        self.btnSelectUncompressed = QtWidgets.QPushButton("选未压缩")
        self.btnSelectUncompressed.clicked.connect(self._select_uncompressed)
        
        self.btnSelectLOD = QtWidgets.QPushButton("选择LOD模组")
        self.btnSelectLOD.clicked.connect(self._select_lod_mods)
        self.btnSelectLOD.setStyleSheet("font-weight: bold;")
        
        self.btnScan = QtWidgets.QPushButton("🔍 扫描已选")
        self.btnScan.clicked.connect(self._scan_selected)
        
        self.btnScanAll = QtWidgets.QPushButton("🔍 扫描全部")
        self.btnScanAll.clicked.connect(self._scan_all)
        
        sel_box.addWidget(self.btnSelectAll)
        sel_box.addWidget(self.btnSelectNone)
        sel_box.addWidget(self.btnSelectCompressed)
        sel_box.addWidget(self.btnSelectUncompressed)
        sel_box.addWidget(self.btnSelectLOD)
        sel_box.addStretch()
        sel_box.addWidget(self.btnScan)
        sel_box.addWidget(self.btnScanAll)
        layout.addLayout(sel_box)
        
        # Progress and actions
        action_box = QtWidgets.QHBoxLayout()
        
        self.progress = QtWidgets.QProgressBar()
        self.progress.setMinimum(0)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        
        self.btnCompress = QtWidgets.QPushButton("📦 压缩")
        self.btnCompress.setStyleSheet("background-color: #4CAF50; color: black; font-weight: bold; padding: 8px 16px;")
        self.btnCompress.clicked.connect(lambda: self._run_job("compress"))
        
        self.btnDecompress = QtWidgets.QPushButton("📂 解压")
        self.btnDecompress.setStyleSheet("background-color: #2196F3; color: black; font-weight: bold; padding: 8px 16px;")
        self.btnDecompress.clicked.connect(lambda: self._run_job("decompress"))
        
        self.btnCancel = QtWidgets.QPushButton("⏹ 取消")
        self.btnCancel.clicked.connect(self._cancel_job)
        self.btnCancel.setEnabled(False)
        
        self.btnClose = QtWidgets.QPushButton("关闭")
        self.btnClose.clicked.connect(self.close)
        
        action_box.addWidget(self.progress, 2)
        action_box.addWidget(self.btnCompress)
        action_box.addWidget(self.btnDecompress)
        action_box.addWidget(self.btnCancel)
        action_box.addWidget(self.btnClose)
        layout.addLayout(action_box)
        
        # Log
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setMaximumHeight(200)
        self.log.setFont(QtGui.QFont("Consolas", 9))
        layout.addWidget(self.log, 1)
        
        self.status = QtWidgets.QLabel("就绪")
        layout.addWidget(self.status)
    
    def _apply_experience_mode(self):
        """Apply novice-safe defaults without exposing advanced compression controls."""
        novice = self._experience_mode == "novice"
        if novice:
            self.mode_hint.setText(
                "新手模式会使用推荐设置。建议先扫描，确认后再压缩。"
            )
            self.chkTextures.setChecked(True)
            self.chkMeshes.setChecked(True)
            self.chkSounds.setChecked(False)
            self.chkLOD.setChecked(True)
            self.chkAnimations.setChecked(False)
            self.chkAll.setChecked(False)
            idx = self.cmbAlgo.findData("xpress8k")
            if idx >= 0:
                self.cmbAlgo.setCurrentIndex(idx)

            self.opts.setVisible(False)
            self.chkCompressed.setVisible(False)
            self.chkUncompressed.setVisible(False)
            self.chkHasLOD.setVisible(False)
            self.btnSelectCompressed.setVisible(False)
            self.btnSelectLOD.setVisible(False)
            self.btnScanAll.setVisible(False)
            self.btnCompress.setText("📦 推荐压缩")
            self.btnDecompress.setText("↩ 恢复已选")
        else:
            self.mode_hint.setText(
                "高级模式可自定义压缩范围和算法。建议先扫描确认，再开始处理。"
            )

    def _on_timer_tick(self):
        self._elapsed_seconds += 1
        m = self._elapsed_seconds // 60
        s = self._elapsed_seconds % 60
        self.lblTimer.setText(f"已用时间: {m:02d}:{s:02d}")
    
    def _on_entire_mod_toggled(self, state):
        """Disable other checkboxes when 'Entire mod' is checked"""
        enabled = state != CHECKED
        self.chkTextures.setEnabled(enabled)
        self.chkMeshes.setEnabled(enabled)
        self.chkSounds.setEnabled(enabled)
        self.chkLOD.setEnabled(enabled)
        self.chkAnimations.setEnabled(enabled)
    
    def _log(self, msg: str):
        """Append to the log (newest at top)"""
        self.log.setPlainText(msg + "\n" + self.log.toPlainText())
    
    def _get_mod_list(self) -> List[str]:
        try:
            ml = self._organizer.modList()
            for attr in ("allMods", "modNames"):
                if hasattr(ml, attr):
                    return [str(x) for x in getattr(ml, attr)()]
        except:
            pass
        return []
    
    def _is_mod_active(self, name: str) -> bool:
        return self._mod_state.is_active(name, default=True)
    
    def _get_mod_path(self, name: str) -> str:
        return _get_mod_path_from_organizer(self._organizer, name)
    
    def _load_mods(self):
        self._mods = []
        total_size = 0
        total_saved = 0
        compressed_count = 0
        
        for name in self._get_mod_list():
            # 跳过分隔符文件夹（MO2中以 _separator 结尾的虚拟条目）
            if name.lower().endswith("_separator"):
                continue
            
            path = self._get_mod_path(name)
            path = self._get_mod_path(name)
            if not path:
                continue
            
            info = self._state.get(name)
            self._mods.append({
                "name": name,
                "active": self._is_mod_active(name),
                "path": path,
                "info": info
            })
            
            total_size += info.total_size
            if info.compressed and info.disk_size > 0:
                total_saved += info.total_size - info.disk_size
                compressed_count += 1
        
        self.lblStats.setText(
            f"模组: {len(self._mods)} | "
            f"已压缩: {compressed_count} | "
            f"总大小: {_format_size(total_size)} | "
            f"节省空间: {_format_size(total_saved)}"
        )
        self._log(f"载入: {len(self._mods)} 个模组")
    
    def _has_lod_content(self, info: ModInfo) -> bool:
        """检查模组是否包含大量LOD内容"""
        return info.lod_size > 256 * 1024  # > 256KB
    
    def _apply_filter(self):
        only_active = self.chkActive.isChecked()
        only_compressed = self.chkCompressed.isChecked()
        only_uncompressed = self.chkUncompressed.isChecked()
        only_lod = self.chkHasLOD.isChecked()
        query = self.editSearch.text().strip().lower()
        
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        
        visible_count = 0
        
        for mod in self._mods:
            info: ModInfo = mod["info"]
            
            if only_active and not mod["active"]:
                continue
            if query and query not in mod["name"].lower():
                continue
            if only_compressed and not info.compressed:
                continue
            if only_uncompressed and info.compressed:
                continue
            if only_lod and not self._has_lod_content(info):
                continue
            
            visible_count += 1
            row = self.table.rowCount()
            self.table.insertRow(row)
            
            # Checkbox
            chk = SortableTableWidgetItem()
            chk.setCheckState(UNCHECKED)
            chk.setData(MOD_NAME_ROLE, mod["name"])
            chk.setData(SORT_ROLE, mod["name"].casefold())
            self.table.setItem(row, 0, chk)
            
            # Name
            name_item = SortableTableWidgetItem(mod["name"])
            name_item.setData(SORT_ROLE, mod["name"].casefold())
            self.table.setItem(row, 1, name_item)
            
            # Active
            act = SortableTableWidgetItem("✓" if mod["active"] else "")
            act.setData(SORT_ROLE, 1 if mod["active"] else 0)
            act.setTextAlignment(ALIGN_CENTER)
            self.table.setItem(row, 2, act)
            
            # Status
            if info.compressed:
                status_txt = f"✓ {info.algorithm}" if info.algorithm else "✓ 已压缩"
                status = SortableTableWidgetItem(status_txt)
                status.setData(SORT_ROLE, f"0:{status_txt.casefold()}")
                status.setForeground(QtGui.QColor("#4CAF50"))
            elif info.scanned_at:
                status = SortableTableWidgetItem("未压缩")
                status.setData(SORT_ROLE, "1:not compressed")
            else:
                status = SortableTableWidgetItem("未扫描")
                status.setData(SORT_ROLE, "2:not scanned")
                status.setForeground(QtGui.QColor("#999999"))
            status.setTextAlignment(ALIGN_CENTER)
            self.table.setItem(row, 3, status)
            
            # Ratio
            if info.ratio > 1.0:
                ratio_txt = f"{info.ratio:.2f}x"
                ratio_item = SortableTableWidgetItem(ratio_txt)
                ratio_item.setData(SORT_ROLE, info.ratio)
                if info.ratio > 1.5:
                    ratio_item.setForeground(QtGui.QColor("#4CAF50"))
                elif info.ratio > 1.2:
                    ratio_item.setForeground(QtGui.QColor("#8BC34A"))
            else:
                ratio_item = SortableTableWidgetItem("1.00x")
                ratio_item.setData(SORT_ROLE, 1.0)
            ratio_item.setTextAlignment(ALIGN_CENTER)
            self.table.setItem(row, 4, ratio_item)
            
            # Size
            size_item = SortableTableWidgetItem(
                _format_size(info.total_size) if info.total_size else "—"
            )
            size_item.setData(SORT_ROLE, info.total_size)
            size_item.setTextAlignment(ALIGN_RIGHT)
            self.table.setItem(row, 5, size_item)
            
            # Saved
            if info.total_size > info.disk_size and info.disk_size > 0:
                saved = info.total_size - info.disk_size
                sav_txt = f"-{_format_size(saved)}"
                sav = SortableTableWidgetItem(sav_txt)
                sav.setData(SORT_ROLE, saved)
                sav.setForeground(QtGui.QColor("#4CAF50"))
            else:
                sav = SortableTableWidgetItem("—")
                sav.setData(SORT_ROLE, 0)
            sav.setTextAlignment(ALIGN_RIGHT)
            self.table.setItem(row, 6, sav)
        
        self.table.setSortingEnabled(True)
        self._update_selected_count()
        self.status.setText(f"显示 {visible_count} / {len(self._mods)} 个模组")
        self._save_options()
    
    def _select_all(self, checked: bool):
        state = CHECKED if checked else UNCHECKED
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(state)
        self._update_selected_count()
    
    def _select_compressed(self):
        """选择第6列（节省）SORT_ROLE大于0的模组"""
        for row in range(self.table.rowCount()):
            sav = self.table.item(row, 6)
            chk = self.table.item(row, 0)
            if sav and chk:
                saved = sav.data(SORT_ROLE)
                chk.setCheckState(CHECKED if (saved is not None and saved > 0) else UNCHECKED)
        self._update_selected_count()
    
    def _select_uncompressed(self):
        """选择第6列（节省）SORT_ROLE为0且已扫描的模组"""
        for row in range(self.table.rowCount()):
            status_item = self.table.item(row, 3)
            sav = self.table.item(row, 6)
            chk = self.table.item(row, 0)
            if status_item and sav and chk:
                is_comp = sav.data(SORT_ROLE) is not None and sav.data(SORT_ROLE) > 0
                chk.setCheckState(UNCHECKED if is_comp else CHECKED)
        self._update_selected_count()
    
    def _select_lod_mods(self):
        """使用已保存的扫描数据选择包含 LOD的模组"""
        selected = 0
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if not chk:
                continue
            name = chk.data(MOD_NAME_ROLE)
            matched = False
            for mod in self._mods:
                if mod["name"] == name:
                    if self._has_lod_content(mod["info"]):
                        matched = True
                    break
            chk.setCheckState(CHECKED if matched else UNCHECKED)
            if matched:
                selected += 1
        self._log(f"已选择 {selected} 个包含 LOD的模组")
        self._update_selected_count()
    
    def _get_checked(self) -> List[str]:
        result = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == CHECKED:
                name = item.data(MOD_NAME_ROLE)
                if name:
                    result.append(name)
        return result
    
    def _get_targets(self) -> List[TargetType]:
        if self.chkAll.isChecked():
            return [TargetType.ALL]
        
        targets = []
        if self.chkTextures.isChecked():
            targets.append(TargetType.TEXTURES)
        if self.chkMeshes.isChecked():
            targets.append(TargetType.MESHES)
        if self.chkSounds.isChecked():
            targets.append(TargetType.SOUNDS)
        if self.chkLOD.isChecked():
            targets.append(TargetType.LOD)
        if self.chkAnimations.isChecked():
            targets.append(TargetType.ANIMATIONS)
        return targets
    
    def _scan_mods(self, names: List[str], deep_scan: bool = False):
        self._log(f"{'='*50}")
        self._log(f"正在扫描: {len(names)} 个模组{' (深度扫描)' if deep_scan else ''}")
        self._log(f"{'='*50}")
        self.progress.setMaximum(len(names))
        
        self._start_timer()
        
        for i, name in enumerate(names):
            self.progress.setValue(i + 1)
            self.progress.setFormat(f"{i+1}/{len(names)}: {name}")
            self.status.setText(f"正在扫描: {name}")
            QtWidgets.QApplication.processEvents()
            
            workload = _resolve_mod_workload(self._organizer, name, [TargetType.ALL])
            path = workload.root
            if not path:
                self._log(f"  [跳过] {name}: 未找到路径")
                continue
            
            old_info = self._state.get(name)
            info = _scan_mod_workload(workload, use_compact_check=deep_scan)
            
            # 保留算法信息（如果仍处于压缩状态）
            if info.compressed and old_info.algorithm and old_info.compressed:
                info.algorithm = old_info.algorithm
                info.compressed_at = old_info.compressed_at
            elif not info.compressed:
                info.algorithm = ""
                info.compressed_at = ""
            
            self._state.set(name, info)
            
            for mod in self._mods:
                if mod["name"] == name:
                    mod["info"] = info
                    break
            
            # Status line
            if info.compressed:
                status = f"已压缩 ({info.ratio:.2f}倍)"
            else:
                status = "未压缩"
            
            self._log(f"  {name}: {status}, size={_format_size(info.total_size)}")
        
        self._stop_timer()
        self._state.save()
        self._apply_filter()
        self._update_stats()
        self.progress.setValue(0)
        self.progress.setFormat("")
        self.status.setText("就绪")
        self._log("扫描完成")
    
    def _update_stats(self):
        """Update statistics display"""
        total_size = 0
        total_saved = 0
        compressed_count = 0
        
        for mod in self._mods:
            info = mod["info"]
            total_size += info.total_size
            if info.compressed and info.disk_size > 0:
                total_saved += info.total_size - info.disk_size
                compressed_count += 1
        
        self.lblStats.setText(
            f"模组: {len(self._mods)} | "
            f"已压缩: {compressed_count} | "
            f"总大小: {_format_size(total_size)} | "
            f"节省空间: {_format_size(total_saved)}"
        )
    
    def _update_selected_count(self):
        """Update the selected mods count label"""
        count = len(self._get_checked())
        self.lblSelected.setText(f"已选: {count}")
    
    def _start_timer(self):
        self._elapsed_seconds = 0
        self._timer.start()
        self.lblTimer.setText("已用时间: 00:00")
    
    def _stop_timer(self):
        self._timer.stop()
    
    def _options_path(self) -> str:
        base = ""
        try:
            base = str(self._organizer.pluginDataPath())
        except:
            try:
                base = str(self._organizer.profilePath())
            except:
                base = os.path.expanduser("~")
        filename = "ui_options_novice.json" if self._experience_mode == "novice" else "ui_options_advanced.json"
        return os.path.join(base, "xingli_assistant", "mod_compressor", filename)
    
    def _save_options(self):
        try:
            opts_path = self._options_path()
            os.makedirs(os.path.dirname(opts_path), exist_ok=True)
            data = {
                "active_only": self.chkActive.isChecked(),
                "compressed_only": self.chkCompressed.isChecked(),
                "uncompressed_only": self.chkUncompressed.isChecked(),
                "lod_only": self.chkHasLOD.isChecked(),
                "targets": {
                    "textures": self.chkTextures.isChecked(),
                    "meshes": self.chkMeshes.isChecked(),
                    "sounds": self.chkSounds.isChecked(),
                    "lod": self.chkLOD.isChecked(),
                    "animations": self.chkAnimations.isChecked(),
                },
                "entire_mod": self.chkAll.isChecked(),
                "algorithm": self.cmbAlgo.currentData(),
            }
            with open(opts_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"mod-compressor: failed to save UI options: {e}")
    
    def _load_options(self):
        try:
            opts_path = self._options_path()
            if not os.path.exists(opts_path):
                return
            with open(opts_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.chkActive.setChecked(data.get("active_only", False))
            self.chkCompressed.setChecked(data.get("compressed_only", False))
            self.chkUncompressed.setChecked(data.get("uncompressed_only", False))
            self.chkHasLOD.setChecked(data.get("lod_only", False))
            targets = data.get("targets", {})
            self.chkTextures.setChecked(targets.get("textures", True))
            self.chkMeshes.setChecked(targets.get("meshes", True))
            self.chkSounds.setChecked(targets.get("sounds", False))
            self.chkLOD.setChecked(targets.get("lod", True))
            self.chkAnimations.setChecked(targets.get("animations", False))
            self.chkAll.setChecked(data.get("entire_mod", False))
            algo = data.get("algorithm")
            if algo:
                idx = self.cmbAlgo.findData(algo)
                if idx >= 0:
                    self.cmbAlgo.setCurrentIndex(idx)
            self._on_entire_mod_toggled(CHECKED if data.get("entire_mod", False) else UNCHECKED)
        except Exception as e:
            print(f"mod-compressor: failed to load UI options: {e}")
    
    def _scan_selected(self):
        names = self._get_checked()
        if not names:
            QtWidgets.QMessageBox.information(self, "扫描", "请先选择模组")
            return
        self._scan_mods(names)
    
    def _scan_all(self):
        reply = QtWidgets.QMessageBox.question(
            self, "扫描全部",
            f"扫描全部 {len(self._mods)} 个模组？\n\n模组较多时可能需要较长时间。"
        )
        if reply == YES_BTN:
            self._scan_mods([m["name"] for m in self._mods])
    
    def _set_ui_busy(self, busy: bool):
        enabled = not busy
        self.btnCompress.setEnabled(enabled)
        self.btnDecompress.setEnabled(enabled)
        self.btnScan.setEnabled(enabled)
        self.btnScanAll.setEnabled(enabled)
        self.btnSelectAll.setEnabled(enabled)
        self.btnSelectNone.setEnabled(enabled)
        self.btnSelectCompressed.setEnabled(enabled)
        self.btnSelectUncompressed.setEnabled(enabled)
        self.btnSelectLOD.setEnabled(enabled)
        self.btnCancel.setEnabled(busy)
        self.chkActive.setEnabled(enabled)
        self.chkCompressed.setEnabled(enabled)
        self.chkUncompressed.setEnabled(enabled)
        self.chkHasLOD.setEnabled(enabled)
        self.editSearch.setEnabled(enabled)
        self.chkTextures.setEnabled(enabled and not self.chkAll.isChecked())
        self.chkMeshes.setEnabled(enabled and not self.chkAll.isChecked())
        self.chkSounds.setEnabled(enabled and not self.chkAll.isChecked())
        self.chkLOD.setEnabled(enabled and not self.chkAll.isChecked())
        self.chkAnimations.setEnabled(enabled and not self.chkAll.isChecked())
        self.chkAll.setEnabled(enabled)
        self.cmbAlgo.setEnabled(enabled)
    
    def _cancel_job(self):
        if self._worker:
            self._worker.cancel()
            self._log(">>> 正在取消...")
    
    def _run_job(self, mode: str):
        names = self._get_checked()
        if not names:
            QtWidgets.QMessageBox.information(self, "运行", "请先选择模组")
            return
        
        if self._experience_mode == "novice":
            targets = [TargetType.TEXTURES, TargetType.MESHES, TargetType.LOD]
            algo = "xpress8k"
        else:
            targets = self._get_targets()
            if not targets:
                QtWidgets.QMessageBox.warning(self, "运行", "请至少选择一种目标类型")
                return
            algo = self.cmbAlgo.currentData()
        action = "压缩" if mode == "compress" else "解压"
        
        target_names = [TARGET_NAMES.get(t, str(t)) for t in targets]
        
        msg = f"{action} {len(names)} 个模组？\n\n"
        msg += f"目标: {', '.join(target_names)}\n"
        if mode == "compress":
            msg += f"算法: {algo}\n"
        msg += f"\n此操作可能需要较长时间。"
        
        reply = QtWidgets.QMessageBox.question(self, "确认", msg)
        if reply != YES_BTN:
            return
        
        self._set_ui_busy(True)
        self.progress.setMaximum(len(names))
        self.progress.setValue(0)
        self._start_timer()
        
        self._worker = CompressionWorker(
            self._organizer,
            self._state.get_all_data(),
            names, mode, targets, algo
        )
        
        self._thread = QtCore.QThread()
        self._worker.moveToThread(self._thread)
        
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self._log)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished, QUEUED_CONNECTION)
        
        self._thread.start()
    
    def _on_progress(self, done: int, total: int, name: str):
        self.progress.setValue(done)
        self.progress.setFormat(f"{done}/{total}: {name}")
        self.status.setText(f"处理中: {name}")
    
    def _on_finished(self, ok: bool, summary: str, data: dict):
        self._stop_timer()
        self._log(summary)
        
        self._state.update_all(data)
        self._state.save()
        
        for mod in self._mods:
            mod["info"] = self._state.get(mod["name"])
        
        self._apply_filter()
        self._update_stats()
        self._set_ui_busy(False)
        self.progress.setValue(self.progress.maximum() if ok else 0)
        self.progress.setFormat("")
        self.status.setText(summary)
        
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread.deleteLater()
            self._thread = None
        if self._worker:
            self._worker.deleteLater()
            self._worker = None


# ---- Xingli integration entry ----
def _state_path_for(organizer) -> str:
    base = ""
    try:
        base = str(organizer.pluginDataPath())
    except Exception:
        try:
            base = str(organizer.profilePath())
        except Exception:
            base = os.path.expanduser("~")
    game_token = _detect_game(organizer)
    return os.path.join(base, "xingli_assistant", "mod_compressor", game_token, "state.json")


def show_mod_compressor(parent, organizer, experience_mode: str = "advanced"):
    """Open the integrated compressor. Compression is restricted to advanced mode."""
    if str(experience_mode).lower() != "advanced":
        QtWidgets.QMessageBox.information(
            parent,
            "高级玩家功能",
            "模组压缩功能仅对高级玩家模式开放。",
        )
        return False
    path = _state_path_for(organizer)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    state = StateManager(path)
    state.load()
    dlg = ModCompressorDialog(parent, organizer, state, experience_mode=experience_mode)
    if hasattr(dlg, "exec"):
        return dlg.exec()
    return dlg.exec_()
