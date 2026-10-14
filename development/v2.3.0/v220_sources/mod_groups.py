# -*- coding: utf-8 -*-
"""Persistent MOD groups for XingLi Assistant v1.9.1.

Groups are author-facing reusable sets of MO2 internal MOD names. A group may
include other groups. Expansion is deterministic, cycle-safe, and never silently
removes missing MOD names so a modpack author can see drift across revisions.
"""

import json
import os
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .stability import get_logger

logger = get_logger("mod_groups")


def assistant_data_root(organizer) -> str:
    base = ""
    try:
        base = str(organizer.pluginDataPath())
    except Exception:
        pass
    if not base:
        try:
            base = str(organizer.profilePath())
        except Exception:
            base = ""
    if not base:
        base = os.path.dirname(__file__)
    root = os.path.join(base, "xingli_assistant")
    os.makedirs(root, exist_ok=True)
    return root


@dataclass
class GroupExpansion:
    mods: List[str]
    missing_groups: List[str]
    cycles: List[str]


class ModGroupStore:
    SCHEMA_VERSION = 1

    def __init__(self, organizer):
        self.organizer = organizer
        self.root = assistant_data_root(organizer)
        self.path = os.path.join(self.root, "mod_groups.json")

    @staticmethod
    def _clean_names(values: Iterable[str]) -> List[str]:
        result = []
        seen = set()
        for raw in values or []:
            value = str(raw).strip()
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return result

    def _load(self) -> Dict:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, dict):
                raise ValueError("root is not an object")
        except FileNotFoundError:
            raw = {}
        except Exception as exc:
            logger.warning("Failed to read MOD groups: %s", exc)
            raw = {}
        groups = raw.get("groups", {}) if isinstance(raw.get("groups", {}), dict) else {}
        normalized = {}
        for name, value in groups.items():
            if not isinstance(value, dict):
                continue
            group_name = str(name).strip()
            if not group_name:
                continue
            normalized[group_name] = {
                "description": str(value.get("description", "")),
                "mods": self._clean_names(value.get("mods", [])),
                "groups": self._clean_names(value.get("groups", [])),
                "created_at": float(value.get("created_at", 0) or 0),
                "updated_at": float(value.get("updated_at", 0) or 0),
            }
        return {"version": self.SCHEMA_VERSION, "groups": normalized}

    def _save(self, data: Dict) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def list_names(self) -> List[str]:
        return sorted(self._load()["groups"].keys(), key=lambda value: value.casefold())

    def get(self, name: str) -> Optional[Dict]:
        value = self._load()["groups"].get(str(name))
        if value is None:
            return None
        return dict(value)

    def definitions(self) -> Dict[str, Dict]:
        return self._load()["groups"]

    def would_create_cycle(self, name: str, include_groups: Iterable[str]) -> bool:
        target = str(name).strip()
        if not target:
            return True
        graph = {key: list(value.get("groups", [])) for key, value in self.definitions().items()}
        graph[target] = self._clean_names(include_groups)

        visiting: Set[str] = set()
        visited: Set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            for child in graph.get(node, []):
                if child in graph and visit(child):
                    return True
            visiting.remove(node)
            visited.add(node)
            return False

        return visit(target)

    def upsert(self, name: str, mods: Iterable[str], include_groups: Iterable[str] = (), description: str = "") -> None:
        group_name = str(name).strip()
        if not group_name:
            raise ValueError("MOD 组名称不能为空")
        mods_clean = self._clean_names(mods)
        groups_clean = self._clean_names(include_groups)
        if group_name in groups_clean:
            raise ValueError("MOD 组不能直接包含自身")
        if self.would_create_cycle(group_name, groups_clean):
            raise ValueError("MOD 组引用会形成循环依赖")

        data = self._load()
        old = data["groups"].get(group_name, {})
        now = time.time()
        data["groups"][group_name] = {
            "description": str(description or "").strip(),
            "mods": mods_clean,
            "groups": groups_clean,
            "created_at": float(old.get("created_at", 0) or now),
            "updated_at": now,
        }
        self._save(data)
        logger.info("MOD group saved: %s mods=%d child_groups=%d", group_name, len(mods_clean), len(groups_clean))

    def delete(self, name: str) -> bool:
        group_name = str(name).strip()
        data = self._load()
        if group_name not in data["groups"]:
            return False
        del data["groups"][group_name]
        # Keep references in other groups visible as missing references instead of
        # silently mutating author definitions.
        self._save(data)
        logger.info("MOD group deleted: %s", group_name)
        return True

    def expand(self, group_names: Iterable[str]) -> GroupExpansion:
        definitions = self.definitions()
        mods: List[str] = []
        mod_seen: Set[str] = set()
        missing: List[str] = []
        missing_seen: Set[str] = set()
        cycles: List[str] = []
        cycles_seen: Set[str] = set()
        expanded: Set[str] = set()
        stack: List[str] = []

        def walk(name: str) -> None:
            if name in stack:
                cycle = " → ".join(stack + [name])
                if cycle not in cycles_seen:
                    cycles_seen.add(cycle)
                    cycles.append(cycle)
                return
            if name in expanded:
                return
            definition = definitions.get(name)
            if definition is None:
                if name not in missing_seen:
                    missing_seen.add(name)
                    missing.append(name)
                return
            stack.append(name)
            for mod_name in definition.get("mods", []):
                if mod_name not in mod_seen:
                    mod_seen.add(mod_name)
                    mods.append(mod_name)
            for child in definition.get("groups", []):
                walk(str(child))
            stack.pop()
            expanded.add(name)

        for raw in self._clean_names(group_names):
            walk(raw)
        return GroupExpansion(mods=mods, missing_groups=missing, cycles=cycles)
