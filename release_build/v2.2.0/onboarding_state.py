# coding=utf-8

import json
import os
import tempfile


_SCHEMA = 1
_APP_DIR = "XingliAssistant"


def _local_appdata_dir() -> str:
    value = os.environ.get("LOCALAPPDATA", "").strip()
    if value:
        return os.path.abspath(value)
    # LOCALAPPDATA is normally present in MO2 on Windows. This fallback keeps
    # startup functional in unusual environments without writing into the modpack.
    return os.path.abspath(os.path.join(os.path.expanduser("~"), "AppData", "Local"))


def state_path(instance_id: str) -> str:
    return os.path.join(
        _local_appdata_dir(),
        _APP_DIR,
        "instances",
        str(instance_id),
        "onboarding.json",
    )


def _quarantine_corrupt(path: str) -> None:
    if not os.path.isfile(path):
        return
    try:
        os.replace(path, path + ".corrupt")
    except Exception:
        pass


def should_show_welcome(instance_id: str) -> bool:
    path = state_path(instance_id)
    if not os.path.isfile(path):
        return True
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except Exception:
        _quarantine_corrupt(path)
        return True

    if not isinstance(data, dict):
        _quarantine_corrupt(path)
        return True
    if int(data.get("schema", 0) or 0) != _SCHEMA:
        return True
    return not bool(data.get("first_run_completed", False))


def mark_welcome_seen(instance_id: str) -> None:
    path = state_path(instance_id)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(prefix=".onboarding-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {"schema": _SCHEMA, "first_run_completed": True},
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.remove(temp_path)
        except Exception:
            pass
        raise
