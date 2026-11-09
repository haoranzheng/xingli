# coding: utf-8
"""Build immutable ZIP packages and profiles/latest.json from profiles/releases."""
import hashlib
import io
import json
import re
import sys
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PROFILE_ROOT = ROOT / "profiles"
SOURCE_ROOT = PROFILE_ROOT / "releases"
PACKAGE_ROOT = PROFILE_ROOT / "packages"
MANIFEST_PATH = PROFILE_ROOT / "latest.json"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}$")
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    "{}{}".format(prefix, number)
    for prefix in ("COM", "LPT") for number in range(1, 10)
}
MAX_FILES = 500
MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_PACKAGE = 64 * 1024 * 1024
IGNORED = {".ds_store", "thumbs.db", "desktop.ini", "profile.json"}


class PublishError(RuntimeError):
    pass


def version_tuple(value):
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        raise PublishError("档案版本必须使用明确的数字版本，例如 1.0.0")
    parts = [int(x) for x in value.split(".")]
    return tuple(parts + [0] * (4 - len(parts)))


def _safe_relative(path):
    relative = path.as_posix()
    parts = relative.split("/")
    if (
        not relative
        or relative.startswith("/")
        or "\\" in relative
        or ":" in relative
        or any(part in ("", ".", "..") for part in parts)
        or any(part.endswith((".", " ")) for part in parts)
        or any(part.split(".", 1)[0].upper() in WINDOWS_RESERVED for part in parts)
        or any(ord(ch) < 32 for ch in relative)
        or len(relative) > 220
    ):
        raise PublishError("Profile 中包含不安全路径：" + relative)
    return relative


def _load_metadata(folder):
    path = folder / "profile.json"
    if not path.is_file() or path.is_symlink():
        raise PublishError("缺少档案元数据：" + str(path))
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise PublishError("档案元数据读取失败 {}：{}".format(path, exc))
    if not isinstance(data, dict):
        raise PublishError("profile.json 必须是 JSON 对象")
    ident = data.get("id")
    name = str(data.get("name", "") or "").strip()
    version = str(data.get("version", "") or "").strip()
    if not isinstance(ident, str) or not ID_RE.fullmatch(ident):
        raise PublishError("profile.json 的 id 格式无效")
    if ident != folder.name:
        raise PublishError("profile.json 的 id 必须与目录名一致：" + folder.name)
    version_tuple(version)
    if not name or len(name) > 100 or any(ord(ch) < 32 for ch in name):
        raise PublishError("profile.json 缺少有效的 name")
    return {
        "id": ident,
        "name": name,
        "version": version,
        "description": str(data.get("description", "") or "")[:1200],
        "changelog": str(data.get("changelog", "") or "")[:1200],
    }


def _collect_files(folder):
    entries = []
    total = 0
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise PublishError("Profile 不允许包含符号链接：" + str(path))
        if path.is_dir():
            continue
        if not path.is_file():
            raise PublishError("Profile 中包含非普通文件：" + str(path))
        relative = path.relative_to(folder)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path.name.lower() in IGNORED or path.name.lower().endswith((".log", ".tmp", ".bak")):
            continue
        name = _safe_relative(relative)
        size = path.stat().st_size
        if size < 0 or size > MAX_FILE:
            raise PublishError("Profile 单个文件超过 32 MiB：" + name)
        total += size
        if total > MAX_TOTAL:
            raise PublishError("Profile 总文件体积超过 128 MiB")
        entries.append((name, path.read_bytes()))
        if len(entries) > MAX_FILES:
            raise PublishError("Profile 文件数量超过 500")
    if not any(name.casefold() == "modlist.txt" for name, _data in entries):
        raise PublishError("Profile 根目录必须包含 modlist.txt")
    folded = [name.casefold() for name, _data in entries]
    if len(folded) != len(set(folded)):
        raise PublishError("Profile 包含 Windows 下大小写冲突的文件名")
    for index, name in enumerate(folded):
        if any(other.startswith(name + "/") for other in folded[index + 1:]):
            raise PublishError("Profile 包含文件与目录路径冲突：" + name)
    return entries


def _make_zip(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100644 & 0xFFFF) << 16
            archive.writestr(info, data)
    blob = output.getvalue()
    if not blob or len(blob) > MAX_PACKAGE:
        raise PublishError("压缩后的档案包为空或超过 64 MiB")
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        if archive.testzip() is not None:
            raise PublishError("生成的档案 ZIP CRC 校验失败")
    return blob


def build(root=ROOT):
    root = Path(root)
    profile_root = root / "profiles"
    source_root = profile_root / "releases"
    package_root = profile_root / "packages"
    manifest_path = profile_root / "latest.json"
    source_root.mkdir(parents=True, exist_ok=True)
    package_root.mkdir(parents=True, exist_ok=True)

    records = []
    for folder in sorted(source_root.iterdir(), key=lambda item: item.name.casefold()):
        if not folder.is_dir():
            continue
        if folder.is_symlink():
            raise PublishError("档案源目录不能是符号链接：" + folder.name)
        metadata = _load_metadata(folder)
        entries = _collect_files(folder)
        blob = _make_zip(entries)
        filename = "{}-v{}.zip".format(metadata["id"], metadata["version"])
        package_path = package_root / filename
        if package_path.exists():
            if package_path.is_symlink() or package_path.read_bytes() != blob:
                raise PublishError(
                    "同一档案版本不可覆盖：{}。请提高 profile.json 的 version。".format(filename)
                )
        else:
            temp = package_path.with_suffix(".zip.tmp")
            temp.write_bytes(blob)
            temp.replace(package_path)

        records.append({
            **metadata,
            "package": {
                "filename": filename,
                "size": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
                "mirrors": [
                    {
                        "name": "Gitee 国内主源",
                        "url": "https://gitee.com/HR-world/xingli/raw/main/profiles/packages/" + filename,
                    },
                    {
                        "name": "GitHub 备用源",
                        "url": "https://raw.githubusercontent.com/haoranzheng/xingli/main/profiles/packages/" + filename,
                    },
                ],
            },
        })

    manifest = {
        "schema": 1,
        "product": "xingli_profile_downloads",
        "channel": "stable",
        "profiles": records,
    }
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if not manifest_path.exists() or manifest_path.read_text(encoding="utf-8-sig") != rendered:
        manifest_path.write_text(rendered, encoding="utf-8")
    return manifest


if __name__ == "__main__":
    try:
        result = build()
        print("PROFILE_CATALOG_BUILD_PASS", len(result["profiles"]))
        for record in result["profiles"]:
            print(record["id"], record["version"], record["package"]["filename"])
    except Exception as exc:
        print("PROFILE_CATALOG_BUILD_FAIL:", exc, file=sys.stderr)
        raise
