# coding: utf-8
"""Safe, versioned MO2 profile downloads. Does not modify the active profile."""
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from urllib.parse import urlsplit
import urllib.request
import zipfile

PRODUCT = "xingli_profile_downloads"
HOSTS = {"gitee.com", "raw.githubusercontent.com"}
MANIFEST_URLS = (
    "https://gitee.com/HR-world/xingli/raw/main/profiles/latest.json",
    "https://raw.githubusercontent.com/haoranzheng/xingli/main/profiles/latest.json",
)
MAX_MANIFEST = 256 * 1024
MAX_PACKAGE = 64 * 1024 * 1024
MAX_UNPACKED = 128 * 1024 * 1024
MAX_FILE = 32 * 1024 * 1024
MAX_FILES = 500
MAX_PROFILES = 80
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    "{}{}".format(prefix, number)
    for prefix in ("COM", "LPT") for number in range(1, 10)
}


class ProfileError(RuntimeError):
    pass


def _version(value):
    value = str(value or "").strip()
    if not VERSION_RE.fullmatch(value):
        raise ProfileError("档案版本号必须使用数字版本，例如 1.0.0")
    parts = [int(part) for part in value.split(".")]
    return tuple(parts + [0] * (4 - len(parts)))


def _validate_url(url):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        raise ProfileError("档案下载地址格式无效")
    if (
        not isinstance(url, str)
        or len(url) > 2048
        or parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in HOSTS
        or parsed.username
        or parsed.password
        or parsed.fragment
        or port not in (None, 443)
    ):
        raise ProfileError("仅允许来自星黎 Gitee/GitHub 镜像的 HTTPS 下载地址")


def _read_http(url, max_bytes, timeout):
    _validate_url(url)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Xingli-MO2-Profile-Downloader/2.4.8",
            "Cache-Control": "no-cache",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        _validate_url(response.geturl())
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ProfileError("档案下载体积超过安全限制")
    return data


def validate_manifest(data):
    if not isinstance(data, dict) or data.get("schema") != 1 or data.get("product") != PRODUCT:
        raise ProfileError("不支持的星黎档案清单")
    profiles = data.get("profiles")
    if not isinstance(profiles, list) or len(profiles) > MAX_PROFILES:
        raise ProfileError("档案清单格式无效或条目过多")
    ids = set()
    result = []
    for item in profiles:
        if not isinstance(item, dict):
            raise ProfileError("档案清单包含无效记录")
        ident = item.get("id")
        name = str(item.get("name", "") or "").strip()
        version = str(item.get("version", "") or "").strip()
        if not isinstance(ident, str) or not ID_RE.fullmatch(ident) or ident in ids:
            raise ProfileError("档案标识重复或格式无效")
        ids.add(ident)
        _version(version)
        if not name or len(name) > 100 or any(ord(ch) < 32 for ch in name):
            raise ProfileError("档案名称为空或包含控制字符")
        package = item.get("package")
        if not isinstance(package, dict):
            raise ProfileError("档案下载包缺失")
        size = package.get("size")
        sha = package.get("sha256")
        mirrors = package.get("mirrors")
        if type(size) is not int or not (0 < size <= MAX_PACKAGE):
            raise ProfileError("档案包大小不合法")
        if not isinstance(sha, str) or not SHA256_RE.fullmatch(sha):
            raise ProfileError("档案包 SHA256 不合法")
        if not isinstance(mirrors, list) or not 1 <= len(mirrors) <= 3:
            raise ProfileError("档案镜像配置无效")
        usable = []
        for mirror in mirrors:
            if not isinstance(mirror, dict) or not isinstance(mirror.get("url"), str):
                raise ProfileError("档案镜像条目无效")
            _validate_url(mirror["url"])
            usable.append({
                "name": str(mirror.get("name", "") or "下载镜像")[:60],
                "url": mirror["url"],
            })
        result.append({
            "id": ident,
            "name": name,
            "version": version,
            "description": str(item.get("description", "") or "")[:1200],
            "changelog": str(item.get("changelog", "") or "")[:1200],
            "package": {"size": size, "sha256": sha.lower(), "mirrors": usable},
        })
    return result


