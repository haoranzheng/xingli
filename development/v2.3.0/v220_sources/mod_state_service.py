# -*- coding: utf-8 -*-
"""Central MO2 2.4.4-compatible MOD state authority for XingLi Assistant.

All XingLi modules should use this service instead of interpreting mobase.ModState
or IModList state flags independently.  The service deliberately avoids
IPluginList and map/QFlags state-change callbacks, which are unsafe on the
MO2 2.4.4 Boost.Python bridge used by this project.
"""

import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

import mobase


_FLAG_VALUES = {
    "EXISTS": 0x00000001,
    "ACTIVE": 0x00000002,
    "ESSENTIAL": 0x00000004,
    "EMPTY": 0x00000008,
    "ENDORSED": 0x00000010,
    "VALID": 0x00000020,
    "ALTERNATE": 0x00000040,
}


@dataclass
class ModRecord:
    internal_name: str
    display_name: str
    active: bool
    priority: int
    essential: bool
    valid: bool
    managed: bool
    path: str


@dataclass
class ModStateChangeResult:
    requested: int
    changed: int
    skipped: int
    failures: List[str]

    @property
    def ok(self) -> bool:
        return not self.failures


class ModStateService:
    """Single source of truth for MOD state reads/writes.

    MO2 API calls are intentionally synchronous and must be made from the GUI
    thread.  Worker threads may consume snapshots produced by this service, but
    must never call its methods against IOrganizer/IModList directly.
    """

    def __init__(self, organizer):
        self.organizer = organizer

    @staticmethod
    def _canonical_flag(flag_name: str) -> str:
        name = str(flag_name or "").upper()
        if name.startswith("STATE_"):
            name = name[6:]
        return name

    @classmethod
    def resolve_flag(cls, flag_name: str):
        canonical = cls._canonical_flag(flag_name)
        enum_type = getattr(mobase, "ModState", None)
        if enum_type is not None:
            for attr in (canonical, canonical.capitalize(), canonical.lower(), "STATE_" + canonical):
                try:
                    return getattr(enum_type, attr)
                except (AttributeError, TypeError):
                    continue
        return _FLAG_VALUES.get(canonical, 0)

    @classmethod
    def state_has(cls, state, flag_name: str) -> bool:
        canonical = cls._canonical_flag(flag_name)
        flag = cls.resolve_flag(canonical)
        if flag:
            try:
                return bool(state & flag)
            except Exception:
                try:
                    return bool(int(state) & int(flag))
                except Exception:
                    pass
        fallback = _FLAG_VALUES.get(canonical, 0)
        if fallback:
            try:
                return bool(int(state) & fallback)
            except Exception:
                try:
                    return bool(state & fallback)
                except Exception:
                    pass
        text = (repr(state) + " " + str(state)).upper()
        if canonical == "ACTIVE":
            if "INACTIVE" in text:
                return False
            return "ACTIVE" in text
        return canonical in text

    def mod_list(self):
        return self.organizer.modList()

    def all_names(self, profile_priority: bool = True) -> List[str]:
        ml = self.mod_list()
        try:
            raw = ml.allModsByProfilePriority() if profile_priority else ml.allMods()
        except TypeError:
            # Some older bindings expose a profile argument overload only.
            try:
                raw = ml.allModsByProfilePriority(self.organizer.profile()) if profile_priority else ml.allMods()
            except Exception:
                raw = ml.allMods()
        except Exception:
            raw = ml.allMods()
        result = []
        seen = set()
        for value in raw:
            name = str(value)
            if name and name not in seen:
                seen.add(name)
                result.append(name)
        return result

    def raw_state(self, name: str):
        return self.mod_list().state(str(name))

    def is_active(self, name: str, default: bool = False) -> bool:
        try:
            return self.state_has(self.raw_state(name), "ACTIVE")
        except Exception:
            return bool(default)

    def is_essential(self, name: str, default: bool = False) -> bool:
        try:
            return self.state_has(self.raw_state(name), "ESSENTIAL")
        except Exception:
            return bool(default)

    def is_valid(self, name: str, default: bool = False) -> bool:
        try:
            return self.state_has(self.raw_state(name), "VALID")
        except Exception:
            return bool(default)

    def priority(self, name: str, default: int = -1) -> int:
        try:
            return int(self.mod_list().priority(str(name)))
        except Exception:
            return int(default)

    def display_name(self, name: str) -> str:
        try:
            return str(self.mod_list().displayName(str(name))) or str(name)
        except Exception:
            return str(name)

    def mod_path(self, name: str) -> str:
        try:
            mod = self.mod_list().getMod(str(name))
            if mod:
                return str(mod.absolutePath())
        except Exception:
            pass
        return ""

    def is_managed_path(self, path: str) -> bool:
        if not path:
            return False
        try:
            mods_root = os.path.normcase(os.path.abspath(str(self.organizer.modsPath())))
            absolute = os.path.normcase(os.path.abspath(str(path)))
            return os.path.commonpath([mods_root, absolute]) == mods_root and absolute != mods_root
        except Exception:
            return False

    def record(self, name: str) -> ModRecord:
        path = self.mod_path(name)
        try:
            state = self.raw_state(name)
        except Exception:
            state = 0
        return ModRecord(
            internal_name=str(name),
            display_name=self.display_name(name),
            active=self.state_has(state, "ACTIVE"),
            priority=self.priority(name),
            essential=self.state_has(state, "ESSENTIAL"),
            valid=self.state_has(state, "VALID") or bool(path),
            managed=self.is_managed_path(path),
            path=path,
        )

    def records(self, profile_priority: bool = True) -> List[ModRecord]:
        return [self.record(name) for name in self.all_names(profile_priority=profile_priority)]

    def active_records(self) -> List[ModRecord]:
        return [record for record in self.records(profile_priority=True) if record.active]

    def snapshot_states(self, names: Iterable[str]) -> Dict[str, bool]:
        result = {}
        for raw in names:
            name = str(raw)
            if name and name not in result:
                result[name] = self.is_active(name, default=False)
        return result

    def set_active(self, names: Iterable[str], active: bool, verify: bool = True) -> ModStateChangeResult:
        """Enable/disable MODs through the safest public MO2 API path.

        MO2 2.4.4 exposes overloaded ``setActive(QString, bool)`` and
        ``setActive(QStringList, bool)`` methods through Boost.Python.  For a
        single MOD we must use the scalar overload explicitly; wrapping one
        name in a Python list needlessly enters overload conversion code and
        has proven unstable in real 2.4.4 instances.

        ``verify=False`` is used by interactive UI actions so the hot path does
        not immediately re-enter MO2 state/VFS reads after the mutation.
        """
        requested_names = []
        seen = set()
        for raw in names:
            name = str(raw)
            if name and name not in seen:
                seen.add(name)
                requested_names.append(name)

        target = bool(active)
        to_change = []
        protected = []
        for name in requested_names:
            current = self.is_active(name, default=not target)
            if current == target:
                continue
            if (not target) and self.is_essential(name, default=False):
                protected.append(name)
                continue
            to_change.append(name)

        skipped = len(requested_names) - len(to_change) - len(protected)
        if not to_change:
            return ModStateChangeResult(len(requested_names), 0, skipped, protected)

        ml = self.mod_list()

        # Critical MO2 2.4.4 compatibility path: NEVER call the QStringList
        # overload for one MOD. This mirrors a normal single checkbox toggle.
        if len(to_change) == 1:
            name = to_change[0]
            try:
                result = ml.setActive(name, target)
                if result is False:
                    return ModStateChangeResult(len(requested_names), 0, skipped, protected + [name])
                if verify and self.is_active(name, default=not target) != target:
                    return ModStateChangeResult(len(requested_names), 0, skipped, protected + [name])
                return ModStateChangeResult(len(requested_names), 1, skipped, protected)
            except Exception:
                return ModStateChangeResult(len(requested_names), 0, skipped, protected + [name])

        # Multiple MODs: the native QStringList overload performs one VFS
        # re-evaluation instead of one per MOD. Keep it for true batch work.
        try:
            result = ml.setActive(to_change, target)
            changed = int(result) if result is not None else len(to_change)
            if not verify:
                if changed < 0:
                    changed = 0
                return ModStateChangeResult(len(requested_names), min(changed, len(to_change)), skipped, protected)
            failures = list(protected) + [name for name in to_change if self.is_active(name, default=not target) != target]
            actual_changed = len(to_change) - len([name for name in failures if name in to_change])
            if changed <= 1 and len(to_change) > 1:
                changed = actual_changed
            return ModStateChangeResult(len(requested_names), changed, skipped, failures)
        except Exception:
            pass

        changed = 0
        failures = list(protected)
        for name in to_change:
            try:
                result = ml.setActive(name, target)
                if result is False:
                    failures.append(name)
                elif not verify or self.is_active(name, default=not target) == target:
                    changed += 1
                else:
                    failures.append(name)
            except Exception:
                failures.append(name)
        return ModStateChangeResult(len(requested_names), changed, skipped, failures)

    def restore_states(self, states: Dict[str, bool], verify: bool = True) -> ModStateChangeResult:
        enable = [name for name, value in states.items() if bool(value)]
        disable = [name for name, value in states.items() if not bool(value)]
        a = self.set_active(disable, False, verify=verify)
        b = self.set_active(enable, True, verify=verify)
        return ModStateChangeResult(
            requested=a.requested + b.requested,
            changed=a.changed + b.changed,
            skipped=a.skipped + b.skipped,
            failures=a.failures + b.failures,
        )
