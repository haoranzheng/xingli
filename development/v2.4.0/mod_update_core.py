# coding: utf-8
"""Xingli per-MOD update catalog and verified resumable downloader.

Python 3.8 compatible; no cloud credential is ever placed in the plugin.
Cloudflare R2 is a public HTTPS download endpoint, not an authenticated client.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit
import urllib.request
import zipfile

PRODUCT = "xingli_mod_updates"
PACK_ID = "xingli-skyrim"
MAX_MANIFEST = 1024 * 1024
MAX_ARCHIVE = 8 * 1024 ** 3
CHUNK = 1024 * 1024
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){1,4}$")
SHA = re.compile(r"^[0-9a-fA-F]{64}$")
TRUSTED_MANIFEST_HOSTS = {"gitee.com", "raw.githubusercontent.com"}
DEFAULT_DOWNLOAD_HOSTS = {"gitee.com", "raw.githubusercontent.com"}


class UpdateError(RuntimeError):
    pass


def _version(value):
    if not VERSION.fullmatch(str(value or "")):
        raise UpdateError("invalid version: %r" % value)
    numbers = [int(x) for x in str(value).split(".")]
    return tuple(numbers + [0] * (5 - len(numbers)))


def is_newer(remote, installed):
    return _version(remote) > _version(installed)


def _host(url, allowed_hosts):
    if not isinstance(url, str) or len(url) > 2048:
        raise UpdateError("invalid URL")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise UpdateError("only public HTTPS download URLs are accepted")
    hostname = (parsed.hostname or "").lower()
    if hostname not in allowed_hosts:
        raise UpdateError("download host not allowed: " + hostname)
    return hostname


def validate_catalog(payload, allowed_hosts):
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise UpdateError("unsupported MOD catalog schema")
    if payload.get("product") != PRODUCT or payload.get("pack_id") != PACK_ID:
        raise UpdateError("catalog product/pack mismatch")
    items = payload.get("mods")
    if not isinstance(items, list) or len(items) > 500:
        raise UpdateError("invalid MOD catalog list")
    result = []
    seen = set()
    for raw in items:
        if not isinstance(raw, dict):
            raise UpdateError("invalid MOD record")
        modid = str(raw.get("id", ""))
        name = str(raw.get("mo2_name", ""))
        version = str(raw.get("version", ""))
        package = raw.get("package")
        if not SLUG.fullmatch(modid) or modid in seen:
            raise UpdateError("invalid or duplicate MOD id: " + modid)
        seen.add(modid)
        if not name or len(name) > 180 or any(c in name for c in ("/", "\\", "\0")) or name in (".", ".."):
            raise UpdateError("invalid MO2 MOD name")
        _version(version)
        if not isinstance(package, dict):
            raise UpdateError("missing MOD package")
        size = package.get("size")
        if type(size) is not int or size <= 0 or size > MAX_ARCHIVE:
            raise UpdateError("invalid MOD archive size")
        sha = str(package.get("sha256", ""))
        if not SHA.fullmatch(sha):
            raise UpdateError("missing MOD archive SHA256")
        mirrors = package.get("mirrors")
        if not isinstance(mirrors, list) or not 1 <= len(mirrors) <= 5:
            raise UpdateError("missing MOD mirrors")
        validated = []
        for mirror in mirrors:
            if not isinstance(mirror, dict):
                raise UpdateError("invalid MOD mirror")
            link = str(mirror.get("url", ""))
            _host(link, allowed_hosts)
            validated.append({"url": link, "name": str(mirror.get("name", ""))[:70]})
        result.append({
            "id": modid,
            "mo2_name": name,
            "version": version,
            "changelog": str(raw.get("changelog", ""))[:2000],
            "package": {"size": size, "sha256": sha.lower(), "mirrors": validated},
        })
    return result


def load_sources(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    raw_urls = data.get("manifest_urls")
    if not isinstance(raw_urls, list) or not raw_urls:
        raise UpdateError("no MOD catalog sources")
    urls = []
    for url in raw_urls:
        _host(url, TRUSTED_MANIFEST_HOSTS)
        urls.append(url)
    hosts = set(DEFAULT_DOWNLOAD_HOSTS)
    extra = data.get("r2_custom_domain", "")
    if extra:
        # Only the installed local config can authorize an R2 custom domain.
        if not isinstance(extra, str) or not re.fullmatch(r"[a-z0-9.-]{4,253}", extra):
            raise UpdateError("invalid R2 custom domain")
        if extra.endswith(".r2.dev"):
            raise UpdateError("r2.dev is for development, not player distribution")
        hosts.add(extra.lower())
    timeout = int(data.get("timeout_seconds", 8))
    return {"urls": urls, "hosts": hosts, "timeout": max(3, min(timeout, 30))}


def fetch_catalog(sources):
    errors = []
    for url in sources["urls"]:
        try:
            _host(url, TRUSTED_MANIFEST_HOSTS)
            req = urllib.request.Request(url, headers={
                "User-Agent": "Xingli-Mod-Updates/2.4",
                "Accept": "application/json",
                "Cache-Control": "no-cache",
            })
            with urllib.request.urlopen(req, timeout=sources["timeout"]) as response:
                _host(response.geturl(), TRUSTED_MANIFEST_HOSTS)
                raw = response.read(MAX_MANIFEST + 1)
                if len(raw) > MAX_MANIFEST:
                    raise UpdateError("MOD catalog too large")
            return validate_catalog(json.loads(raw.decode("utf-8-sig")), sources["hosts"]), url
        except Exception as exc:
            errors.append("%s: %s" % (url, exc))
    raise UpdateError("all MOD catalog mirrors unavailable: " + "; ".join(errors))


def installed_version(organizer, mod_name):
    """Unknown local versions are deliberately NOT silently upgraded."""
    try:
        mod = organizer.modList().getMod(mod_name)
        if mod is None or mod.isSeparator() or mod.isOverwrite() or mod.isForeign():
            return None
        value = mod.version()
        for attr in ("canonicalString", "displayString"):
            fn = getattr(value, attr, None)
            if callable(fn):
                version = str(fn()).strip()
                if VERSION.fullmatch(version):
                    return version
        version = str(value).strip()
        if VERSION.fullmatch(version):
            return version
    except Exception:
        pass
    return None


def eligible_updates(organizer, records):
    found = []
    for record in records:
        current = installed_version(organizer, record["mo2_name"])
        if current is None:
            continue
        if is_newer(record["version"], current):
            item = dict(record)
            item["installed_version"] = current
            found.append(item)
    return found


def verify_sha256(path, expected_size, expected_sha):
    p = Path(path)
    if not p.is_file() or p.stat().st_size != expected_size:
        return False
    h = hashlib.sha256()
    with p.open("rb") as file:
        for chunk in iter(lambda: file.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest() == expected_sha.lower()


def _save_metadata(path, metadata):
    path = Path(path)
    temp = path.with_name(path.name + ".new")
    temp.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    os.replace(str(temp), str(path))


def _resume_one(url, part, meta, expected_size, expected_sha, allowed_hosts,
                timeout, progress, cancelled):
    _host(url, allowed_hosts)
    current = part.stat().st_size if part.exists() else 0
    if current > expected_size:
        part.unlink()
        current = 0
    if current and (not meta.exists() or
                    json.loads(meta.read_text(encoding="utf-8")).get("url") != url or
                    json.loads(meta.read_text(encoding="utf-8")).get("sha256") != expected_sha):
        part.unlink()
        current = 0
    headers = {"User-Agent": "Xingli-Mod-Updates/2.4", "Accept-Encoding": "identity"}
    if current:
        headers["Range"] = "bytes=%d-" % current
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        _host(response.geturl(), allowed_hosts)
        status = int(response.getcode())
        if current and status == 206:
            content_range = str(response.headers.get("Content-Range", ""))
            if not content_range.startswith("bytes %d-" % current):
                raise UpdateError("server returned mismatched Range")
        elif current and status == 200:
            # Server ignored Range: restart instead of appending duplicate bytes.
            current = 0
        elif current:
            raise UpdateError("resume not supported by mirror")
        if status not in (200, 206):
            raise UpdateError("invalid download HTTP status")
        _save_metadata(meta, {"url": url, "sha256": expected_sha})
        with part.open("ab" if current else "wb") as output:
            written = current
            while True:
                if cancelled and cancelled():
                    raise UpdateError("download cancelled")
                data = response.read(CHUNK)
                if not data:
                    break
                written += len(data)
                if written > expected_size:
                    raise UpdateError("archive exceeds manifest size")
                output.write(data)
                if progress:
                    progress(int(written * 100 / expected_size))
    if part.stat().st_size != expected_size:
        raise UpdateError("incomplete MOD package download")


def download_verified(record, cache_dir, allowed_hosts, timeout=20, progress=None,
                      cancelled=None):
    """Return a verified .zip path; partial transfers stay for later resumption.

    Fallback mirrors never share unverified partial bytes. A complete file is
    never trusted without comparing size and SHA256.
    """
    package = record["package"]
    target_dir = Path(cache_dir) / record["id"]
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / ("%s.zip" % record["version"])
    part = target_dir / ("%s.zip.part" % record["version"])
    meta = target_dir / ("%s.zip.part.json" % record["version"])
    if verify_sha256(final, package["size"], package["sha256"]):
        return str(final)
    if final.exists():
        final.unlink()
    failures = []
    for mirror in package["mirrors"]:
        url = mirror["url"]
        try:
            _resume_one(url, part, meta, package["size"], package["sha256"],
                        allowed_hosts, timeout, progress, cancelled)
            if not verify_sha256(part, package["size"], package["sha256"]):
                raise UpdateError("download SHA256 mismatch")
            # Confirm ZIP structure without decompressing multi-GB archives.
            with zipfile.ZipFile(str(part)) as archive:
                if not archive.namelist():
                    raise UpdateError("empty ZIP")
                for member in archive.infolist():
                    name = member.filename.replace("\\", "/")
                    if (name.startswith("/") or
                            ".." in name.split("/") or
                            name.startswith("__MACOSX/")):
                        raise UpdateError("unsafe ZIP member path")
            os.replace(str(part), str(final))
            if meta.exists():
                meta.unlink()
            return str(final)
        except Exception as exc:
            failures.append(str(exc))
            if cancelled and cancelled():
                break
            # Preserve partial data on the LAST mirror for later retry.
            if url != package["mirrors"][-1]["url"]:
                for p in (part, meta):
                    if p.exists():
                        p.unlink()
    raise UpdateError("MOD download failed: " + "; ".join(failures))


def backup_mod(organizer, mod_name, backup_root, mod_id):
    """Copy the complete original MOD before MO2's native installer is called.

    Backups live outside the MODs folder and are never automatically deleted.
    """
    mod_list = organizer.modList()
    mod = mod_list.getMod(mod_name)
    if mod is None or mod.isSeparator() or mod.isOverwrite() or mod.isForeign():
        raise UpdateError("MOD is not an ordinary managed MO2 mod")
    source = Path(str(mod.absolutePath())).resolve()
    mods_root = Path(str(organizer.modsPath())).resolve()
    if source.parent != mods_root or source.is_symlink():
        raise UpdateError("MOD path is not a direct child of MO2 mods root")
    for base, directories, filenames in os.walk(str(source), followlinks=False):
        for name in directories + filenames:
            if (Path(base) / name).is_symlink():
                raise UpdateError("MOD contains symlinks; refusing unsafe backup")
    backup_root = Path(backup_root).resolve()
    if backup_root == mods_root or mods_root in backup_root.parents:
        raise UpdateError("backup directory must be outside MO2 mods")
    size = sum((Path(base) / name).stat().st_size
               for base, _, filenames in os.walk(str(source))
               for name in filenames)
    backup_root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(str(backup_root)).free < size + 256 * 1024 * 1024:
        raise UpdateError("not enough free space for full MOD backup")
    from datetime import datetime
    identifier = datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")
    location = backup_root / mod_id / identifier
    location.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(source), str(location))
    record = {
        "mod_id": mod_id, "mo2_name": mod_name,
        "original_path": str(source), "backup_path": str(location),
        "created_utc": identifier,
        "note": "To restore this MOD, close MO2 first, then use an offline recovery procedure.",
    }
    _save_metadata(location.parent / (identifier + ".json"), record)
    return record