def fetch_profiles(timeout=8):
    failures = []
    for url in MANIFEST_URLS:
        try:
            data = json.loads(_read_http(url, MAX_MANIFEST, timeout).decode("utf-8-sig"))
            return validate_manifest(data)
        except Exception as exc:
            failures.append(str(exc))
    raise ProfileError("档案清单暂时无法获取：" + "; ".join(failures[-2:]))


def download_package(record, timeout=20):
    package = record.get("package", {})
    errors = []
    for mirror in package.get("mirrors", []):
        try:
            blob = _read_http(mirror["url"], min(MAX_PACKAGE, package["size"]), timeout)
            if len(blob) != package["size"]:
                raise ProfileError("档案包文件长度不符")
            if hashlib.sha256(blob).hexdigest() != package["sha256"]:
                raise ProfileError("档案包 SHA256 校验失败")
            validate_profile_archive(blob)
            return blob
        except Exception as exc:
            errors.append(str(exc))
    raise ProfileError("档案下载失败：" + "; ".join(errors[-3:]))


def _safe_member_name(name):
    if not isinstance(name, str) or not name or len(name) > 220 or "\\" in name or name.startswith("/"):
        raise ProfileError("档案 ZIP 包含异常文件路径")
    if re.match(r"^[A-Za-z]:", name):
        raise ProfileError("档案 ZIP 包含盘符路径")
    directory = name.endswith("/")
    normalized = name[:-1] if directory else name
    parts = normalized.split("/")
    if not normalized or any(part in ("", ".", "..") for part in parts):
        raise ProfileError("档案 ZIP 包含目录穿越路径")
    for part in parts:
        if (
            ":" in part
            or part.endswith((".", " "))
            or any(ord(ch) < 32 for ch in part)
            or part.split(".", 1)[0].upper() in WINDOWS_RESERVED
        ):
            raise ProfileError("档案 ZIP 包含 Windows 不安全文件名")
    return parts, directory


def validate_profile_archive(blob):
    try:
        archive = zipfile.ZipFile(BytesIO(blob), "r")
    except (zipfile.BadZipFile, OSError):
        raise ProfileError("档案 ZIP 已损坏")
    files = []
    seen = set()
    total = 0
    with archive:
        for info in archive.infolist():
            parts, directory = _safe_member_name(info.filename)
            if directory:
                continue
            if info.flag_bits & 0x1:
                raise ProfileError("不接受加密档案 ZIP")
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise ProfileError("档案 ZIP 不允许包含符号链接")
            key = "/".join(parts).casefold()
            if key in seen:
                raise ProfileError("档案 ZIP 包含重复文件名")
            if any(key.startswith(existing + "/") or existing.startswith(key + "/") for existing in seen):
                raise ProfileError("档案 ZIP 包含文件与目录路径冲突")
            seen.add(key)
            if len(files) >= MAX_FILES:
                raise ProfileError("档案文件数量超过安全限制")
            if info.file_size < 0 or info.file_size > MAX_FILE:
                raise ProfileError("档案中存在过大的单个文件")
            total += info.file_size
            if total > MAX_UNPACKED:
                raise ProfileError("档案解压后总体积超过安全限制")
            data = archive.read(info)
            if len(data) != info.file_size:
                raise ProfileError("档案文件长度校验失败")
            files.append(("/".join(parts), data))
        if archive.testzip() is not None:
            raise ProfileError("档案 ZIP CRC 校验失败")
    if not any(name.casefold() == "modlist.txt" for name, _data in files):
        raise ProfileError("这不是完整的 MO2 Profile：缺少根目录 modlist.txt")
    return files


