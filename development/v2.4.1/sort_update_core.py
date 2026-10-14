# coding: utf-8
"""MO2-native sorting BACKUP import, never live sorting mutation.

MO2 MainWindow::queryRestore() recognises <base>.anything, using the native
yyyy_MM_dd_hh_mm_ss parser for exact date suffixes and generic fallback
for other suffixes. We deliberately suffix '_Xingli_NNN' to distinguish
downloaded order sets and keep all existing local backups untouched.

Left MOD ordering: modlist.txt.<identical suffix>
Right plugin ordering: plugins.txt/loadorder.txt/lockedorder.txt.<same suffix>
MO2's Restore action (not this importer) owns the live file replacement.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
import urllib.request
import zipfile

PRODUCT = "xingli_sort_updates"
PACK_ID = "xingli-skyrim"
HOSTS = {"gitee.com", "raw.githubusercontent.com"}
MAX_MANIFEST = 256 * 1024
MAX_DOWNLOAD = 2 * 1024 * 1024
MAX_TEXT = 1024 * 1024
MAX_LIST = 60
KINDS = {"mods": ("modlist.txt",),
         "plugins": ("plugins.txt", "loadorder.txt", "lockedorder.txt")}
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){1,4}$")
SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class SortError(RuntimeError):
    pass


def _version(value):
    if not VERSION.fullmatch(str(value or "")):
        raise SortError("排序版本号格式错误")
    numbers = [int(x) for x in str(value).split(".")]
    return tuple(numbers + [0] * (5-len(numbers)))


def _validate_url(url):
    p = urlsplit(url)
    if (not isinstance(url, str) or len(url)>2048 or p.scheme != "https" or
            p.hostname not in HOSTS or p.username or p.password or p.fragment):
        raise SortError("仅允许来自现有 Gitee/GitHub 镜像的 HTTPS 链接")


def _read_http(url, max_bytes, timeout):
    _validate_url(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Xingli-MO2-Sort-Backup/2.4.1",
        "Cache-Control": "no-cache", "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        _validate_url(response.geturl())
        raw = response.read(max_bytes + 1)
    if len(raw)>max_bytes:
        raise SortError("响应体大小超出排序文件限制")
    return raw


def validate_manifest(data):
    if not isinstance(data, dict) or data.get("schema") != 1 or data.get("product") != PRODUCT:
        raise SortError("不支持的排序更新清单")
    if data.get("pack_id") != PACK_ID:
        raise SortError("整合包标识不匹配")
    releases = data.get("releases")
    if not isinstance(releases, list) or len(releases)>MAX_LIST:
        raise SortError("无效的排序发布列表")
    ids = set()
    result = []
    for record in releases:
        if not isinstance(record,dict):
            raise SortError("排序清单记录格式无效")
        ident = record.get("id")
        version = record.get("version")
        kind = record.get("kind")
        if not isinstance(ident,str) or not SLUG.fullmatch(ident) or ident in ids:
            raise SortError("重复或不合法的排序标识")
        ids.add(ident)
        _version(version)
        if kind not in KINDS:
            raise SortError("排序类型必须是 mods 或 plugins")
        package = record.get("package")
        if not isinstance(package,dict):
            raise SortError("排序更新包缺失")
        size = package.get("size")
        sha = package.get("sha256")
        links = package.get("mirrors")
        if type(size) is not int or not (0<size<=MAX_DOWNLOAD):
            raise SortError("排序包大小不合法")
        if not isinstance(sha,str) or not SHA256.fullmatch(sha):
            raise SortError("排序包 SHA256 不合法")
        if not isinstance(links,list) or not 1<=len(links)<=3:
            raise SortError("排序镜像配置无效")
        mirrors = []
        for link in links:
            if not isinstance(link,dict) or not isinstance(link.get("url"),str):
                raise SortError("排序镜像条目无效")
            _validate_url(link["url"])
            mirrors.append({"url":link["url"],"name":str(link.get("name",""))[:60]})
        result.append({"id":ident, "version":version, "kind":kind,
                       "title":str(record.get("title","排序更新"))[:100],
                       "changelog":str(record.get("changelog",""))[:1500],
                       "package":{"size":size,"sha256":sha.lower(),"mirrors":mirrors}})
    return result


def fetch_releases(timeout=8):
    urls = (
        "https://gitee.com/HR-world/xingli/raw/main/sort/latest.json",
        "https://raw.githubusercontent.com/haoranzheng/xingli/main/sort/latest.json")
    failures = []
    for url in urls:
        try:
            data = json.loads(_read_http(url,MAX_MANIFEST,timeout).decode("utf-8-sig"))
            return validate_manifest(data)
        except Exception as exc:
            failures.append(str(exc))
    raise SortError("排序更新源均不可用：" + "; ".join(failures))


def _check_text(name, content):
    if len(content) == 0 or len(content)>MAX_TEXT or b"\0" in content:
        raise SortError(name + " 非有效排序文本")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeError:
        raise SortError(name + " 必须使用 UTF-8")
    if not text.strip():
        raise SortError(name + " 内容为空")
    # Do not reorder modlist: MO2's priority order is REVERSED on disk.
    if name=="modlist.txt":
        entries = [line.strip() for line in text.splitlines()
                   if line.strip() and not line.lstrip().startswith("#")]
        if not entries or len(entries)>200000:
            raise SortError("MOD 排序列表没有任何项目")
        seen = set()
        for line in entries:
            value = line[1:] if line[0] in "+-*" else line
            if not value.strip() or value in seen:
                raise SortError("MOD 排序包含无效或重复的 MOD")
            seen.add(value)
    return content


def decode_order_archive(data, kind):
    if kind not in KINDS:
        raise SortError("无效排序类型")
    from io import BytesIO
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            info = [x for x in z.infolist() if not x.is_dir()]
            expected = set(KINDS[kind])
            names = [x.filename for x in info]
            if len(names)!=len(expected) or set(names)!=expected:
                raise SortError("ZIP 文件组成不符合 MO2 原生排序恢复要求")
            if sum(x.file_size for x in info)>MAX_DOWNLOAD:
                raise SortError("压缩内容过大")
            contents = {}
            for obj in info:
                if obj.file_size>MAX_TEXT or obj.filename not in expected:
                    raise SortError("ZIP 中包含多余或过大的文件")
                contents[obj.filename]=_check_text(obj.filename,z.read(obj))
            if z.testzip() is not None:
                raise SortError("ZIP CRC 校验失败")
            return contents
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise SortError("损坏的排序 ZIP：" + str(exc))


def download_order(record, timeout=12):
    expected = record["package"]
    errors=[]
    for mirror in expected["mirrors"]:
        try:
            blob=_read_http(mirror["url"],MAX_DOWNLOAD,timeout)
            if len(blob)!=expected["size"] or hashlib.sha256(blob).hexdigest()!=expected["sha256"]:
                raise SortError("排序包长度或 SHA256 校验失败")
            return decode_order_archive(blob,record["kind"])
        except Exception as exc:
            errors.append(str(exc))
    raise SortError("排序包所有镜像下载失败："+"; ".join(errors))


def profile_folder(organizer):
    """Fail closed rather than writing to a guessed profile folder."""
    profile = Path(str(organizer.profilePath()))
    if not profile.is_dir() or profile.is_symlink() or not (profile/"modlist.txt").is_file():
        raise SortError("无法识别当前 MO2 Profile，已取消备份导入")
    try:
        name = str(organizer.profileName())
        if name and name.lower() != profile.name.lower():
            raise SortError("MO2 Profile 路径与名称不一致，拒绝导入")
    except AttributeError:
        pass
    return profile


def install_as_backups(profile, files, kind, clock=None):
    """Exclusive CREATE: never overwrite current files nor other backups.

    Example modlist.txt.2026_10_14_13_22_00_Xingli_001. MO2's queryRestore
    generic suffix path accepts this file and displays its suffix literally.
    The sequence applies to all files of a plugins backup as one unit.
    """
    if kind not in KINDS or set(files)!=set(KINDS[kind]):
        raise SortError("备份组成不合法")
    profile = Path(profile)
    if not profile.is_dir() or profile.is_symlink() or not (profile/"modlist.txt").is_file():
        raise SortError("不是有效的 MO2 Profile 目录")
    for name,data in files.items():
        _check_text(name,data)
        if name not in KINDS[kind]:
            raise SortError("不允许写入其他文件")
    date = (clock or datetime.now()).strftime("%Y_%m_%d_%H_%M_%S")
    names=list(KINDS[kind])
    for sequence in range(1,1000):
        suffix="%s_Xingli_%03d" % (date,sequence)
        destinations=[profile/(name+"."+suffix) for name in names]
        if any(path.exists() or path.is_symlink() for path in destinations):
            continue
        opened=[]
        try:
            for target in destinations:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os,"O_BINARY",0)
                fd=os.open(str(target),flags,0o600)
                opened.append((target,fd))
            for target,fd in opened:
                with os.fdopen(fd,"wb") as f:
                    f.write(files[_original_name(target,suffix)])
                    f.flush()
                    os.fsync(f.fileno())
            return {"suffix":suffix,"paths":[str(p) for p in destinations]}
        except FileExistsError:
            # Concurrent backup creation: do not clobber other process files.
            for p,fd in opened:
                try:os.close(fd)
                except OSError:pass
                try:p.unlink()
                except OSError:pass
            continue
        except BaseException:
            for p,fd in opened:
                try:os.close(fd)
                except OSError:pass
                try:p.unlink()
                except OSError:pass
            raise
    raise SortError("当前时间段 999 个排序备份名均已存在")


def _original_name(path,suffix):
    for name in ("modlist.txt","plugins.txt","loadorder.txt","lockedorder.txt"):
        if path.name==name+"."+suffix:
            return name
    raise SortError("无法识别排序备份文件名")


def receipt_path(organizer):
    """Stored in plugin data, outside the profile and installed plugin."""
    root = Path(str(organizer.pluginDataPath()))/"xingli_assistant"/"sort_receipts"
    profile = profile_folder(organizer)
    digest=hashlib.sha256(str(profile.resolve()).lower().encode("utf-8")).hexdigest()[:20]
    return root/(digest+".json")


def read_receipts(path):
    path=Path(path)
    if not path.is_file():
        return {}
    try:
        data=json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data,dict) else {}
    except (OSError,ValueError):
        return {}


def save_receipt(path, release):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    data=read_receipts(path)
    data[release["id"]]=release["version"]
    temp=path.with_suffix(".json.tmp")
    with temp.open("w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(temp),str(path))


def unseen(releases,receipts):
    return [r for r in releases
            if r["id"] not in receipts or _version(r["version"])>_version(receipts[r["id"]])]
