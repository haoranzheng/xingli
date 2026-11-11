# -*- coding: utf-8 -*-
"""Transactional MOD state changes for XingLi Assistant v1.9.1.

Transactions intentionally cover only reversible enable/disable state. Physical
MOD uninstall is not represented as reversible because removeMod() deletes files.
"""

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from .mod_groups import assistant_data_root
from .mod_state_service import ModStateChangeResult, ModStateService
from .stability import get_logger

logger = get_logger("mod_transaction")


@dataclass
class StateTransactionPlan:
    label: str
    before_states: Dict[str, bool]
    target_states: Dict[str, bool]
    changes: Dict[str, bool]
    skipped: int

    @property
    def change_count(self) -> int:
        return len(self.changes)


@dataclass
class StateTransactionResult:
    ok: bool
    changed: int
    failures: List[str]
    rolled_back: bool
    rollback_failures: List[str]
    transaction_id: str = ""


class ModTransactionService:
    def __init__(self, organizer, mod_state: Optional[ModStateService] = None):
        self.organizer = organizer
        self.mod_state = mod_state or ModStateService(organizer)
        self.root = os.path.join(assistant_data_root(organizer), "transactions")
        self.last_path = os.path.join(self.root, "last_state_transaction.json")
        self.pending_path = os.path.join(self.root, "pending_state_transaction.json")
        os.makedirs(self.root, exist_ok=True)

    @staticmethod
    def _unique(values: Iterable[str]) -> List[str]:
        result = []
        seen = set()
        for raw in values or []:
            value = str(raw).strip()
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return result

    def plan_set_active(self, names: Iterable[str], active: bool, label: str = "MOD 状态操作") -> StateTransactionPlan:
        return self.plan_targets({name: bool(active) for name in self._unique(names)}, label=label)

    def plan_targets(self, targets: Dict[str, bool], label: str = "MOD 状态操作") -> StateTransactionPlan:
        normalized = {}
        for raw_name, raw_value in (targets or {}).items():
            name = str(raw_name).strip()
            if name:
                normalized[name] = bool(raw_value)
        before = self.mod_state.snapshot_states(normalized.keys())
        changes = {name: target for name, target in normalized.items() if before.get(name, not target) != target}
        return StateTransactionPlan(
            label=str(label or "MOD 状态操作"),
            before_states=before,
            target_states=normalized,
            changes=changes,
            skipped=max(0, len(normalized) - len(changes)),
        )


    def _write_pending(self, plan: StateTransactionPlan, transaction_id: str) -> None:
        data = {
            "version": 1,
            "transaction_id": transaction_id,
            "label": plan.label,
            "started_at": time.time(),
            "before_states": plan.before_states,
            "target_states": plan.target_states,
            "changes": plan.changes,
            "status": "pending",
        }
        tmp = self.pending_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, self.pending_path)

    def load_pending(self) -> Dict:
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

    def recover_pending(self) -> StateTransactionResult:
        pending = self.load_pending()
        before = pending.get("before_states", {}) if isinstance(pending, dict) else {}
        if not isinstance(before, dict) or not before:
            return StateTransactionResult(False, 0, ["没有可恢复的未完成事务"], False, [])
        try:
            result = self.mod_state.restore_states({str(name): bool(value) for name, value in before.items()}, verify=False)
            failures = list(result.failures)
            if result.changed + result.skipped < result.requested and not failures:
                failures.append("MO2 未能完整恢复 pending 事务")
            if failures:
                return StateTransactionResult(False, result.changed, failures, True, failures)
            self.clear_pending()
            logger.info("Recovered pending MOD state transaction: %s", pending.get("label", ""))
            return StateTransactionResult(True, result.changed, [], True, [])
        except Exception as exc:
            return StateTransactionResult(False, 0, [str(exc)], True, [str(exc)])

    def _write_last(self, plan: StateTransactionPlan, transaction_id: str) -> None:
        data = {
            "version": 1,
            "transaction_id": transaction_id,
            "label": plan.label,
            "committed_at": time.time(),
            "before_states": plan.before_states,
            "after_states": plan.target_states,
        }
        tmp = self.last_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, self.last_path)

    def load_last(self) -> Dict:
        try:
            with open(self.last_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def clear_last(self) -> None:
        try:
            if os.path.isfile(self.last_path):
                os.remove(self.last_path)
        except OSError:
            pass

    def apply(self, plan: StateTransactionPlan, auto_rollback: bool = True) -> StateTransactionResult:
        if not plan.changes:
            return StateTransactionResult(True, 0, [], False, [])

        disable = [name for name, active in plan.changes.items() if not active]
        enable = [name for name, active in plan.changes.items() if active]
        failures: List[str] = []
        changed = 0
        transaction_id = uuid.uuid4().hex
        try:
            self._write_pending(plan, transaction_id)
        except Exception as exc:
            logger.error("Could not persist pending MOD transaction: %s", exc)
            return StateTransactionResult(False, 0, ["无法写入事务日志：{}".format(exc)], False, [])

        def apply_side(names: List[str], active: bool) -> None:
            nonlocal changed
            if not names:
                return
            result = self.mod_state.set_active(names, active, verify=False)
            changed += int(result.changed)
            failures.extend(result.failures)
            # Native batch setActive returns the count but not failed identities.
            # A short count is still a transaction failure and triggers full rollback.
            if result.changed < len(names) and not result.failures:
                failures.append("{}：MO2 仅完成 {}/{}".format("启用" if active else "禁用", result.changed, len(names)))

        try:
            apply_side(disable, False)
            if not failures:
                apply_side(enable, True)
        except Exception as exc:
            failures.append(str(exc))

        rolled_back = False
        rollback_failures: List[str] = []
        if failures and auto_rollback:
            rolled_back = True
            try:
                rollback = self.mod_state.restore_states(plan.before_states, verify=False)
                rollback_failures.extend(rollback.failures)
                if rollback.changed + rollback.skipped < rollback.requested and not rollback.failures:
                    rollback_failures.append("MO2 未能完整恢复操作前状态")
            except Exception as exc:
                rollback_failures.append(str(exc))
            if not rollback_failures:
                self.clear_pending()
            logger.error("MOD transaction failed and rollback attempted: %s failures=%s rollback=%s", plan.label, failures, rollback_failures)
            return StateTransactionResult(False, changed, failures, rolled_back, rollback_failures, transaction_id)

        if failures:
            return StateTransactionResult(False, changed, failures, False, [], transaction_id)

        self.clear_pending()
        try:
            self._write_last(plan, transaction_id)
        except Exception as exc:
            logger.warning("Could not persist last MOD transaction: %s", exc)
        logger.info("MOD transaction committed: %s id=%s changed=%d", plan.label, transaction_id, changed)
        return StateTransactionResult(True, changed, [], False, [], transaction_id)

    def plan_undo_last(self) -> Optional[StateTransactionPlan]:
        last = self.load_last()
        before = last.get("before_states", {}) if isinstance(last, dict) else {}
        if not isinstance(before, dict) or not before:
            return None
        label = "撤销：{}".format(str(last.get("label", "上次 MOD 状态操作")))
        return self.plan_targets({str(name): bool(value) for name, value in before.items()}, label=label)

    def undo_last(self) -> StateTransactionResult:
        plan = self.plan_undo_last()
        if plan is None:
            return StateTransactionResult(False, 0, ["没有可撤销的状态事务"], False, [])
        result = self.apply(plan, auto_rollback=True)
        if result.ok:
            # Applying undo creates its own last transaction. Clearing avoids an
            # accidental endless toggle loop labelled as a normal undo history.
            self.clear_last()
        return result
