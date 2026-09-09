# coding=utf-8

import ctypes
import hashlib
import os
from ctypes import wintypes
from typing import Tuple


_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


def _win32_directory_identity(path: str) -> str:
    if os.name != "nt":
        raise OSError("Win32 directory identity is only available on Windows")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE

    get_info = kernel32.GetFileInformationByHandle
    get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION)]
    get_info.restype = wintypes.BOOL

    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = create_file(
        os.path.abspath(path),
        0,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        info = _BY_HANDLE_FILE_INFORMATION()
        if not get_info(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close_handle(handle)

    file_id = (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow)
    creation_time = (int(info.ftCreationTime.dwHighDateTime) << 32) | int(info.ftCreationTime.dwLowDateTime)
    return "{}|{}|{}".format(int(info.dwVolumeSerialNumber), file_id, creation_time)


def _fallback_directory_identity(path: str) -> str:
    stat = os.stat(path)
    device = int(getattr(stat, "st_dev", 0) or 0)
    inode = int(getattr(stat, "st_ino", 0) or 0)
    ctime_ns = int(getattr(stat, "st_ctime_ns", int(stat.st_ctime * 1000000000)))
    if inode:
        return "stat|{}|{}|{}".format(device, inode, ctime_ns)
    canonical = os.path.normcase(os.path.realpath(os.path.abspath(path)))
    return "path|{}|{}".format(canonical, ctime_ns)


def resolve_instance_identity(modpack_root: str) -> Tuple[str, str]:
    root = os.path.abspath(modpack_root)
    if not os.path.isdir(root):
        raise OSError("modpack root is not a directory: {}".format(root))

    try:
        raw = _win32_directory_identity(root)
        method = "win32-file-id"
    except Exception:
        raw = _fallback_directory_identity(root)
        method = "fallback-stat"

    digest = hashlib.sha256(("xingli-instance-v1|" + raw).encode("utf-8")).hexdigest()
    return digest, method


def get_instance_id(modpack_root: str) -> str:
    return resolve_instance_identity(modpack_root)[0]
