# coding=utf-8
"""PGPatcher integration helpers for Xingli Assistant.

The adapter deliberately launches PGPatcher through MO2's startApplication so it
sees the current USVFS load order. It never edits PGPatcher's own cfg files.
DynDOLOD and TexGen output states are checked before launch to preserve the
recommended PGPatcher -> TexGen -> DynDOLOD generation order.
"""

import os
from typing import Dict, List, Optional, Tuple

from .mod_state_service import ModStateService


OFFICIAL_NEXUS_URL = "https://www.nexusmods.com/skyrimspecialedition/mods/120946"
OFFICIAL_GITHUB_URL = "https://github.com/hakasapl/PGPatcher"
RECOMMENDED_OUTPUT_NAME = "PGPatcher_Output"


def _norm(value: str) -> str:
    return str(value or "").strip().casefold().replace(" ", "").replace("_", "").replace("-", "")


def _candidate_exes(mod_path: str) -> List[str]:
    if not mod_path:
        return []
    return [
        os.path.join(mod_path, "PGPatcher.exe"),
        os.path.join(mod_path, "PGPatcher", "PGPatcher.exe"),
        os.path.join(mod_path, "ParallaxGen.exe"),
    ]


def find_pgpatcher(organizer) -> Tuple[Optional[str], Optional[str]]:
    """Return (exe_path, owning_mod_name). owning_mod_name can be None for external installs."""
    service = ModStateService(organizer)
    records = service.records(profile_priority=True)

    # Prefer a clearly named MO2 mod so PGPatcher itself can stay disabled while
    # its executable is launched directly through MO2's VFS.
    preferred = []
    fallback = []
    for record in records:
        for exe in _candidate_exes(record.path):
            if os.path.isfile(exe):
                row = (os.path.abspath(exe), record.internal_name)
                if "pgpatcher" in _norm(record.display_name) or "parallaxgen" in _norm(record.display_name):
                    preferred.append(row)
                else:
                    fallback.append(row)
                break
    if preferred:
        return preferred[0]
    if fallback:
        return fallback[0]

    # Support external/common placements without recursively walking the whole
    # mod list or disk.
    roots = []
    try:
        base = os.path.abspath(str(organizer.basePath() or ""))
    except Exception:
        base = ""
    try:
        mods = os.path.abspath(str(organizer.modsPath() or ""))
    except Exception:
        mods = ""
    for root in [base, os.path.dirname(base) if base else "", mods]:
        if root and root not in roots:
            roots.append(root)
    for root in roots:
        for rel in [
            os.path.join("PGPatcher", "PGPatcher.exe"),
            os.path.join("Tools", "PGPatcher", "PGPatcher.exe"),
            os.path.join("tools", "PGPatcher", "PGPatcher.exe"),
        ]:
            exe = os.path.join(root, rel)
            if os.path.isfile(exe):
                return os.path.abspath(exe), None
    return None, None


def _matching_mods(organizer, predicate) -> List[str]:
    service = ModStateService(organizer)
    result = []
    for name in service.all_names(profile_priority=True):
        display = service.display_name(name)
        if predicate(_norm(name), _norm(display)):
            result.append(name)
    return result


def output_mods(organizer) -> List[str]:
    def match(name: str, display: str) -> bool:
        for value in (name, display):
            if ("pgpatcher" in value or "parallaxgen" in value) and "output" in value:
                return True
        return False
    return _matching_mods(organizer, match)


def active_output_mods(organizer) -> List[str]:
    service = ModStateService(organizer)
    return [name for name in output_mods(organizer) if service.is_active(name, default=False)]


def active_dyndolod_outputs(organizer) -> List[str]:
    service = ModStateService(organizer)
    names = _matching_mods(
        organizer,
        lambda name, display: any("dyndolod" in value and "output" in value for value in (name, display)),
    )
    return [name for name in names if service.is_active(name, default=False)]


def active_texgen_outputs(organizer) -> List[str]:
    service = ModStateService(organizer)
    names = _matching_mods(
        organizer,
        lambda name, display: any("texgen" in value and "output" in value for value in (name, display)),
    )
    return [name for name in names if service.is_active(name, default=False)]


def recommended_output_path(organizer) -> str:
    try:
        mods = str(organizer.modsPath() or "")
    except Exception:
        mods = ""
    if not mods:
        return RECOMMENDED_OUTPUT_NAME
    return os.path.abspath(os.path.join(mods, RECOMMENDED_OUTPUT_NAME))


def mo2_instance_path(organizer) -> str:
    try:
        return os.path.abspath(str(organizer.basePath() or ""))
    except Exception:
        return ""


def inspect(organizer) -> Dict[str, object]:
    exe, owner = find_pgpatcher(organizer)
    instance = mo2_instance_path(organizer)
    return {
        "exe": exe or "",
        "owner_mod": owner or "",
        "instance_path": instance,
        "instance_ini_ok": bool(instance and os.path.isfile(os.path.join(instance, "ModOrganizer.ini"))),
        "recommended_output": recommended_output_path(organizer),
        "active_outputs": active_output_mods(organizer),
        "active_dyndolod": active_dyndolod_outputs(organizer),
        "active_texgen": active_texgen_outputs(organizer),
    }


def disable_output_mods(organizer, names: List[str]) -> Tuple[bool, List[str]]:
    if not names:
        return True, []
    result = ModStateService(organizer).set_active(names, False, verify=False)
    return result.ok, list(result.failures)


def launch(organizer, exe_path: str):
    """Launch PGPatcher under the active MO2 VFS without forcing another profile."""
    exe = os.path.abspath(str(exe_path or ""))
    if not exe or not os.path.isfile(exe):
        raise RuntimeError("未找到 PGPatcher.exe")
    cwd = os.path.dirname(exe)
    handle = organizer.startApplication(exe, [], cwd)
    if handle is None or handle is False:
        raise RuntimeError("MO2 startApplication 返回启动失败")
    try:
        value = int(handle)
        if value == 0 or value == -1:
            raise RuntimeError("MO2 startApplication 返回无效进程句柄")
    except (TypeError, ValueError):
        pass
    return handle
