# coding: utf-8
"""Explicit, per-MO2-instance confirmation of an already installed Xingli pack.

This module never downloads, extracts, installs, or changes MOD files. It only
updates the installed-version marker after the user explicitly confirms that
the running MO2 instance already contains the remote release.
"""
import json
import os
import re
import tempfile
from datetime import datetime, timezone


PRODUCT_ID = "xingli_modpack"
ROOT_RELEASE_FILE = "xingli_modpack_release.json"
PERSISTENT_RELEASE_FILE = "installed_release.json"
VERSION_RE = re.compile(r"^\d+(?:\.\d+){0,5}$")


def _version_tuple(value):
    value = str(value or "").strip()
    if not VERSION_RE.fullmatch(value):
        raise ValueError("无效的整合包版本号：{}".format(value or "(空)"))
    return tuple(int(part) for part in value.split("."))


def _read_json(path):
    if not path or not os.path.isfile(path):
        return {}
    if os.path.islink(path):
        raise ValueError("版本标记是符号链接，拒绝修改：{}".format(path))
    with open(path, "r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("版本标记格式无效：{}".format(path))
    return value


def _write_json_atomic(path, value):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    if os.path.islink(path):
        raise ValueError("版本标记是符号链接，拒绝覆盖：{}".format(path))
    fd, temporary = tempfile.mkstemp(prefix=".xingli-release-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except OSError:
            pass


def _resolve_paths(controller):
    organizer = getattr(controller, "organizer", None)
    if organizer is None:
        raise RuntimeError("无法取得当前 MO2 实例，未修改版本记录。")

    try:
        base = str(organizer.basePath() or "").strip()
    except Exception:
        base = ""
    if not base:
        plugin_path = os.path.abspath(
            getattr(controller, "plugin_path", os.path.dirname(__file__)))
        base = os.path.abspath(os.path.join(plugin_path, "..", ".."))

    try:
        plugin_data = str(organizer.pluginDataPath() or "").strip()
    except Exception:
        plugin_data = ""
    if plugin_data:
        persistent_dir = os.path.join(plugin_data, "xingli_assistant", "modpack")
    else:
        plugin_path = os.path.abspath(
            getattr(controller, "plugin_path", os.path.dirname(__file__)))
        persistent_dir = os.path.join(plugin_path, "data", "modpack")

    return (
        os.path.join(os.path.abspath(base), ROOT_RELEASE_FILE),
        os.path.join(persistent_dir, PERSISTENT_RELEASE_FILE),
    )


def confirm_installed_version(controller, manifest, current_release):
    """Persist a user-confirmed remote version for this MO2 instance only.

    The package bootstrap file is deliberately never changed: it is only a
    first-run seed. Existing root marker precedence is preserved so that
    load_local_release() cannot read an older root marker over a newer
    pluginData marker.
    """
    if not isinstance(manifest, dict) or not isinstance(current_release, dict):
        raise ValueError("整合包版本信息无效，未修改本地记录。")
    if int(manifest.get("schema", 0) or 0) < 1:
        raise ValueError("远程整合包清单版本无效。")
    if str(manifest.get("product", "")) != PRODUCT_ID:
        raise ValueError("远程清单不是星黎整合包。")
    if not bool(manifest.get("enabled", True)):
        raise ValueError("当前整合包更新公告未启用。")

    remote_pack_id = str(manifest.get("pack_id", "") or "").strip()
    local_pack_id = str(current_release.get("pack_id", "") or "").strip()
    if not remote_pack_id or remote_pack_id != local_pack_id:
        raise ValueError("整合包标识不匹配，拒绝修改本地版本。")

    current_version = str(current_release.get("installed_version", "") or "").strip()
    remote_version = str(manifest.get("version", "") or "").strip()
    if _version_tuple(remote_version) <= _version_tuple(current_version):
        raise ValueError("远程版本并未高于本地版本，无需确认或修改。")

    root_path, persistent_path = _resolve_paths(controller)
    root_record = _read_json(root_path)
    persistent_record = _read_json(persistent_path)

    # This exactly mirrors load_local_release() source precedence.
    root_is_authoritative = bool(
        str(root_record.get("installed_version", "") or "").strip())
    if root_is_authoritative:
        source_record = root_record
    elif str(persistent_record.get("installed_version", "") or "").strip():
        source_record = persistent_record
    else:
        # A legacy first-run migration may not have succeeded previously.
        plugin_path = os.path.abspath(
            getattr(controller, "plugin_path", os.path.dirname(__file__)))
        bootstrap_path = os.path.join(plugin_path, "modpack_release.json")
        source_record = _read_json(bootstrap_path)

    record = dict(source_record or {})
    record.update({
        "schema": int(record.get("schema", 2) or 2),
        "pack_id": remote_pack_id,
        "name": str(manifest.get("name", current_release.get("name", "星黎整合包"))
                    or "星黎整合包").strip(),
        "installed_version": remote_version,
        "confirmed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "confirmation_source": "user_confirmed_installed_release",
        "confirmed_manifest_published_at": str(manifest.get("published_at", "") or ""),
    })

    warning = ""
    if root_is_authoritative:
        # The root marker wins over pluginData on next startup; update it first.
        _write_json_atomic(root_path, record)
        try:
            _write_json_atomic(persistent_path, record)
        except Exception as exc:
            # Root marker is already authoritative and correct. The normal
            # load_local_release() path will retry mirroring it on next launch.
            warning = "根目录版本记录已更新；插件持久记录将在下次启动时重新同步：{}".format(exc)
    else:
        # Do not create a root marker unnecessarily; pluginData is authoritative.
        _write_json_atomic(persistent_path, record)

    return {
        "pack_id": remote_pack_id,
        "installed_version": remote_version,
        "marker_path": root_path if root_is_authoritative else persistent_path,
        "persistent_path": persistent_path,
        "warning": warning,
    }
