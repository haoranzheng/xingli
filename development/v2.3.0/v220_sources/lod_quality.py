# -*- coding: utf-8 -*-
"""
XingLi LOD quality guard for MO2 2.4.4 / Python 3.8.

The module applies a conservative, reversible Skyrim SE/AE terrain/object LOD
configuration before xLODGen and DynDOLOD are launched by the XingLi pipeline.
It edits only known scalar settings and never simulates GUI clicks.
"""

import json
import os
import re
import shutil
import time
from typing import Dict, List, Optional, Tuple


QUALITY_LABELS = {
    "balanced": "星黎中配·着色器兼容（推荐）",
    "performance": "性能优先（较低显存/帧率余量）",
    "off": "不自动修改工具画质",
}

SHADER_LABELS = {
    "auto": "自动检测着色器",
    "community_shaders": "Community Shaders",
    "enb": "ENB",
    "vanilla": "原版/其它",
}

GRASS_PRESET_SUMMARY = {
    "balanced": "Mode 1 / iMinGrassSize 60 / DynDOLOD Grass LOD 50%",
    "performance": "Mode 1 / iMinGrassSize 70 / DynDOLOD Grass LOD 33%",
    "off": "只修复草缓存必需项，不覆盖密度/远景草地参数",
}


# xLODGen uses 225 for BC7 Quick in current xEdit/xLODGen view settings.
BC7_QUICK = "225"


