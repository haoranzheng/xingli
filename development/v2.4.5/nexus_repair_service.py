# coding: utf-8
"""Safe MO2 Nexus metadata correction without retaining native MOD pointers."""
import os

from . import nexus_archive_metadata as legacy


class _ArchiveHintView:
    """Proxy only the installation filename; the original MOD remains authoritative."""
    def __init__(self, mod, archive):
        self._mod = mod
        self._archive = archive

    def installationFile(self):
        return self._archive

    def __getattr__(self, key):
        return getattr(self._mod, key)


def _actual_installation_file(mod):
    try:
        return str(mod.installationFile() or "")
    except Exception:
        return ""


def propose(organizer, mod, archive_hint=""):
    """Use MO2's installed archive; fallback only for an explicit new install path."""
    domain = legacy.nexus_game_domain(organizer)
    if not domain:
        return None
    actual = _actual_installation_file(mod)
    plan = legacy.propose(mod, domain)
    if plan:
        return plan
    # Never bypass a *different, valid* recorded archive identity.
    if legacy.parse_archive_identity(actual):
        return None
    hint = str(archive_hint or "")
    if not hint or not hint.lower().endswith(legacy.ARCHIVE_SUFFIXES):
        return None
    if not legacy.parse_archive_identity(hint):
        return None
    return legacy.propose(_ArchiveHintView(mod, hint), domain)


def find_candidates(organizer):
    """Snapshot immutable dicts. Do not keep IModInterface through a modal dialog."""
    result = []
    if not legacy.nexus_game_domain(organizer):
        return result
    modlist = organizer.modList()
    for name in modlist.allMods():
        try:
            mod = modlist.getMod(str(name))
            if mod is None:
                continue
            candidate = propose(organizer, mod)
            if candidate:
                # No MO2 native objects in a preview, even for an active refresh.
                result.append(dict(candidate))
        except Exception:
            continue
    return result


def apply_by_name(organizer, mod_name, expected, archive_hint=""):
    """Reacquire the live mod, check the snapshot, then update only metadata."""
    try:
        mod = organizer.modList().getMod(str(mod_name))
        if mod is None:
            return False
        current = propose(organizer, mod, archive_hint)
        if current is None or current != expected or current["name"] != str(mod_name):
            return False
        if current["change_id"]:
            mod.setNexusID(int(current["mod_id"]))
        if current["change_url"]:
            mod.setUrl(current["new_url"])
        # MO2 IModInterface setters save metadata. Avoid modDataChanged here:
        # it concerns file-tree state and can synchronously invalidate wrappers.
        return True
    except Exception:
        raise


def apply_new_install(organizer, name, archive_hint=""):
    """Best effort: never let a metadata error fail or undo an installed mod."""
    mod = organizer.modList().getMod(str(name))
    if mod is None:
        return "mod-not-ready"
    candidate = propose(organizer, mod, archive_hint)
    if candidate is None:
        try:
            source = _actual_installation_file(mod)
            if not source:
                return "no-installation-file"
            if not legacy.parse_archive_identity(source):
                return "unrecognised-archive"
            return "existing-id-or-url-protected"
        except Exception:
            return "no-candidate"
    return ("applied:" + str(candidate["mod_id"])
            if apply_by_name(organizer, name, candidate, archive_hint)
            else "changed-during-apply")
