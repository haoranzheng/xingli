# coding: utf-8
"""Remote author announcements for Xingli MO2 assistant (read-only)."""
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
import urllib.request

PRODUCT = "xingli_announcements"
CATALOGS = (
    "https://gitee.com/HR-world/xingli/raw/main/announcements/latest.json",
    "https://raw.githubusercontent.com/haoranzheng/xingli/main/announcements/latest.json",
)
MAX_MANIFEST = 96 * 1024
MAX_ANNOUNCEMENTS = 30
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,79}$")
ALLOWED_PAGES = {
    "pan.quark.cn": re.compile(r"^/s/[a-zA-Z0-9_-]{8,100}$"),
    "pan.baidu.com": re.compile(r"^/s/[a-zA-Z0-9_-]{8,100}$"),
}


class AnnouncementError(RuntimeError):
    pass


def _validate_download_page(url):
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise AnnouncementError("公告网盘链接格式无效")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise AnnouncementError("公告网盘地址无效")
    pattern = ALLOWED_PAGES.get((parsed.hostname or "").lower())
    if (
        parsed.scheme != "https" or not pattern
        or not pattern.fullmatch(parsed.path)
        or parsed.username or parsed.password or parsed.fragment
        or port not in (None, 443)
    ):
        raise AnnouncementError("公告只允许可信的夸克/百度 HTTPS 网盘页面")
    return url


def validate_manifest(data):
    if not isinstance(data, dict) or data.get("schema") != 1 or data.get("product") != PRODUCT:
        raise AnnouncementError("公告清单格式无效")
    items = data.get("announcements")
    if not isinstance(items, list) or len(items) > MAX_ANNOUNCEMENTS:
        raise AnnouncementError("公告列表类型或长度无效")
    found = set()
    output = []
    for item in items:
        if not isinstance(item, dict):
            raise AnnouncementError("公告记录必须是对象")
        ident = item.get("id")
        revision = item.get("revision")
        title = item.get("title")
        message = item.get("message")
        if not isinstance(ident, str) or not ID_RE.fullmatch(ident) or ident in found:
            raise AnnouncementError("公告 ID 重复或无效")
        found.add(ident)
        if type(revision) is not int or not 1 <= revision <= 1000000:
            raise AnnouncementError("公告修订号无效")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
            raise AnnouncementError("公告标题无效")
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 1500:
            raise AnnouncementError("公告正文无效")
        if any(ord(ch) < 32 and ch not in "\n\t" for ch in title + message):
            raise AnnouncementError("公告含非法控制字符")
        links = item.get("links")
        if not isinstance(links, list) or not 1 <= len(links) <= 3:
            raise AnnouncementError("公告下载入口数量无效")
        safe_links = []
        for link in links:
            if not isinstance(link, dict):
                raise AnnouncementError("公告下载入口格式无效")
            name = link.get("name")
            code = link.get("code", "")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 30:
                raise AnnouncementError("下载入口名称无效")
            if not isinstance(code, str) or len(code) > 32:
                raise AnnouncementError("提取码格式无效")
            safe_links.append({
                "name": name.strip(),
                "url": _validate_download_page(link.get("url")),
                "code": code.strip(),
            })
        output.append({
            "id": ident, "revision": revision,
            "title": title.strip(), "message": message.strip(),
            "published_at": str(item.get("published_at", "") or "")[:40],
            "category": str(item.get("category", "") or "announcement")[:40],
            "links": safe_links,
        })
    return output


def _read_http(url, timeout):
    request = urllib.request.Request(url, headers={
        "User-Agent": "Xingli-MO2-Announcement/2.4.9",
        "Cache-Control": "no-cache",
        "Accept-Encoding": "identity",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        # Prevent external redirect of the catalog itself.
        parsed = urlsplit(response.geturl())
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in (
            "gitee.com", "raw.githubusercontent.com", "github.com",
        ):
            raise AnnouncementError("公告源发生了不可信的重定向")
        body = response.read(MAX_MANIFEST + 1)
    if len(body) > MAX_MANIFEST:
        raise AnnouncementError("远程公告清单超出大小上限")
    return body


def fetch_announcements(timeout=7):
    """Merge mirror catalogs; a stale Gitee copy must not mask a newer GitHub announcement."""
    newest = {}
    failures = []
    succeeded = False
    for url in CATALOGS:
        try:
            raw = _read_http(url, timeout)
            items = validate_manifest(json.loads(raw.decode("utf-8-sig")))
            succeeded = True
            for item in items:
                previous = newest.get(item["id"])
                # Github is processed second and wins on equal revision.
                if previous is None or item["revision"] >= previous["revision"]:
                    newest[item["id"]] = item
        except Exception as exc:
            failures.append("{}: {}".format(urlsplit(url).hostname, exc))
    if not succeeded:
        raise AnnouncementError("更新公告暂时无法获取：" + "; ".join(failures))
    return sorted(newest.values(), key=lambda item: (item["published_at"], item["id"]), reverse=True)


def receipt_path(organizer):
    """Per-instance read receipts; never stored inside an MO2 Profile."""
    path = Path(str(organizer.pluginDataPath()))
    if not str(path) or str(path) == "." or path.is_symlink():
        raise AnnouncementError("MO2 插件数据目录无效")
    return path / "xingli_assistant" / "announcement_receipts.json"


def read_receipts(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            return {}
        return {
            key: version for key, version in raw.items()
            if isinstance(key, str) and ID_RE.fullmatch(key)
            and type(version) is int and 1 <= version <= 1000000
        }
    except (OSError, ValueError, UnicodeError):
        return {}


def unseen(announcements, receipts):
    return [
        item for item in announcements
        if receipts.get(item["id"], 0) < item["revision"]
    ]


def mark_seen(path, item):
    """Atomic local receipt update; content update never changes MO2/modpack version."""
    path = Path(path)
    if path.is_symlink():
        raise AnnouncementError("拒绝更新链接到其他文件的公告回执")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise AnnouncementError("拒绝通过符号链接写入公告回执")
    receipts = read_receipts(path)
    receipts[item["id"]] = max(receipts.get(item["id"], 0), item["revision"])
    fd, temp_name = tempfile.mkstemp(prefix=".announcements-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(receipts, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, str(path))
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
