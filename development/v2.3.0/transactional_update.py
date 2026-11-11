# coding: utf-8
"""Crash-resilient updater file transaction (Windows, same-volume plugin install).

This module is intentionally independent of MO2/Qt and runs from an external
updater process AFTER MO2 exits. It never deletes its backup before explicit
health acknowledgement. Configuration is stored outside the replaced plugin.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid
import zipfile


def verify_package(package_path, expected_size, expected_sha256):
    package = Path(package_path)
    if package.stat().st_size != int(expected_size):
        raise ValueError("update size mismatch")
    digest = hashlib.sha256()
    with package.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != str(expected_sha256).lower():
        raise ValueError("update SHA256 mismatch")


def extract_plugin(package_path, staging, plugin_folder="xingli_Little_assistant"):
    staging = Path(staging)
    staging.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(str(package_path)) as z:
        if z.testzip() is not None:
            raise ValueError("update archive is corrupt")
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            parts = name.split("/")
            if not name or name.startswith("/") or any(p in ("", ".", "..") for p in parts):
                raise ValueError("unsafe archive path")
            if parts[0] != plugin_folder:
                raise ValueError("unexpected plugin package root")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("symbolic links are forbidden")
            target = staging.joinpath(*parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as source, target.open("xb") as dest:
                shutil.copyfileobj(source, dest)
    new_plugin = staging / plugin_folder
    if not (new_plugin / "auto_updater.py").is_file():
        raise ValueError("incomplete update archive")
    if not (new_plugin / "consolidation_controller.py").is_file():
        raise ValueError("controller missing from update")
    return new_plugin


def _write_json_atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".update-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare_install(package_path, plugin_dir, state_dir, expected_size, expected_sha256):
    """Prepare files without touching the live plugin."""
    plugin = Path(plugin_dir).resolve(strict=True)
    state = Path(state_dir).resolve()
    if state == plugin or plugin in state.parents:
        raise ValueError("transaction state must live outside the plugin")
    verify_package(package_path, expected_size, expected_sha256)
    txid = uuid.uuid4().hex
    staging = plugin.parent / (".xingli-staging-" + txid)
    prepared = extract_plugin(package_path, staging, plugin.name)
    state.mkdir(parents=True, exist_ok=True)
    record_path = state / "update-transaction.json"
    if record_path.exists():
        raise RuntimeError("pending update exists; recover it before installing another")
    record = {
        "schema": 1, "txid": txid, "phase": "prepared",
        "live": str(plugin), "staging": str(prepared),
        "backup": str(plugin.parent / (".xingli-backup-" + txid)),
        "created_at": int(time.time()),
    }
    _write_json_atomic(record_path, record)
    return record_path


def apply_prepared(record_path):
    """Must be invoked only once MO2 has exited and released all plugin files."""
    record_path = Path(record_path)
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if record["phase"] != "prepared":
        raise RuntimeError("transaction phase mismatch")
    live = Path(record["live"])
    staging = Path(record["staging"])
    backup = Path(record["backup"])
    if backup.exists() or not live.is_dir() or not staging.is_dir():
        raise RuntimeError("unsafe transaction path state")
    # Each state transition is persisted; recovery uses filesystem reality too.
    os.replace(str(live), str(backup))
    try:
        os.replace(str(staging), str(live))
    except BaseException:
        os.replace(str(backup), str(live))
        raise
    record["phase"] = "awaiting_health"
    _write_json_atomic(record_path, record)
    return record


def rollback(record_path):
    """Restore the exact previous plugin tree; quarantine the unsuccessful update."""
    record_path = Path(record_path)
    if not record_path.exists():
        return False
    record = json.loads(record_path.read_text(encoding="utf-8"))
    live = Path(record["live"])
    backup = Path(record["backup"])
    if not backup.is_dir():
        if live.is_dir() and record.get("phase") == "prepared":
            return False  # Update was never applied.
        raise RuntimeError("rollback backup missing; manual recovery required")
    failed = live.parent / (".xingli-failed-" + record["txid"])
    if failed.exists():
        raise RuntimeError("failed-version quarantine already exists")
    if live.exists():
        os.replace(str(live), str(failed))
    os.replace(str(backup), str(live))
    record["phase"] = "rolled_back"
    _write_json_atomic(record_path, record)
    return True


def acknowledge_healthy(record_path, txid):
    """Call only after MO2 has initialized the upgraded plugin successfully."""
    record_path = Path(record_path)
    if not record_path.is_file():
        return False
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if record.get("txid") != txid or record.get("phase") != "awaiting_health":
        return False
    record["phase"] = "healthy"
    _write_json_atomic(record_path, record)
    return True


def cleanup_confirmed(record_path):
    """Never remove backups until the health marker exists."""
    record_path = Path(record_path)
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if record.get("phase") != "healthy":
        raise RuntimeError("health not acknowledged")
    backup = Path(record["backup"])
    if backup.is_dir():
        shutil.rmtree(str(backup))
    staging_parent = Path(record["staging"]).parent
    if staging_parent.exists() and staging_parent.name.startswith(".xingli-staging-"):
        shutil.rmtree(str(staging_parent))
    record_path.unlink()
