# coding: utf-8
"""Conservative Nexus metadata recognition for Xingli/MO2 archive installs.

Never infer a Nexus ID from an arbitrary number in a title. Only formats with
an unambiguous version AND upload timestamp are eligible. No network access.
"""
import os
import re
from urllib.parse import urlsplit

ARCHIVE_SUFFIXES = (".7z", ".zip", ".rar")
# "TreeLODGen 195679 1.0.6 2026-10-19T04-53Z VMSnJrggR.7z"
SPACED_NAME = re.compile(
    r"^.+?\s+(?P<id>[1-9]\d{1,8})\s+"
    r"(?P<version>[vV]?\d+(?:[._-]\d+){1,5})\s+"
    r"(?P<date>20\d{2}-(?:0[1-9]|1[0-2])-[0-3]\dT[0-2]\d-[0-5]\dZ)\s+"
    r"(?P<slug>[A-Za-z0-9]{7,20})$"
)
# Legacy Nexus: "My Mod-87521-1-8-3-1712345678.7z"
LEGACY_NAME = re.compile(
    r"^.+-(?P<id>[1-9]\d{1,8})-"
    r"(?P<version>[vV]?\d+(?:-\d+){1,5})-"
    r"(?P<epoch>1[456789]\d{8}|2\d{9})$"
)
DOMAIN_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def parse_archive_identity(filename):
    """Return (Nexus ID, version) only for high-confidence archive names."""
    if not filename:
        return None
    base = os.path.basename(str(filename).replace("\\", "/"))
    stem, ext = os.path.splitext(base)
    if ext and ext.lower() not in ARCHIVE_SUFFIXES:
        return None
    if not ext:
        stem = base
    for pattern in (SPACED_NAME, LEGACY_NAME):
        match = pattern.fullmatch(stem)
        if match:
            return int(match.group("id")), match.group("version").replace("-", ".")
    return None


def nexus_game_domain(organizer):
    """The game domain must come from MO2, never guessed from a mod title."""
    try:
        domain = str(organizer.managedGame().gameNexusName()).strip().lower()
    except Exception:
        return ""
    return domain if DOMAIN_NAME.fullmatch(domain) else ""


def _generated_nexus_url(url, domain, old_id, new_id):
    """Recognize only standard Nexus URLs we may safely replace."""
    if not url:
        return True
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or
                parsed.hostname not in ("www.nexusmods.com", "nexusmods.com") or
                parsed.username or parsed.password or parsed.port):
            return False
        match = re.fullmatch(r"/([a-z0-9-]+)/mods/([1-9]\d*)/?", parsed.path)
        return bool(match and match.group(1) == domain and
                    int(match.group(2)) in (old_id, new_id, 10))
    except (ValueError, TypeError):
        return False


def propose(mod, game_domain):
    """Read-only candidate. Existing good IDs and custom URLs are preserved."""
    if not DOMAIN_NAME.fullmatch(str(game_domain or "")):
        return None
    try:
        archive = str(mod.installationFile() or "")
        # A real installation file, not a renamed mod folder, is required.
        if not archive.lower().endswith(ARCHIVE_SUFFIXES):
            return None
        identity = parse_archive_identity(archive)
        if not identity:
            return None
        identified_id, version = identity
        current_id = int(mod.nexusId())
        old_url = str(mod.url() or "")
        mod_name = str(mod.name())
    except (AttributeError, TypeError, ValueError):
        return None
    # 10 can be a legitimate Nexus ID. Only a *different, fully parsed*
    # archive ID permits it to be corrected.
    if current_id not in (-1, 0, 10, identified_id):
        return None
    new_url = "https://www.nexusmods.com/{}/mods/{}".format(
        game_domain, identified_id)
    change_id = current_id != identified_id
    change_url = (old_url != new_url and
                  _generated_nexus_url(old_url, game_domain, current_id, identified_id))
    if not change_id and not change_url:
        return None
    return {
        "name": mod_name, "archive": archive, "old_id": current_id,
        "mod_id": identified_id, "version": version,
        "old_url": old_url, "new_url": new_url,
        "change_id": change_id, "change_url": change_url,
    }


def apply(organizer, mod, expected):
    """Recheck the candidate immediately before touching MO2 metadata."""
    current = propose(mod, nexus_game_domain(organizer))
    if current is None or current != expected:
        return False
    if current["change_id"]:
        mod.setNexusID(current["mod_id"])
    if current["change_url"]:
        mod.setUrl(current["new_url"])
    notify = getattr(organizer, "modDataChanged", None)
    if callable(notify):
        notify(mod)
    return True


def find_candidates(organizer):
    """Never perform a write during a bulk scan."""
    domain = nexus_game_domain(organizer)
    if not domain:
        return []
    result = []
    modlist = organizer.modList()
    for name in modlist.allMods():
        try:
            mod = modlist.getMod(str(name))
            if mod is None:
                continue
            candidate = propose(mod, domain)
            if candidate:
                result.append((mod, candidate))
        except Exception:
            continue
    return result
