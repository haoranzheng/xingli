# coding=utf-8
"""Synchronize Xingli modpack version/update text into Main Menu Customizer.

Safety rules:
- only touches the active MainMenuCustomizer.ini and its configured version list text;
- never changes unrelated Main Menu Customizer options;
- writes are atomic and stay inside the existing winning mod source when possible;
- cached remote data contains only public release metadata, never download secrets or user data.
"""

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

from . import stability

logger = stability.get_logger("mainmenu_sync")

INI_RELATIVE = os.path.join("SKSE", "Plugins", "MainMenuCustomizer.ini")
INTERFACE_RELATIVE = os.path.join("Interface", "MainMenuCustomizer")
CACHE_NAME = "mainmenu_release_cache.json"


def _plugin_data_dir(controller) -> str:
    organizer = getattr(controller, "organizer", None)
    try:
        base = str(organizer.pluginDataPath() or "")
        if base:
            path = os.path.join(base, "xingli_assistant", "mainmenu")
            os.makedirs(path, exist_ok=True)
            return path
    except Exception:
        pass
    try:
        root = str(organizer.basePath() or "")
    except Exception:
        root = ""
    if not root:
        root = os.path.abspath(os.path.join(getattr(controller, "plugin_path", os.path.dirname(__file__)), "..", ".."))
    path = os.path.join(root, "_XingliState", "mainmenu")
    os.makedirs(path, exist_ok=True)
    return path


def _cache_path(controller) -> str:
    return os.path.join(_plugin_data_dir(controller), CACHE_NAME)


def _sanitize_manifest(manifest: Optional[Dict[str, object]]) -> Dict[str, object]:
    if not isinstance(manifest, dict):
        return {}
    return {
        "enabled": bool(manifest.get("enabled", True)),
        "pack_id": str(manifest.get("pack_id", "") or ""),
        "name": str(manifest.get("name", "星黎整合包") or "星黎整合包"),
        "version": str(manifest.get("version", "") or ""),
        "title": str(manifest.get("title", "") or ""),
        "published_at": str(manifest.get("published_at", "") or ""),
        "changelog": str(manifest.get("changelog", "") or ""),
    }


def _load_cache_state(controller) -> Dict[str, object]:
    path = _cache_path(controller)
    try:
        if not os.path.isfile(path):
            return {"schema": 1, "manifest": {}, "legacy_captured": False, "legacy_version_text": ""}
        with open(path, "r", encoding="utf-8-sig") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("cache root is not an object")
        # Backward-compatible with an earlier direct-manifest cache shape.
        if "manifest" not in raw and "version" in raw:
            raw = {"schema": 1, "manifest": _sanitize_manifest(raw), "legacy_captured": False, "legacy_version_text": ""}
        raw.setdefault("schema", 1)
        raw["manifest"] = _sanitize_manifest(raw.get("manifest") if isinstance(raw.get("manifest"), dict) else {})
        raw["legacy_captured"] = bool(raw.get("legacy_captured", False))
        raw["legacy_version_text"] = str(raw.get("legacy_version_text", "") or "")
        return raw
    except Exception:
        logger.exception("读取主菜单公开版本缓存失败")
        return {"schema": 1, "manifest": {}, "legacy_captured": False, "legacy_version_text": ""}