def profile_paths(organizer):
    """Use MO2's active profile as the authority; never guess a global path."""
    try:
        raw = Path(str(organizer.profilePath()))
    except Exception as exc:
        raise ProfileError("无法读取当前 MO2 Profile 路径：" + str(exc))
    if not raw.exists() or raw.is_symlink() or not raw.is_dir():
        raise ProfileError("当前 MO2 Profile 目录不存在或不安全")
    profile = raw.resolve()
    if not (profile / "modlist.txt").is_file():
        raise ProfileError("当前 MO2 Profile 缺少 modlist.txt，已停止档案安装")
    root = profile.parent
    if not root.is_dir() or root.resolve() == profile.resolve():
        raise ProfileError("无法确认 MO2 profiles 根目录")
    return root, profile


def safe_profile_name(value, fallback):
    value = str(value or fallback).strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = value.strip(" .")
    if not value:
        value = fallback
    if value.split(".", 1)[0].upper() in WINDOWS_RESERVED:
        value = "_" + value
    return value[:72].rstrip(" .") or fallback


def suggested_target(root, record):
    base = safe_profile_name(record.get("name"), record.get("id", "XingliProfile"))
    existing = set()
    try:
        existing = {item.name.casefold() for item in root.iterdir()}
    except OSError as exc:
        raise ProfileError("无法读取 MO2 profiles 目录：" + str(exc))
    if base.casefold() not in existing:
        return root / base
    for number in range(1, 1000):
        name = "{} (星黎下载 {})".format(base, number)
        if name.casefold() not in existing:
            return root / name
    raise ProfileError("同名档案过多，无法生成安全的新档案目录")


def install_profile_archive(organizer, record, blob):
    """Extract to a sibling staging directory, then rename to a new profile.

    Existing profiles and the active profile are never overwritten.
    """
    files = validate_profile_archive(blob)
    root, active_profile = profile_paths(organizer)
    stage = Path(tempfile.mkdtemp(prefix=".xingli-profile-staging-", dir=str(root)))
    try:
        for relative, data in files:
            parts = relative.split("/")
            target = stage.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        if not (stage / "modlist.txt").is_file():
            raise ProfileError("档案解压后缺少 modlist.txt")
        # Re-check the active MO2 profile after the asynchronous download.
        root_after, active_after = profile_paths(organizer)
        if root_after.resolve() != root.resolve() or active_after.resolve() != active_profile.resolve():
            raise ProfileError("下载期间 MO2 当前 Profile 发生变化，已取消安装")
        for _attempt in range(1000):
            target = suggested_target(root, record)
            try:
                os.rename(str(stage), str(target))
                return {"path": str(target), "name": target.name, "files": len(files)}
            except FileExistsError:
                continue
        raise ProfileError("无法为新档案分配不冲突的目录名")
    except BaseException:
        if stage.exists():
            shutil.rmtree(str(stage), ignore_errors=True)
        raise


def receipt_path(organizer):
    try:
        root = Path(str(organizer.pluginDataPath()))
    except Exception as exc:
        raise ProfileError("无法读取 MO2 插件数据目录：" + str(exc))
    return root / "xingli_assistant" / "profile_download_receipts.json"


def read_receipts(path):
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in data.items()
            if isinstance(key, str) and isinstance(value, str)
        }
    except (OSError, ValueError):
        return {}


def save_receipt(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    receipts = read_receipts(path)
    receipts[record["id"]] = record["version"]
    fd, temp_name = tempfile.mkstemp(prefix="profile-receipts-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(receipts, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, str(path))
    except BaseException:
        try:
            os.remove(temp_name)
        except OSError:
            pass
        raise


def unseen(records, receipts):
    pending = []
    for record in records:
        old = receipts.get(record["id"])
        if old is None:
            pending.append(record)
            continue
        try:
            old_version = _version(old)
        except ProfileError:
            # A damaged or legacy receipt must not suppress a valid release.
            pending.append(record)
            continue
        if _version(record["version"]) > old_version:
            pending.append(record)
    return pending