def _read_text(path: str) -> Tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), enc
        except (UnicodeDecodeError, LookupError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read(), "utf-8"


def _atomic_write(path: str, text: str, encoding: str = "utf-8") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".xingli.tmp"
    with open(tmp, "w", encoding=encoding, newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def _patch_section(path: str, section: str, values: Dict[str, str]) -> List[str]:
    """Patch only one INI section while preserving all unrelated text/settings."""
    if os.path.isfile(path):
        text, enc = _read_text(path)
    else:
        text, enc = "", "utf-8"
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    sec_re = re.compile(r"^\s*\[\s*" + re.escape(section) + r"\s*\]\s*$", re.I)
    any_sec_re = re.compile(r"^\s*\[[^]]+\]\s*$")
    start = None
    end = len(lines)
    for i, line in enumerate(lines):
        if sec_re.match(line):
            start = i
            for j in range(i + 1, len(lines)):
                if any_sec_re.match(lines[j]):
                    end = j
                    break
            break
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        start = len(lines)
        lines.append("[{}]".format(section))
        end = len(lines)

    key_lookup = {k.casefold(): (k, str(v)) for k, v in values.items()}
    found = set()
    key_re = re.compile(r"^(\s*)([^;#][^=]*?)(\s*)=(.*)$")
    for i in range(start + 1, end):
        m = key_re.match(lines[i])
        if not m:
            continue
        key = m.group(2).strip()
        spec = key_lookup.get(key.casefold())
        if not spec:
            continue
        canonical, value = spec
        lines[i] = "{}{}={}".format(m.group(1), canonical, value)
        found.add(canonical.casefold())

    inserts = []
    for key, value in values.items():
        if key.casefold() not in found:
            inserts.append("{}={}".format(key, value))
    if inserts:
        lines[end:end] = inserts

    out = newline.join(lines)
    if text.endswith(("\n", "\r")) or out:
        out += newline
    _atomic_write(path, out, enc)
    return ["{}={}".format(k, v) for k, v in values.items()]


def _patch_global_keys(path: str, values: Dict[str, str]) -> List[str]:
    """Patch unique DynDOLOD INI scalar keys without replacing its shipped rules."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    text, enc = _read_text(path)
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    key_lookup = {k.casefold(): (k, str(v)) for k, v in values.items()}
    found = set()
    key_re = re.compile(r"^(\s*)([^;#\[][^{=]*?)(\s*)=(.*)$")
    first_section = len(lines)
    for i, line in enumerate(lines):
        if re.match(r"^\s*\[[^]]+\]\s*$", line):
            first_section = i
            break
    # DynDOLOD's global settings live before the first worldspace/rule section.
    # Do not overwrite similarly named section-local keys.
    for i in range(first_section):
        m = key_re.match(lines[i])
        if not m:
            continue
        key = m.group(2).strip()
        spec = key_lookup.get(key.casefold())
        if not spec:
            continue
        canonical, value = spec
        lines[i] = "{}{}={}".format(m.group(1), canonical, value)
        found.add(canonical.casefold())

    missing = ["{}={}".format(k, v) for k, v in values.items() if k.casefold() not in found]
    if missing:
        # Put global scalar defaults before the first INI section so we do not inject
        # them into a worldspace/rule section.
        block = ["; XingLi balanced LOD guard"] + missing + [""]
        lines[first_section:first_section] = block

    out = newline.join(lines)
    if text.endswith(("\n", "\r")) or out:
        out += newline
    _atomic_write(path, out, enc)
    return ["{}={}".format(k, v) for k, v in values.items()]


def _read_ini_section_values(path: str, section: str) -> Dict[str, str]:
    if not os.path.isfile(path):
        return {}
    text, _enc = _read_text(path)
    current = ""
    result: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip().casefold()
            continue
        if current != section.casefold() or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if key:
            result[key] = value.strip()
    return result


def _split_semicolon_values(value: str) -> List[str]:
    return [x.strip() for x in str(value or "").split(";") if x.strip()]


class LODQualityManager:
    def __init__(self, organizer, store_root: str, log_callback=None):
        self.organizer = organizer
        self.store_root = store_root
        self.log_callback = log_callback
        self.backup_root = os.path.join(store_root, "settings_backups")
        self.manifest_path = os.path.join(store_root, "last_settings_backup.json")
        self._session = time.strftime("%Y%m%d_%H%M%S")
        self._backed_up = {}

    def _log(self, text: str) -> None:
        if self.log_callback:
            try:
                self.log_callback(text)
            except Exception:
                pass

    def _game_short(self) -> str:
        try:
            game = self.organizer.managedGame()
            for attr in ("gameShortName", "gameName"):
                if hasattr(game, attr):
                    value = str(getattr(game, attr)()).casefold()
                    if value:
                        return value
        except Exception:
            pass
        return ""

    def supports_game(self) -> bool:
        token = self._game_short().replace(" ", "")
        return any(x in token for x in ("skyrimse", "skyrimspecialedition", "skyrimae"))

    def _find_grass_control_source(self) -> str:
        """Return the effective/most useful GrassControl.ini path when available."""
        try:
            overwrite = os.path.normpath(str(self.organizer.overwritePath()))
            override = os.path.join(overwrite, "SKSE", "Plugins", "GrassControl.ini")
            if os.path.isfile(override):
                return override
        except Exception:
            pass
        candidates = []
        for folder, pattern in (("SKSE/Plugins", "GrassControl.ini"), ("SKSE/Plugins/", "GrassControl.ini")):
            try:
                for item in self.organizer.findFiles(folder, pattern) or []:
                    path = os.path.normpath(str(item))
                    if os.path.isfile(path) and os.path.basename(path).casefold() == "grasscontrol.ini":
                        candidates.append(path)
            except Exception:
                pass
        return candidates[0] if candidates else ""

    def _grass_control_target_and_source(self) -> Tuple[str, str]:
        """Return Overwrite target and current VFS source without mutating either."""
        source = self._find_grass_control_source()
        try:
            overwrite = os.path.normpath(str(self.organizer.overwritePath()))
        except Exception:
            overwrite = ""
        if not overwrite:
            raise RuntimeError("无法定位 MO2 Overwrite，不能安全写入 GrassControl.ini")
        target = os.path.join(overwrite, "SKSE", "Plugins", "GrassControl.ini")
        return target, source

    @staticmethod
    def _grass_control_values(preset: str) -> Dict[str, str]:
        # Required for precaching regardless of quality preset.
        values = {
            "Use-grass-cache": "true",
            "Only-load-from-cache": "true",
        }
        if preset == "off":
            return values

        # Broad-distribution baseline. Mode 1 renders full grass only to active cells and
        # lets DynDOLOD grass LOD take over after that, which is the performance-oriented
        # official mode. 60/70 are conservative cache-density baselines, not universal
        # visual truths; changing either requires regenerating the grass cache.
        values.update({
            "Super-dense-grass": "false",
            "Extend-grass-distance": "false",
            "Extend-grass-count": "true",
            "Ensure-max-grass-types-setting": "15",
            "Overwrite-grass-distance": "-1.000000",
            "Overwrite-grass-fade-range": "-1.000000",
            "Overwrite-min-grass-size": "70" if preset == "performance" else "60",
            "Global-grass-scale": "1.000000",
            "DynDOLOD-Grass-Mode": "1",
            "Max-Failures": "2",
            "Freeze-check-enabled": "true",
            "Freeze-check-time": "90000",
            "Updating-Cache": "false",
        })
        return values

    def inspect_grass_control(self) -> Tuple[bool, str]:
        try:
            source = self._find_grass_control_source()
            if not source:
                return False, "MO2 VFS 中未找到 SKSE/Plugins/GrassControl.ini"
            values = _read_ini_section_values(source, "GrassConfig")
            required = {
                "Use-grass-cache": values.get("Use-grass-cache", "<缺失>"),
                "Only-load-from-cache": values.get("Only-load-from-cache", "<缺失>"),
                "DynDOLOD-Grass-Mode": values.get("DynDOLOD-Grass-Mode", "<缺失>"),
                "Overwrite-min-grass-size": values.get("Overwrite-min-grass-size", "<缺失>"),
                "Ensure-max-grass-types-setting": values.get("Ensure-max-grass-types-setting", "<缺失>"),
                "Extend-grass-distance": values.get("Extend-grass-distance", "<缺失>"),
                "Extend-grass-count": values.get("Extend-grass-count", "<缺失>"),
            }
            skip = set(_split_semicolon_values(values.get("Skip-pregenerate-world-spaces", "")))
            only = set(_split_semicolon_values(values.get("Only-pregenerate-world-spaces", "")))
            overlap = sorted(skip & only, key=str.casefold)
            lines = ["当前生效 GrassControl.ini：{}".format(source)]
            lines.extend("{} = {}".format(k, v) for k, v in required.items())
            if overlap:
                lines.append("⚠ Skip/Only 世界空间列表存在交集：{}".format("; ".join(overlap)))
            if str(required["Use-grass-cache"]).casefold() != "true":
                lines.append("❌ Use-grass-cache 不是 true，草缓存不会正常进入生成模式。")
            if str(required["Only-load-from-cache"]).casefold() != "true":
                lines.append("⚠ Only-load-from-cache 不是 true，不符合 DynDOLOD Grass LOD 推荐前置。")
            return True, "\n".join(lines)
        except Exception as e:
            return False, "GrassControl.ini 检查失败：{}".format(e)

    def apply_grass_control(self, preset: str) -> Tuple[bool, str]:
        try:
            target, source = self._grass_control_target_and_source()
            target_existed = os.path.isfile(target)
            self._backup(target)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if not target_existed:
                if not source:
                    raise RuntimeError("MO2 VFS 中未找到 SKSE/Plugins/GrassControl.ini，请先运行/安装 NGIO-NG")
                shutil.copy2(source, target)
            changed = _patch_section(target, "GrassConfig", self._grass_control_values(preset))
            values = _read_ini_section_values(target, "GrassConfig")
            skip = set(_split_semicolon_values(values.get("Skip-pregenerate-world-spaces", "")))
            only = set(_split_semicolon_values(values.get("Only-pregenerate-world-spaces", "")))
            overlap = sorted(skip & only, key=str.casefold)
            detail = "已写入 GrassControl.ini 覆盖：{}；{}。".format(target, "，".join(changed))
            if source and os.path.normcase(source) != os.path.normcase(target):
                detail += " 原 VFS 文件：{}。".format(source)
            if overlap:
                detail += " ⚠ 保留了现有 Skip/Only 世界空间设置，但两者交集为：{}。".format("; ".join(overlap))
            if preset != "off":
                detail += " 草地远景基线：{}。".format(GRASS_PRESET_SUMMARY.get(preset, preset))
            return True, detail
        except Exception as e:
            return False, "GrassControl.ini 自动预设失败：{}".format(e)

    def detect_shader(self) -> str:
        # Community Shaders is inside the MO2 VFS, so getFileOrigins is preferable.
        for rel in ("SKSE/Plugins/CommunityShaders.dll", "skse/plugins/CommunityShaders.dll"):
            try:
                origins = self.organizer.getFileOrigins(rel)
                if origins:
                    return "community_shaders"
            except Exception:
                pass
        try:
            game = self.organizer.managedGame()
            game_dir = game.gameDirectory()
            roots = []
            if isinstance(game_dir, str):
                roots.append(game_dir)
            for attr in ("absolutePath", "path"):
                if hasattr(game_dir, attr):
                    candidate = getattr(game_dir, attr)()
                    roots.append(str(candidate))
            for root in roots:
                if os.path.isfile(os.path.join(root, "d3d11.dll")) and (
                    os.path.isfile(os.path.join(root, "enbseries.ini")) or
                    os.path.isdir(os.path.join(root, "enbseries"))
                ):
                    return "enb"
        except Exception:
            pass
        return "vanilla"

    def effective_shader(self, shader_mode: str) -> str:
        if shader_mode in ("community_shaders", "enb", "vanilla"):
            return shader_mode
        return self.detect_shader()

    def _backup(self, path: str) -> None:
        path = os.path.normpath(path)
        if path in self._backed_up:
            return
        session_dir = os.path.join(self.backup_root, self._session)
        os.makedirs(session_dir, exist_ok=True)
        base = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.path.basename(path))
        dst = os.path.join(session_dir, base)
        existed = os.path.isfile(path)
        if existed:
            shutil.copy2(path, dst)
        self._backed_up[path] = {"backup": dst if existed else "", "existed": existed}
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump({"session": self._session, "files": self._backed_up}, f, ensure_ascii=False, indent=2)

    def restore_latest(self) -> Tuple[bool, str]:
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            files = data.get("files", {})
            if not files:
                return False, "没有可恢复的工具设置备份"
            restored = []
            for original, meta in files.items():
                backup = meta.get("backup", "")
                existed = bool(meta.get("existed", False))
                if existed and backup and os.path.isfile(backup):
                    os.makedirs(os.path.dirname(original), exist_ok=True)
                    shutil.copy2(backup, original)
                    restored.append(original)
                elif not existed and os.path.isfile(original):
                    os.remove(original)
                    restored.append(original)
            return True, "已恢复 {} 个设置文件：{}".format(len(restored), "；".join(restored))
        except Exception as e:
            return False, "恢复失败：{}".format(e)

    def _find_xlodgen_viewsettings(self) -> str:
        candidates = []
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            for folder in ("Skyrim Special Edition", "Skyrim Special Edition GOG", "Skyrim Special Edition EPIC"):
                candidates.append(os.path.join(local, folder, "Plugins.sseviewsettings"))
        # Prefer an already existing settings file. If none exists, use the normal SSE path.
        existing = [p for p in candidates if os.path.isfile(p)]
        if existing:
            return max(existing, key=lambda p: os.path.getmtime(p))
        if candidates:
            return candidates[0]
        raise RuntimeError("无法定位 LOCALAPPDATA，不能安全写入 xLODGen 设置")

    @staticmethod
    def _find_dyndolod_ini(exe_path: str) -> str:
        start = os.path.abspath(os.path.dirname(exe_path))
        roots = [start]
        cur = start
        for _ in range(4):
            cur = os.path.dirname(cur)
            if cur and cur not in roots:
                roots.append(cur)
        candidates = []
        for root in roots:
            candidates.extend([
                os.path.join(root, "Edit Scripts", "DynDOLOD", "DynDOLOD_SSE.ini"),
                os.path.join(root, "DynDOLOD", "Edit Scripts", "DynDOLOD", "DynDOLOD_SSE.ini"),
            ])
        for p in candidates:
            if os.path.isfile(p):
                return p
        raise RuntimeError("无法从 DynDOLOD EXE 位置找到 Edit Scripts\\DynDOLOD\\DynDOLOD_SSE.ini")

    @staticmethod
    def _find_texgen_ini(exe_path: str) -> str:
        start = os.path.abspath(os.path.dirname(exe_path))
        roots = [start]
        cur = start
        for _ in range(4):
            cur = os.path.dirname(cur)
            if cur and cur not in roots:
                roots.append(cur)
        candidates = []
        for root in roots:
            candidates.extend([
                os.path.join(root, "Edit Scripts", "DynDOLOD", "TexGen_SSE.ini"),
                os.path.join(root, "DynDOLOD", "Edit Scripts", "DynDOLOD", "TexGen_SSE.ini"),
            ])
        for path in candidates:
            if os.path.isfile(path):
                return path
        raise RuntimeError("无法从 TexGen EXE 位置找到 Edit Scripts\\DynDOLOD\\TexGen_SSE.ini")

    @staticmethod
    def _xlodgen_values(preset: str) -> Dict[str, str]:
        if preset == "performance":
            qualities = (5, 8, 8, 10)
            sizes = (512, 256, 256, 256)
            default_size = 4
        else:
            qualities = (4, 7, 8, 10)
            sizes = (512, 512, 512, 512)
            default_size = 16
        levels = (4, 8, 16, 32)
        values = {
            "TerrainLOD": "1",
            "TerrainBuildMeshes": "1",
            "TerrainBuildDiffuseTextures": "1",
            "TerrainBuildNormalTextures": "1",
            "TerrainProtectCellBorders": "1",
            "TerrainNormalBakeMaps": "1",
            "TerrainDefaultDiffuseSize": str(default_size),
            "TerrainDefaultNormalSize": str(default_size),
            "TerrainVertexColorMultiplier": "1.00",
        }
        for idx, lod in enumerate(levels):
            values["TerrainQualityLOD{}".format(lod)] = str(qualities[idx])
            values["TerrainMaxVertsLOD{}".format(lod)] = "32767"
            values["TerrainDiffuseSizeLOD{}".format(lod)] = str(sizes[idx])
            values["TerrainNormalSizeLOD{}".format(lod)] = str(sizes[idx])
            values["TerrainDiffuseCompLOD{}".format(lod)] = BC7_QUICK
            values["TerrainNormalCompLOD{}".format(lod)] = BC7_QUICK
            values["TerrainGammaLOD{}".format(lod)] = "1.00"
            values["TerrainBrightnessLOD{}".format(lod)] = "0"
            values["TerrainContrastLOD{}".format(lod)] = "0"
            values["TerrainDiffuseMipMapLOD{}".format(lod)] = "1" if lod == 4 else "0"
            values["TerrainNormalMipMapLOD{}".format(lod)] = "0"
            values["TerrainNormalRaiseSteepnessLOD{}".format(lod)] = "0"
        return values

    @staticmethod
    def _dyndolod_values(preset: str, shader: str) -> Dict[str, str]:
        values = {
            # Cap generated atlas/texture work at 8K instead of allowing 16K. This is a
            # better broad-distribution trade-off than 4K, which can create more atlases
            # (and therefore more draw calls) in texture-heavy worldspaces.
            "MaxTextureSize": "8192",
            "ObjectLODMaxAtlasSize": "8192",
            "TreeLODMaxAtlasSize": "8192",
            # Keep the current memory-friendly DynDOLOD format split explicit.
            "ObjectLODDiffuseFormat": "200",
            "ObjectLODAlphaDiffuseFormat": "225",
            "ObjectLODNormalFormat": "200",
            "TreeLODDiffuseFormat": "225",
            # Grass LOD is one of the easiest places to spend frames/triangles. 50 is the
            # official performance-oriented guideline for HD/complex grass.
            "GrassDensity": "33" if preset == "performance" else "50",
            # NGIO-NG currently emits .CGID files; keep the official/default suffix explicit.
            "GrassGID": "cgid",
        }
        if shader == "community_shaders":
            values["ComplexGrassBillboard"] = "5"
            values["ComplexGrassBacklightMask"] = "100"
        return values

    def apply_for_stage(self, stage: str, stage_config, preset: str, shader_mode: str) -> Tuple[bool, str]:
        if not self.supports_game():
            return True, "当前游戏不是 Skyrim SE/AE，跳过星黎 SSE 画质/草地预设"
        shader = self.effective_shader(shader_mode)
        try:
            if stage == "grass":
                # Even when the quality preset is off, grass precaching must have the two
                # mandatory NGIO cache flags enabled or it simply launches Skyrim normally.
                return self.apply_grass_control(preset if preset in ("balanced", "performance") else "off")
            if preset == "off":
                return True, "画质护栏已关闭，保留工具现有设置"
            if stage == "xlodgen":
                path = self._find_xlodgen_viewsettings()
                self._backup(path)
                changed = _patch_section(path, "SSE LOD Options", self._xlodgen_values(preset))
                return True, "已应用 xLODGen {}；着色器={}；{} 项；设置={}。Gamma 保持 1.00，不做通用 0.6 强制修正。".format(
                    QUALITY_LABELS.get(preset, preset), SHADER_LABELS.get(shader, shader), len(changed), path)
            if stage == "texgen":
                if shader != "community_shaders":
                    return True, "TexGen：当前不是 Community Shaders，保留 Grass billboard 类型设置"
                return True, "TexGen：检测到 Community Shaders。按当前 DynDOLOD 官方说明，HD Grass 会自动可用/启用；建议 HD Grass Direct=0 / Ambient=100 / Smoothness=0。插件不强制 ForceComplexGrass，避免覆盖草模作者的 basic/complex grass 设计。"
            if stage == "dyndolod":
                path = self._find_dyndolod_ini(stage_config.exe)
                self._backup(path)
                changed = _patch_global_keys(path, self._dyndolod_values(preset, shader))
                return True, "已应用 DynDOLOD {}；着色器={}；{}；设置={}。GrassControl 已锁定 Mode 1；DynDOLOD 高级界面的 Grass LOD Mode 也必须使用 1。".format(
                    QUALITY_LABELS.get(preset, preset), SHADER_LABELS.get(shader, shader), "，".join(changed), path)
            return True, "当前阶段不需要修改 xLODGen/DynDOLOD 画质设置"
        except Exception as e:
            return False, "自动画质/草地预设应用失败：{}".format(e)