def _save_cache_state(controller, state: Dict[str, object]) -> None:
    path = _cache_path(controller)
    payload = {
        "schema": 1,
        "manifest": _sanitize_manifest(state.get("manifest") if isinstance(state.get("manifest"), dict) else {}),
        "legacy_captured": bool(state.get("legacy_captured", False)),
        "legacy_version_text": str(state.get("legacy_version_text", "") or ""),
    }
    _atomic_write_bytes(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def cache_manifest(controller, manifest: Optional[Dict[str, object]]) -> None:
    data = _sanitize_manifest(manifest)
    if not data:
        return
    try:
        state = _load_cache_state(controller)
        state["manifest"] = data
        _save_cache_state(controller, state)
    except Exception:
        logger.exception("保存主菜单公开版本缓存失败")


def load_cached_manifest(controller) -> Dict[str, object]:
    return _sanitize_manifest(_load_cache_state(controller).get("manifest", {}))


def _resolve_existing(organizer, relative_path: str) -> str:
    normalized = relative_path.replace("/", os.sep).replace("\\", os.sep)
    try:
        resolved = str(organizer.resolvePath(normalized) or "")
        if resolved and os.path.isfile(resolved):
            return os.path.abspath(resolved)
    except Exception:
        pass
    try:
        directory, name = os.path.split(normalized)
        found = organizer.findFiles(directory, name)
        for item in found or []:
            path = os.path.abspath(str(item))
            if os.path.isfile(path):
                return path
    except Exception:
        pass
    return ""


def _read_text_preserve_bom(path: str) -> Tuple[str, bool, str]:
    raw = Path(path).read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if b"\r\n" in raw else "\n"
    return text, bom, newline


def _encode_text(text: str, bom: bool) -> bytes:
    data = text.encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


def _atomic_write_bytes(path: str, data: bytes) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".xingli_", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _update_ini_version(path: str, installed_version: str) -> Tuple[bool, str]:
    text, bom, _newline = _read_text_preserve_bom(path)
    section = re.search(r"(?ims)^\s*\[Version\]\s*$.*?(?=^\s*\[|\Z)", text)
    if not section:
        return False, "未找到 [Version] 区段"
    block = section.group(0)
    replacement, count = re.subn(
        r"(?im)^(\s*sText\s*=\s*).*$",
        lambda match: match.group(1) + "v" + installed_version,
        block,
        count=1,
    )
    if count == 0:
        return False, "[Version] 中未找到 sText"
    if replacement == block:
        return False, "版本文字已同步"
    updated = text[:section.start()] + replacement + text[section.end():]
    _atomic_write_bytes(path, _encode_text(updated, bom))
    return True, "已同步主菜单版本文字"


def _version_source_name(ini_path: str) -> str:
    try:
        text, _bom, _newline = _read_text_preserve_bom(ini_path)
    except Exception:
        return "version.txt"
    section = re.search(r"(?ims)^\s*\[versionlist\]\s*$.*?(?=^\s*\[|\Z)", text)
    if not section:
        return "version.txt"
    match = re.search(r"(?im)^\s*sDataSource\s*=\s*(.+?)\s*$", section.group(0))
    if not match:
        return "version.txt"
    name = os.path.basename(match.group(1).strip().strip('"'))
    return name or "version.txt"


def _resolve_version_text(organizer, ini_path: str) -> str:
    source_name = _version_source_name(ini_path)
    rel = os.path.join(INTERFACE_RELATIVE, source_name)
    resolved = _resolve_existing(organizer, rel)
    if resolved:
        return resolved
    # If the data source has not been created yet, create it inside the same mod
    # that owns the winning MainMenuCustomizer.ini. This avoids writing to game Data.
    try:
        ini = Path(ini_path)
        mod_root = ini.parents[2]
        target = mod_root / "Interface" / "MainMenuCustomizer" / source_name
        target.parent.mkdir(parents=True, exist_ok=True)
        return str(target)
    except Exception:
        return ""


def _version_key(value: str):
    parts = []
    for item in re.findall(r"\d+", str(value or "")):
        try:
            parts.append(int(item))
        except Exception:
            parts.append(0)
    return tuple(parts or [0])


def _build_version_text(installed_version: str, manifest: Dict[str, object], legacy_text: str = "") -> str:
    latest = str(manifest.get("version", "") or "").strip()
    title = str(manifest.get("title", "") or "").strip()
    changelog = str(manifest.get("changelog", "") or "").strip()
    enabled = bool(manifest.get("enabled", True)) if manifest else True
    lines = ["当前安装版本：v{}".format(installed_version or "未记录")]
    if latest and enabled:
        lines.append("最新发布版本：v{}".format(latest))
        if _version_key(latest) > _version_key(installed_version):
            lines.append("状态：发现整合包更新")
            lines.append("请打开桌面【星黎帮助与教程】或 MO2 中【星黎 MO2 小助手】查看更新。")
        else:
            lines.append("状态：当前已是最新版本")
        if title:
            lines.append("")
            lines.append(title)
        if changelog:
            lines.append("")
            lines.append("更新说明：")
            lines.extend(line.rstrip() for line in changelog.splitlines() if line.strip())
    else:
        lines.append("状态：等待联网检查更新")
    legacy = str(legacy_text or "").strip()
    if legacy:
        lines.append("")
        lines.append("历史更新记录：")
        lines.extend(legacy.splitlines())
    return "\r\n".join(lines).rstrip() + "\r\n"


def sync(controller, installed_version: str, manifest: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    """Synchronize local installed version and cached/current release info."""
    result = {"found": False, "ini_updated": False, "text_updated": False, "ini_path": "", "text_path": ""}
    organizer = getattr(controller, "organizer", None)
    if organizer is None or not str(installed_version or "").strip():
        return result
    try:
        if bool(controller._is_mo2_managed_app_running()):
            result["skipped"] = "managed_app_running"
            return result
    except Exception:
        pass
    cache_state = _load_cache_state(controller)
    if isinstance(manifest, dict):
        cache_state["manifest"] = _sanitize_manifest(manifest)
    manifest_data = _sanitize_manifest(cache_state.get("manifest") if isinstance(cache_state.get("manifest"), dict) else {})
    ini_path = _resolve_existing(organizer, INI_RELATIVE)
    if not ini_path:
        return result
    result["found"] = True
    result["ini_path"] = ini_path
    try:
        changed, message = _update_ini_version(ini_path, str(installed_version).strip())
        result["ini_updated"] = bool(changed)
        result["ini_message"] = message
    except Exception as exc:
        result["ini_error"] = str(exc)
        logger.exception("同步 MainMenuCustomizer.ini 版本失败")

    text_path = _resolve_version_text(organizer, ini_path)
    if text_path:
        result["text_path"] = text_path
        try:
            existing = Path(text_path).read_text(encoding="utf-8-sig", errors="ignore") if os.path.isfile(text_path) else ""
            if not bool(cache_state.get("legacy_captured", False)):
                # Preserve the author's pre-existing update history once before this file becomes managed.
                normalized_existing = existing.strip()
                if normalized_existing and not normalized_existing.startswith("当前安装版本："):
                    cache_state["legacy_version_text"] = existing.strip()
                cache_state["legacy_captured"] = True
            if isinstance(manifest, dict):
                cache_state["manifest"] = manifest_data
            _save_cache_state(controller, cache_state)
            content = _build_version_text(
                str(installed_version).strip(),
                manifest_data,
                str(cache_state.get("legacy_version_text", "") or ""),
            )
            if existing.replace("\r\n", "\n") != content.replace("\r\n", "\n"):
                _atomic_write_bytes(text_path, b"\xef\xbb\xbf" + content.encode("utf-8"))
                result["text_updated"] = True
        except Exception as exc:
            result["text_error"] = str(exc)
            logger.exception("同步 MainMenuCustomizer 更新说明失败")
    return result
