# -*- coding: utf-8 -*-
"""Transactional safe repair for the XingLi modpack health baseline (v2.1.0).

The repair layer deliberately limits itself to reversible operations:
- MOD active/inactive state through ModTransactionService;
- MOD left-pane priority when the baseline/current MOD set is identical;
- exact baseline copies of selected profile files.

Missing/added MODs, deleting files, downloading MODs, save games, and ambiguous
load-order repairs are never automatic.
"""

import base64
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .mod_groups import assistant_data_root
from .mod_state_service import ModStateService
from .mod_transaction import ModTransactionService
from .stability import get_logger

logger = get_logger("modpack_repair")

REPAIR_SCHEMA = 1
PENDING_FILE = "pending_health_repair.json"
LAST_FILE = "last_health_repair.json"
PROFILE_FILES = ("plugins.txt", "loadorder.txt", "Skyrim.ini", "SkyrimPrefs.ini", "SkyrimCustom.ini")
PLUGIN_ORDER_FILES = {"plugins.txt", "loadorder.txt"}


@dataclass
class RepairPlan:
    label: str = "整合包安全修复"
    state_targets: Dict[str, bool] = field(default_factory=dict)
    priority_targets: Dict[str, int] = field(default_factory=dict)
    file_targets: Dict[str, str] = field(default_factory=dict)  # filename -> base64 exact bytes
    safe_items: List[str] = field(default_factory=list)
    manual_items: List[str] = field(default_factory=list)
    safe_issue_count: int = 0
    manual_issue_count: int = 0

    @property
    def has_safe_repairs(self) -> bool:
        return bool(self.state_targets or self.priority_targets or self.file_targets)


@dataclass
class RepairResult:
    ok: bool
    changed_states: int = 0
    changed_priorities: int = 0
    changed_files: int = 0
    failures: List[str] = field(default_factory=list)
    rolled_back: bool = False
    rollback_failures: List[str] = field(default_factory=list)
    transaction_id: str = ""


class ModpackRepairService:
    def __init__(self, organizer):
        self.organizer = organizer
        self.mod_state = ModStateService(organizer)
        self.mod_tx = ModTransactionService(organizer, self.mod_state)
        self.root = os.path.join(assistant_data_root(organizer), "modpack")
        self.pending_path = os.path.join(self.root, PENDING_FILE)
        self.last_path = os.path.join(self.root, LAST_FILE)
        os.makedirs(self.root, exist_ok=True)

    def profile_path(self) -> str:
        try:
            path = str(self.organizer.profilePath())
        except Exception:
            path = ""
        if not path or not os.path.isdir(path):
            raise RuntimeError("无法读取当前 MO2 配置目录。")
        return os.path.abspath(path)

    @staticmethod
    def _baseline_mods(baseline: Dict[str, object]) -> List[Dict[str, object]]:
        env = baseline.get("environment", {}) if isinstance(baseline, dict) else {}
        mods = env.get("mods", []) if isinstance(env, dict) else []
        return [item for item in mods if isinstance(item, dict)]

    @staticmethod
    def _baseline_files(baseline: Dict[str, object]) -> Dict[str, Dict[str, object]]:
        env = baseline.get("environment", {}) if isinstance(baseline, dict) else {}
        files = env.get("profile_files", {}) if isinstance(env, dict) else {}
        if not isinstance(files, dict):
            return {}
        return {str(k): v for k, v in files.items() if isinstance(v, dict)}

    def build_plan(self, baseline: Dict[str, object], current: Dict[str, object], report: Dict[str, object]) -> RepairPlan:
        plan = RepairPlan()

        # Reversible MOD state differences are safe. Missing MODs never appear in
        # state_changes, and Essential protection remains enforced by the central
        # transaction service.
        for item in report.get("state_changes", []) or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            expected = item.get("expected")
            if name and isinstance(expected, bool):
                plan.state_targets[name] = expected
        if plan.state_targets:
            plan.safe_items.append("修复 MOD 启停状态：{} 个".format(len(plan.state_targets)))
            plan.safe_issue_count += len(plan.state_targets)

        missing = [str(x) for x in (report.get("missing_mods", []) or []) if str(x)]
        added = [str(x) for x in (report.get("added_mods", []) or []) if str(x)]
        if missing:
            plan.manual_items.append("缺少 MOD：{} 个（不会自动下载或安装）".format(len(missing)))
            plan.manual_issue_count += len(missing)
        if added:
            plan.manual_items.append("新增 MOD：{} 个（不会自动删除或禁用）".format(len(added)))
            plan.manual_issue_count += len(added)

        # Left-pane priority is only deterministic when the MOD set is unchanged
        # and the baseline was created by v2.1+ with numeric priorities.
        if bool(report.get("mod_order_changed")):
            base_mods = self._baseline_mods(baseline)
            can_repair_order = not missing and not added and bool(base_mods)
            expected_priorities: Dict[str, int] = {}
            if can_repair_order:
                for item in base_mods:
                    name = str(item.get("name", "")).strip()
                    priority = item.get("priority")
                    if not name or not isinstance(priority, int) or priority < 0:
                        can_repair_order = False
                        break
                    expected_priorities[name] = int(priority)
            if can_repair_order:
                for name, expected in expected_priorities.items():
                    current_priority = self.mod_state.priority(name, default=-1)
                    if current_priority != expected:
                        plan.priority_targets[name] = expected
                if plan.priority_targets:
                    plan.safe_items.append("恢复 MOD 左侧顺序")
                    plan.safe_issue_count += 1
            else:
                reason = "MOD 集合有新增/缺失" if (missing or added) else "基准版本过旧，缺少顺序快照"
                plan.manual_items.append("MOD 顺序变化（{}，不自动移动）".format(reason))
                plan.manual_issue_count += 1

        # Profile file repair is exact-byte restore only. Plugin/loadorder files are
        # considered ambiguous when the player added/removed MODs, because restoring
        # them could silently disable plugins belonging to those changes.
        baseline_files = self._baseline_files(baseline)
        for filename in report.get("profile_file_changes", []) or []:
            filename = str(filename)
            if filename not in PROFILE_FILES:
                continue
            info = baseline_files.get(filename, {})
            baseline_exists = bool(info.get("exists"))
            content_b64 = str(info.get("content_b64", "") or "")
            if not baseline_exists:
                plan.manual_items.append("{}：基准中不存在该文件，不自动删除当前文件".format(filename))
                plan.manual_issue_count += 1
                continue
            if not content_b64:
                plan.manual_items.append("{}：旧基准没有可回滚原文，请先更新基准".format(filename))
                plan.manual_issue_count += 1
                continue
            if filename in PLUGIN_ORDER_FILES and (missing or added):
                plan.manual_items.append("{}：MOD 集合有新增/缺失，避免影响玩家自装内容，不自动覆盖".format(filename))
                plan.manual_issue_count += 1
                continue
            try:
                base64.b64decode(content_b64.encode("ascii"), validate=True)
            except Exception:
                plan.manual_items.append("{}：基准内容损坏，不自动覆盖".format(filename))
                plan.manual_issue_count += 1
                continue
            plan.file_targets[filename] = content_b64
            plan.safe_items.append("恢复 {}".format(filename))
            plan.safe_issue_count += 1

        return plan

    def _snapshot_file(self, filename: str) -> Dict[str, object]:
        path = os.path.join(self.profile_path(), filename)
        if not os.path.isfile(path):
            return {"exists": False, "content_b64": ""}
        with open(path, "rb") as handle:
            data = handle.read()
        return {"exists": True, "content_b64": base64.b64encode(data).decode("ascii")}

    @staticmethod
    def _write_exact(path: str, data: bytes) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp = path + ".xingli-repair.tmp"
        with open(temp, "wb") as handle:
            handle.write(data)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except Exception:
                pass
        os.replace(temp, path)

    def _restore_file_snapshot(self, filename: str, snapshot: Dict[str, object]) -> None:
        path = os.path.join(self.profile_path(), filename)
        if bool(snapshot.get("exists")):
            raw = base64.b64decode(str(snapshot.get("content_b64", "")).encode("ascii"), validate=True)
            self._write_exact(path, raw)
        else:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass

    def _set_priorities(self, targets: Dict[str, int]) -> int:
        if not targets:
            return 0
        ml = self.organizer.modList()
        changed = 0
        # Move from low numeric priority to high so the already-fixed prefix is
        # stable in the normal MO2 left-pane ordering.
        for name, target in sorted(targets.items(), key=lambda pair: (pair[1], pair[0].lower())):
            current = self.mod_state.priority(name, default=-1)
            if current == int(target):
                continue
            ok = bool(ml.setPriority(str(name), int(target)))
            if not ok:
                raise RuntimeError("MO2 无法移动 MOD：{} → {}".format(name, target))
            changed += 1
        return changed

    def _verify_priorities(self, targets: Dict[str, int]) -> List[str]:
        failures = []
        for name, expected in targets.items():
            actual = self.mod_state.priority(name, default=-1)
            if actual != int(expected):
                failures.append("MOD 顺序校验失败：{} 期望 {}，当前 {}".format(name, expected, actual))
        return failures

    def _verify_files(self, targets: Dict[str, str]) -> List[str]:
        failures = []
        profile = self.profile_path()
        for filename, encoded in targets.items():
            path = os.path.join(profile, filename)
            try:
                expected = base64.b64decode(encoded.encode("ascii"), validate=True)
                with open(path, "rb") as handle:
                    actual = handle.read()
                if actual != expected:
                    failures.append("配置文件校验失败：{}".format(filename))
            except Exception as exc:
                failures.append("配置文件校验失败：{} ({})".format(filename, exc))
        return failures

    def _write_pending(self, data: Dict[str, object]) -> None:
        temp = self.pending_path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(temp, self.pending_path)

    def load_pending(self) -> Dict[str, object]:
        try:
            with open(self.pending_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def clear_pending(self) -> None:
        try:
            if os.path.isfile(self.pending_path):
                os.remove(self.pending_path)
        except OSError:
            pass

    def _write_last(self, data: Dict[str, object]) -> None:
        temp = self.last_path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(temp, self.last_path)

    def _refresh(self) -> None:
        try:
            self.organizer.refresh(True)
        except TypeError:
            self.organizer.refresh()

    def _rollback_snapshot(self, snapshot: Dict[str, object]) -> List[str]:
        failures: List[str] = []
        files = snapshot.get("before_files", {}) if isinstance(snapshot, dict) else {}
        priorities = snapshot.get("before_priorities", {}) if isinstance(snapshot, dict) else {}
        states = snapshot.get("before_states", {}) if isinstance(snapshot, dict) else {}

        if isinstance(files, dict):
            for filename, info in files.items():
                if not isinstance(info, dict):
                    continue
                try:
                    self._restore_file_snapshot(str(filename), info)
                except Exception as exc:
                    failures.append("恢复 {} 失败：{}".format(filename, exc))

        if isinstance(priorities, dict):
            try:
                self._set_priorities({str(k): int(v) for k, v in priorities.items()})
            except Exception as exc:
                failures.append("恢复 MOD 顺序失败：{}".format(exc))

        if isinstance(states, dict):
            try:
                result = self.mod_state.restore_states({str(k): bool(v) for k, v in states.items()}, verify=False)
                failures.extend("恢复 MOD 状态失败：{}".format(x) for x in result.failures)
            except Exception as exc:
                failures.append("恢复 MOD 状态失败：{}".format(exc))

        try:
            self._refresh()
        except Exception as exc:
            failures.append("刷新 MO2 失败：{}".format(exc))
        return failures

    def recover_pending(self) -> RepairResult:
        pending = self.load_pending()
        if not pending:
            return RepairResult(False, failures=["没有未完成的整合包修复操作"])
        rollback_failures = self._rollback_snapshot(pending)
        if not rollback_failures:
            self.clear_pending()
            # The nested MOD state transaction may have been interrupted as well.
            self.mod_tx.clear_pending()
        return RepairResult(
            ok=not rollback_failures,
            failures=[] if not rollback_failures else ["未完成修复恢复不完整"],
            rolled_back=True,
            rollback_failures=rollback_failures,
            transaction_id=str(pending.get("transaction_id", "")),
        )

    def apply(self, plan: RepairPlan) -> RepairResult:
        if not plan.has_safe_repairs:
            return RepairResult(True)

        transaction_id = uuid.uuid4().hex
        state_plan = self.mod_tx.plan_targets(plan.state_targets, label=plan.label)
        before_priorities = {name: self.mod_state.priority(name, default=-1) for name in plan.priority_targets}
        before_files = {filename: self._snapshot_file(filename) for filename in plan.file_targets}
        snapshot: Dict[str, object] = {
            "schema": REPAIR_SCHEMA,
            "transaction_id": transaction_id,
            "label": plan.label,
            "started_at": time.time(),
            "before_states": state_plan.before_states,
            "before_priorities": before_priorities,
            "before_files": before_files,
            "target_states": plan.state_targets,
            "target_priorities": plan.priority_targets,
            "target_files": plan.file_targets,
            "status": "pending",
        }
        try:
            self._write_pending(snapshot)
        except Exception as exc:
            return RepairResult(False, failures=["无法写入修复事务记录：{}".format(exc)], transaction_id=transaction_id)

        result = RepairResult(False, transaction_id=transaction_id)
        failures: List[str] = []
        try:
            state_result = self.mod_tx.apply(state_plan, auto_rollback=True)
            result.changed_states = int(state_result.changed)
            if not state_result.ok:
                failures.extend(state_result.failures or ["MOD 状态事务失败"])
                failures.extend(state_result.rollback_failures)
                raise RuntimeError("；".join(failures))

            result.changed_priorities = self._set_priorities(plan.priority_targets)

            profile = self.profile_path()
            for filename, encoded in plan.file_targets.items():
                raw = base64.b64decode(encoded.encode("ascii"), validate=True)
                self._write_exact(os.path.join(profile, filename), raw)
                result.changed_files += 1

            self._refresh()

            # Verification is intentionally strict. Any partial repair becomes a
            # failed transaction and the complete pre-repair snapshot is restored.
            for name, expected in plan.state_targets.items():
                if self.mod_state.is_active(name, default=not expected) != bool(expected):
                    failures.append("MOD 状态校验失败：{}".format(name))
            failures.extend(self._verify_priorities(plan.priority_targets))
            failures.extend(self._verify_files(plan.file_targets))
            if failures:
                raise RuntimeError("；".join(failures))

        except Exception as exc:
            if not failures:
                failures.append(str(exc))
            rollback_failures = self._rollback_snapshot(snapshot)
            self.mod_tx.clear_last()
            if not rollback_failures:
                self.clear_pending()
                self.mod_tx.clear_pending()
            result.ok = False
            result.failures = failures
            result.rolled_back = True
            result.rollback_failures = rollback_failures
            logger.error("Health repair failed; rollback=%s failures=%s", rollback_failures, failures)
            return result

        self.clear_pending()
        committed = dict(snapshot)
        committed["status"] = "committed"
        committed["committed_at"] = time.time()
        try:
            self._write_last(committed)
        except Exception as exc:
            logger.warning("无法保存上次健康修复记录：%s", exc)
        result.ok = True
        logger.info(
            "Health repair committed id=%s states=%d priorities=%d files=%d",
            transaction_id,
            result.changed_states,
            result.changed_priorities,
            result.changed_files,
        )
        return result
