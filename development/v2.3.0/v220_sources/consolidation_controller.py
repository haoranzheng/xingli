# coding=utf-8

import os
import re
import webbrowser
import logging 
import mobase
import configparser
import json
import shutil
import time
import tempfile
import threading
import winreg
import configparser
from typing import Optional, List # 添加 List
from datetime import datetime # 添加 datetime
import subprocess # 添加 subprocess
import shutil # 添加 shutil
from . import page_file_checker
from . import page_file_manager
from . import utils
from . import user_assistant
from . import auto_updater
from . import modpack_health
from . import modpack_updater
from . import mainmenu_sync
from . import instance_identity
from . import onboarding_state
from . import pgpatcher_adapter
from . import mod_compressor
from . import lod_pipeline
from . import mod_manager
from .mod_state_service import ModStateService
from . import stability

logger = stability.get_logger("controller")

try:
    import PyQt6.QtWidgets as QtWidgets
    import PyQt6.QtGui as QtGui
    from PyQt6.QtCore import QCoreApplication, Qt, QThread, pyqtSignal, QTimer, QStandardPaths
except ImportError:
    import PyQt5.QtWidgets as QtWidgets
    import PyQt5.QtGui as QtGui
    from PyQt5.QtCore import QCoreApplication, Qt, QThread, pyqtSignal, QTimer, QStandardPaths # 确保 PyQt5 也有 QStandardPaths

from .tutorial_data import TUTORIAL_CATEGORIES

class ConsolidationController(mobase.IPluginTool):
    NAME = "星黎整合用户助手"  # 用户版名称
    CONFIG_FILE_NAME = "version.ini" # ini 文件名
    SETTINGS_FILE_NAME = "settings.ini" # 新增：设置文件名
    DEFAULT_VERSION = "2.2.0" # 默认版本号

    AUTO_RESOLUTION_MOD_NAME = "自动分辨率设置-Auto Resolution"


    def __init__(self):
        super().__init__()
        self.organizer = None
        self.version_label = None # 用于稍后更新标签
        self.plugin_path = os.path.dirname(__file__) # 获取插件目录
        self.config_path = os.path.join(self.plugin_path, self.CONFIG_FILE_NAME) # ini 文件路径
        self.settings_path = os.path.join(self.plugin_path, self.SETTINGS_FILE_NAME) # 新增：设置文件路径
        self.experience_mode = self._read_experience_mode()
        self._advanced_mode_widgets = []
        self.mode_combo = None
        self.compressor_button = None
        self.lod_pipeline_button = None
        self.mod_manager_button = None
        self.local_version = self._read_local_version() # 在初始化时读取
        self._xingli_update_status = {"state": "unknown", "latest": "", "source": "", "message": ""}
        self._xingli_modpack_update_status = {"state": "unknown", "installed": "", "latest": "", "source": "", "message": ""}
        self._modpack_update_manifest = None
        self._modpack_update_thread = None
        self._modpack_update_prompted_version = ""
        self._mo2_managed_app_running = False
        self._mo2_managed_app_name = ""
        self._help_request_timer = None
        self.update_status_label = None
        self.assistant_update_button = None
        self.update_button = None
        self.modpack_update_status_label = None
        self.modpack_update_button = None
        self.game_path = None # 新增：存储游戏路径的变量
        self.game_path_label = None # 新增：用于显示游戏路径的标签
        # --- DSD Generator 相关 ---
        self.dsd_exe_path = "" # 用于存储 ESP2DSD 路径
        self.dsd_copy_enabled = False # 用于存储复制选项
        self.dsd_blacklist_path = os.path.join(self.plugin_path, "dsd_blacklist.txt") # 黑名单路径
        self.dsd_blacklist_cache = [] # 黑名单缓存
        # --- 结束 DSD Generator 相关 ---

    # --- 玩家模式：新手 / 高级 ---
    def _read_experience_mode(self) -> str:
        config = configparser.ConfigParser()
        try:
            if os.path.exists(self.settings_path):
                config.read(self.settings_path, encoding="utf-8")
                mode = config.get("Interface", "ExperienceMode", fallback="novice").strip().lower()
                if mode in ("novice", "advanced"):
                    return mode
        except Exception as e:
            print(f"读取玩家模式失败，使用新手模式: {e}")
        return "novice"

    def _write_experience_mode(self, mode: str) -> None:
        mode = "advanced" if mode == "advanced" else "novice"
        config = configparser.ConfigParser()
        try:
            if os.path.exists(self.settings_path):
                config.read(self.settings_path, encoding="utf-8")
            if "Interface" not in config:
                config["Interface"] = {}
            config["Interface"]["ExperienceMode"] = mode
            with open(self.settings_path, "w", encoding="utf-8") as f:
                config.write(f)
        except Exception as e:
            print(f"保存玩家模式失败: {e}")

    def _on_experience_mode_changed(self, index: int) -> None:
        if not self.mode_combo:
            return
        mode = self.mode_combo.itemData(index)
        self.experience_mode = "advanced" if mode == "advanced" else "novice"
        self._write_experience_mode(self.experience_mode)
        self._apply_experience_mode_ui()
        try:
            if getattr(self, "window", None):
                self.window.resize(610, 545 if self.experience_mode == "advanced" else 445)
        except Exception:
            pass

    def _apply_experience_mode_ui(self) -> None:
        advanced = self.experience_mode == "advanced"
        for widget in getattr(self, "_advanced_mode_widgets", []):
            try:
                widget.setVisible(advanced)
            except Exception:
                pass
        if self.compressor_button is not None:
            self.compressor_button.setVisible(advanced)
            self.compressor_button.setEnabled(advanced)
            self.compressor_button.setText("🗜 模组压缩")
            self.compressor_button.setToolTip("高级模式可自定义压缩范围和算法，并支持批量处理。")
        if self.lod_pipeline_button is not None:
            self.lod_pipeline_button.setVisible(advanced)
            self.lod_pipeline_button.setEnabled(advanced)
            self.lod_pipeline_button.setToolTip("自动完成草缓存、xLODGen、TexGen 和 DynDOLOD，并管理生成时的 MOD 启停。")
        if self.mod_manager_button is not None:
            self.mod_manager_button.setVisible(advanced)
            self.mod_manager_button.setEnabled(advanced)
            self.mod_manager_button.setToolTip("批量安装、卸载和启停 MOD，并管理 MOD 组。")

    # --- 新增 INI 读写函数 ---
    def _read_game_path_from_ini(self) -> Optional[str]:
        """从 settings.ini 读取游戏路径"""
        config = configparser.ConfigParser()
        try:
            if os.path.exists(self.settings_path):
                config.read(self.settings_path, encoding='utf-8')
                if 'Settings' in config and 'GamePath' in config['Settings']:
                    path = config['Settings']['GamePath']
                    print(f"从 {self.SETTINGS_FILE_NAME} 读取到游戏路径: {path}")
                    return path
                else:
                    print(f"信息: {self.SETTINGS_FILE_NAME} 中未找到 [Settings] 或 'GamePath'。")
            else:
                print(f"信息: 未找到 {self.SETTINGS_FILE_NAME}。")
        except Exception as e:
            print(f"读取 {self.SETTINGS_FILE_NAME} 时出错: {e}")
        return None

    def _write_game_path_to_ini(self, path: str):
        """将游戏路径写入 settings.ini"""
        config = configparser.ConfigParser()
        # 如果文件已存在，先读取现有内容，避免覆盖其他设置
        if os.path.exists(self.settings_path):
            try:
                config.read(self.settings_path, encoding='utf-8')
            except Exception as e:
                print(f"读取现有 {self.SETTINGS_FILE_NAME} 失败: {e}。将创建新文件。")
                config = configparser.ConfigParser() # 重置以防读取错误

        if 'Settings' not in config:
            config['Settings'] = {}
        config['Settings']['GamePath'] = path

        try:
            with open(self.settings_path, 'w', encoding='utf-8') as configfile:
                config.write(configfile)
            print(f"已将游戏路径 {path} 写入 {self.SETTINGS_FILE_NAME}")
        except Exception as e:
            print(f"写入 {self.SETTINGS_FILE_NAME} 时出错: {e}")

    # --- 新增路径验证函数 ---
    def _validate_game_path(self, path: Optional[str]) -> bool:
        """检查给定路径是否是有效的游戏目录"""
        if not path or not os.path.isdir(path):
            print(f"验证失败：路径 '{path}' 无效或不是目录。")
            return False

        # 优先检查 skse64_loader.exe
        skse_path = os.path.join(path, "skse64_loader.exe")
        if os.path.exists(skse_path):
            print(f"验证成功：在 '{path}' 找到 skse64_loader.exe。")
            return True

        # 其次检查 SkyrimSE.exe
        skyrim_exe_path = os.path.join(path, "SkyrimSE.exe")
        if os.path.exists(skyrim_exe_path):
            print(f"验证成功（但警告）：在 '{path}' 找到 SkyrimSE.exe，但未找到 skse64_loader.exe。")
            # 仍然认为是有效路径，但可能提示用户安装 SKSE
            return True

        print(f"验证失败：在 '{path}' 未找到 SkyrimSE.exe 或 skse64_loader.exe。")
        return False

    def _find_and_store_game_path(self) -> Optional[str]:
        """
        查找有效的游戏路径（MO2 -> 注册表 -> 手动），
        验证后存储到 INI 文件并返回路径。
        """
        print("开始查找游戏路径...")
        found_path = None

        try:
            if self.organizer:
                managed_game = self.organizer.managedGame()
                if managed_game:
                    game_path_mo2 = managed_game.gameDirectory().absolutePath()
                    print(f"从 MO2 获取到路径: {game_path_mo2}")
                    if self._validate_game_path(game_path_mo2):
                        found_path = game_path_mo2
                        print("MO2 路径验证成功。")
                    else:
                        print("MO2 路径验证失败。")
                else:
                    print("调试日志：无法访问 managedGame()，可能 MO2 版本不兼容或未正确初始化。")
            else:
                print("调试日志：Organizer 尚未初始化，无法从 MO2 获取路径。")
        except AttributeError:
             print("警告: 无法访问 managedGame() 或 gameDirectory()。可能 MO2 版本不兼容或未正确初始化。")
        except Exception as e:
            print(f"从 MO2 获取游戏路径时出错: {e}")

        if not found_path:
            print("调试日志：尝试从注册表获取路径...")
            game_path_reg = self._get_game_path_from_registry() # 已有方法
            if game_path_reg:
                print(f"调试日志：从注册表获取到路径: {game_path_reg}")
                if self._validate_game_path(game_path_reg):
                    found_path = game_path_reg
                    print("调试日志：注册表路径验证成功。")
                else:
                    print("调试日志：注册表路径验证失败。")
            else:
                print("调试日志：未在注册表中找到路径。")

        if not found_path:
            print("尝试提示用户手动选择路径...")
            game_path_manual = self._prompt_for_game_path() # 需要修改此函数返回值
            if game_path_manual:
                print(f"用户手动选择了路径: {game_path_manual}")
                if self._validate_game_path(game_path_manual):
                    found_path = game_path_manual
                    print("手动选择的路径验证成功。")
                else:
                    # 如果 _prompt 返回了路径但 _validate 失败，说明 prompt 逻辑需调整
                    print("手动选择的路径最终验证失败。")
                    # 可以在这里再次提示错误，但 _prompt 内部应该已经提示过了
                    found_path = None # 确保是失败状态
            else:
                print("用户取消了手动选择或选择的路径无效。")


        # 4. 如果找到有效路径，存储到 INI
        if found_path:
            print(f"最终找到有效游戏路径: {found_path}")
            self._write_game_path_to_ini(found_path)
            return found_path
        else:
            print("未能找到有效的游戏路径。")
            # 提示最终错误，因为所有方法都失败了
            QtWidgets.QMessageBox.critical(
                None, "游戏路径查找失败",
                "未找到有效的游戏路径。\n\n"
                "请确认游戏已正确安装，并且 MO2 当前实例指向正确的游戏。"
                "手动选择时，请选择包含 SkyrimSE.exe 的游戏根目录。\n\n"
                "找到正确路径前，ENB 管理功能不可用。"
            )
            return None
    def _read_local_version(self) -> str:
        """从 version.ini 读取本地版本号"""
        config = configparser.ConfigParser()
        try:
            if os.path.exists(self.config_path):
                config.read(self.config_path, encoding='utf-8')
                if 'Version' in config and 'local' in config['Version']:
                    version_str = config['Version']['local'].strip()
                    # 允许 1.1.5-user / v1.1.5 这类用户版版本号
                    if re.search(r"\d+", version_str):
                        print(f"从 {self.CONFIG_FILE_NAME} 读取到版本: {version_str}")
                        return version_str
                    else:
                        print(f"警告: {self.CONFIG_FILE_NAME} 中的版本号格式无效: {version_str}。使用默认版本。")
                else:
                    print(f"警告: {self.CONFIG_FILE_NAME} 中缺少 [Version] 或 'local' 键。使用默认版本。")
            else:
                print(f"信息: 未找到 {self.CONFIG_FILE_NAME}。使用默认版本。")
        except Exception as e:
            print(f"读取 {self.CONFIG_FILE_NAME} 时出错: {e}。使用默认版本。")

        # 如果读取失败或文件不存在，返回默认值并尝试创建文件
        self._write_local_version(self.DEFAULT_VERSION) # 写入默认值
        return self.DEFAULT_VERSION

    def _write_local_version(self, version_str: str):
        """将本地版本号写入 version.ini"""
        config = configparser.ConfigParser()
        config['Version'] = {'local': version_str}
        try:
            with open(self.config_path, 'w', encoding='utf-8') as configfile:
                config.write(configfile)
            print(f"已将版本 {version_str} 写入 {self.CONFIG_FILE_NAME}")
        except Exception as e:
            print(f"写入 {self.CONFIG_FILE_NAME} 时出错: {e}")


    def init(self, organizer: mobase.IOrganizer):
        self.organizer = organizer
        self.mod_state = ModStateService(organizer)
        stability.configure_logging(organizer, self.plugin_path)
        stability.log_runtime_compatibility(organizer, self.plugin_path)
        # 清理旧更新缓存；未来版本已下载但尚未安装的有效包会保留。
        try:
            auto_updater.cleanup_update_workspace(self)
        except Exception:
            logger.exception("启动时清理旧更新缓存失败")
        # --- 新增：初始化时查找或加载游戏路径 ---
        print("初始化：开始处理游戏路径...")
        # 1. 尝试从 INI 文件读取已保存的路径
        ini_path = self._read_game_path_from_ini()
        if ini_path and self._validate_game_path(ini_path):
            print(f"初始化：使用 INI 文件中的有效路径: {ini_path}")
            self.game_path = ini_path
        else:
            # 2. 如果 INI 无效或不存在，执行查找流程
            print("初始化：INI 路径无效或未找到，开始执行查找...")
            # 注意：_find_and_store_game_path 会处理查找、验证和写入 INI
            # 它也会在找不到时显示错误消息
            self.game_path = self._find_and_store_game_path()
            if self.game_path:
                print(f"初始化：查找到并存储了新路径: {self.game_path}")
            else:
                print("初始化：未能找到有效的游戏路径。")
                # self.game_path 保持为 None
        # --- 结束新增 ---

        # --- 新增：读取 DSD 设置 ---
        self._read_dsd_settings_from_ini()
        self._read_dsd_blacklist() # 读取黑名单到缓存
        # --- 结束新增 ---

        # 记录是否有程序正在通过 MO2 运行。仅使用简单参数回调，兼容 MO2 2.4.4。
        try:
            organizer.onAboutToRun(self._on_mo2_about_to_run)
            organizer.onFinishedRun(self._on_mo2_finished_run)
        except Exception as e:
            logger.warning("注册 MO2 运行状态回调失败: %s", e)

        # 每次启动 MO2 时刷新两个桌面入口：日常游戏 + 帮助/教程。
        self.ensure_desktop_launcher(silent=True)

        # 主菜单版本先使用本机版本 + 上次公开更新缓存同步，避免网络尚未返回时仍显示旧版本。
        try:
            local_release = modpack_updater.load_local_release(self)
            mainmenu_sync.sync(self, str(local_release.get("installed_version", "") or ""))
        except Exception:
            logger.exception("启动时同步 Main Menu Customizer 失败")

        # 用户版自动更新：助手与整合包分别静默检查。
        QTimer.singleShot(2600, lambda: auto_updater.maybe_auto_check(self))
        QTimer.singleShot(3300, lambda: modpack_updater.maybe_auto_check(self))

        # 从桌面【星黎帮助与教程】进入时自动打开教程；否则欢迎页只显示一次。
        initial_help_request = self._consume_help_request()
        if initial_help_request:
            QTimer.singleShot(1400, self.show_tutorial_window)
        elif self._should_show_welcome():
            QTimer.singleShot(1800, self.show_welcome_dialog)

        # MO2 已经打开时，桌面【星黎帮助与教程】仍然能触发教程，不要求重启 MO2。
        self._help_request_timer = QTimer()
        self._help_request_timer.setInterval(1200)
        self._help_request_timer.timeout.connect(self._poll_help_request)
        self._help_request_timer.start()
        return True

    def _on_mo2_about_to_run(self, executable):
        """Track applications launched through MO2 so updates never close a locked session."""
        self._mo2_managed_app_running = True
        self._mo2_managed_app_name = str(executable or "")
        return True

    def _on_mo2_finished_run(self, executable, exit_code):
        self._mo2_managed_app_running = False
        self._mo2_managed_app_name = ""

    def _is_mo2_managed_app_running(self) -> bool:
        return bool(getattr(self, "_mo2_managed_app_running", False))

    # --- 用户桌面启动器 ---
    def _mo2_base_path(self) -> str:
        """返回当前 MO2 根目录。优先使用 MO2 API；失败时按插件目录回推。"""
        try:
            if self.organizer:
                base = self.organizer.basePath()
                if base:
                    return os.path.abspath(str(base))
        except Exception as e:
            print(f"获取 MO2 basePath 失败，使用插件相对路径回退: {e}")
        return os.path.abspath(os.path.join(self.plugin_path, "..", ".."))

    def _desktop_dir(self) -> str:
        """返回当前 Windows 用户桌面目录，兼容 OneDrive/本地桌面。"""
        try:
            desktop = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)
            if desktop and os.path.isdir(desktop):
                return os.path.abspath(desktop)
        except Exception as e:
            print(f"QStandardPaths 获取桌面失败: {e}")
        userprofile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
        return os.path.abspath(os.path.join(userprofile, "Desktop"))

    def _desktop_launcher_path(self) -> str:
        return os.path.join(self._desktop_dir(), "星黎整合启动器.bat")

    def _desktop_help_path(self) -> str:
        return os.path.join(self._desktop_dir(), "星黎帮助与教程.bat")

    def _help_request_path(self) -> str:
        return os.path.join(self._mo2_base_path(), "_XingliOpenHelp.request")

    def _modpack_root(self) -> str:
        """Return the physical modpack root (the parent directory of MO2)."""
        return os.path.abspath(os.path.join(self._mo2_base_path(), ".."))

    def _onboarding_instance_id(self) -> str:
        instance_id, method = instance_identity.resolve_instance_identity(self._modpack_root())
        if method != "win32-file-id":
            logger.warning("整合实例身份使用回退方式: %s", method)
        return instance_id

    def _onboarding_state_path(self) -> str:
        return onboarding_state.state_path(self._onboarding_instance_id())

    def _should_show_welcome(self) -> bool:
        try:
            return onboarding_state.should_show_welcome(self._onboarding_instance_id())
        except Exception:
            logger.exception("读取新手欢迎状态失败；按首次启动处理")
            return True

    def _mark_welcome_seen(self) -> None:
        try:
            onboarding_state.mark_welcome_seen(self._onboarding_instance_id())
        except Exception:
            logger.exception("保存新手欢迎状态失败")

    def _consume_help_request(self) -> bool:
        path = self._help_request_path()
        if not os.path.isfile(path):
            return False
        try:
            os.remove(path)
        except Exception:
            logger.exception("删除帮助入口请求文件失败")
        return True

    def _poll_help_request(self) -> None:
        if self._consume_help_request():
            QTimer.singleShot(0, self.show_tutorial_window)

    def _launcher_ps1_path(self) -> str:
        # helper 放在插件目录中，桌面只放用户可见的 bat 入口。
        return os.path.join(self.plugin_path, "XingliDesktopLauncher.ps1")

    def _powershell_quote(self, value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def _write_desktop_launcher_scripts(self) -> None:
        ps1_path = self._launcher_ps1_path()
        desktop_bat = self._desktop_launcher_path()
        os.makedirs(os.path.dirname(desktop_bat), exist_ok=True)

        ps1 = fr"""# coding: utf-8
param([string]$Mode = 'Game')
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$PluginDir = $PSScriptRoot
$PluginsDir = Split-Path -Parent $PluginDir
$Mo2Dir = Split-Path -Parent $PluginsDir
$MoExe = Join-Path $Mo2Dir 'ModOrganizer.exe'
$Ini = Join-Path $Mo2Dir 'ModOrganizer.ini'
$ShortcutArg = 'moshortcut://Skyrim Special Edition:SKSE'

Write-Host '========================================'
Write-Host '        星黎整合启动器'
Write-Host '========================================'
Write-Host "MO2 目录: $Mo2Dir"

if (!(Test-Path -LiteralPath $MoExe)) {{
    Write-Host '[错误] 未找到 ModOrganizer.exe。'
    Write-Host "预期位置: $MoExe"
    exit 10
}}

if ($Mode -eq 'Help') {{
    $Request = Join-Path $Mo2Dir '_XingliOpenHelp.request'
    Set-Content -LiteralPath $Request -Value 'open' -Encoding ASCII
    Write-Host '[帮助] 正在打开 MO2；星黎小助手会自动弹出教程中心。'
    Start-Process -FilePath $MoExe -WorkingDirectory $Mo2Dir
    exit 0
}}

function Get-IniGamePath {{
    if (!(Test-Path -LiteralPath $Ini)) {{ return $null }}
    $line = Get-Content -LiteralPath $Ini -Encoding UTF8 -ErrorAction SilentlyContinue | Where-Object {{ $_ -match '^gamePath=' }} | Select-Object -First 1
    if ($line) {{ return (($line -replace '^gamePath=', '').Trim().Trim('"')) }}
    return $null
}}

$Candidates = New-Object System.Collections.Generic.List[string]
$IniGamePath = Get-IniGamePath
if ($IniGamePath) {{ [void]$Candidates.Add($IniGamePath) }}
foreach ($rel in @('Stock Game','Game Root','Game','Skyrim Special Edition','SkyrimSE')) {{
    [void]$Candidates.Add((Join-Path $Mo2Dir $rel))
}}
$Parent = Split-Path -Parent $Mo2Dir
foreach ($rel in @('Stock Game','Skyrim Special Edition','SkyrimSE')) {{
    [void]$Candidates.Add((Join-Path $Parent $rel))
}}

$Skse = $null
$GameDir = $null
foreach ($dir in ($Candidates | Select-Object -Unique)) {{
    if (!$dir) {{ continue }}
    $candidate = Join-Path $dir 'skse64_loader.exe'
    if (Test-Path -LiteralPath $candidate) {{
        $Skse = (Resolve-Path -LiteralPath $candidate).Path
        $GameDir = (Resolve-Path -LiteralPath $dir).Path
        break
    }}
}}

if (!$Skse) {{
    Write-Host '[错误] 未找到 skse64_loader.exe。'
    Write-Host '请确认整合包包含 Stock Game 或正确的游戏根目录。'
    exit 11
}}
Write-Host "SKSE: $Skse"

function Repair-SkseExecutable {{
    param([string]$IniPath, [string]$SksePath, [string]$WorkDir)
    if (!(Test-Path -LiteralPath $IniPath)) {{ return }}
    $lines = Get-Content -LiteralPath $IniPath -Encoding UTF8 -ErrorAction SilentlyContinue
    $idx = $null
    foreach ($line in $lines) {{
        if ($line -match '^([0-9]+)\\title=(.+)$') {{
            if ($Matches[2].Trim().ToLowerInvariant() -eq 'skse') {{
                $idx = $Matches[1]
                break
            }}
        }}
    }}
    if (!$idx) {{
        Write-Host '[提示] ModOrganizer.ini 中未找到名为 SKSE 的可执行项，将直接启动 MO2。'
        return
    }}

    $safeSkse = $SksePath.Replace([char]92,'/')
    $safeWorkDir = $WorkDir.Replace([char]92,'/')
    $newLines = New-Object System.Collections.Generic.List[string]
    $hasBinary = $false
    $hasWorkDir = $false
    foreach ($line in $lines) {{
        if ($line -match ('^' + [regex]::Escape($idx) + '\\binary=')) {{
            [void]$newLines.Add("$idx\binary=$safeSkse")
            $hasBinary = $true
        }} elseif ($line -match ('^' + [regex]::Escape($idx) + '\\workingDirectory=')) {{
            [void]$newLines.Add("$idx\workingDirectory=$safeWorkDir")
            $hasWorkDir = $true
        }} else {{
            [void]$newLines.Add($line)
        }}
    }}
    if (!$hasBinary) {{ [void]$newLines.Add("$idx\binary=$safeSkse") }}
    if (!$hasWorkDir) {{ [void]$newLines.Add("$idx\workingDirectory=$safeWorkDir") }}
    Set-Content -LiteralPath $IniPath -Value $newLines -Encoding UTF8
    Write-Host '[完成] 已校正 MO2 中 SKSE 的本机路径。'
}}

Repair-SkseExecutable -IniPath $Ini -SksePath $Skse -WorkDir $GameDir
Write-Host '[启动] 正在通过 MO2 启动 SKSE...'
Start-Process -FilePath $MoExe -ArgumentList $ShortcutArg -WorkingDirectory $Mo2Dir
exit 0
"""

        bat = fr"""@echo off
chcp 65001 >nul
title 星黎整合启动器
set "XINGLI_LAUNCHER_PS1={ps1_path}"
if not exist "%XINGLI_LAUNCHER_PS1%" (
    echo [错误] 未找到星黎启动脚本：
    echo %XINGLI_LAUNCHER_PS1%
    echo.
    echo 请打开 MO2 后进入【星黎用户助手】重新创建桌面启动器。
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%XINGLI_LAUNCHER_PS1%"
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
    echo.
    echo [星黎整合启动器] 启动没有完成，错误码：%ERR%
    echo 请打开 MO2，点击【星黎用户助手】复制诊断报告发给整合包维护人员。
    pause
)
exit /b %ERR%
"""
        # .bat 使用 utf-8-sig，提高中文标题/文件名在 Windows cmd 中的兼容性。
        with open(ps1_path, "w", encoding="utf-8-sig", newline="\r\n") as handle:
            handle.write(ps1)
        with open(desktop_bat, "w", encoding="utf-8-sig", newline="\r\n") as handle:
            handle.write(bat)

        help_bat = fr"""@echo off
chcp 65001 >nul
title 星黎帮助与教程
set "XINGLI_LAUNCHER_PS1={ps1_path}"
if not exist "%XINGLI_LAUNCHER_PS1%" (
    echo [错误] 未找到星黎小助手启动脚本。
    echo 请先打开 MO2 一次，插件会自动修复桌面入口。
    pause
    exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%XINGLI_LAUNCHER_PS1%" -Mode Help
exit /b %ERRORLEVEL%
"""
        with open(self._desktop_help_path(), "w", encoding="utf-8-sig", newline="\r\n") as handle:
            handle.write(help_bat)

    def ensure_desktop_launcher(self, silent: bool = False) -> Optional[str]:
        """创建/刷新桌面的游戏入口和帮助/教程入口。"""
        try:
            self._write_desktop_launcher_scripts()
            path = self._desktop_launcher_path()
            print(f"已创建/刷新桌面星黎整合启动器: {path}")
            if not silent:
                QtWidgets.QMessageBox.information(None, "已创建桌面入口", f"已在桌面创建：\n{path}\n{self._desktop_help_path()}")
            return path
        except Exception as e:
            print(f"创建桌面星黎整合启动器失败: {e}")
            if not silent:
                QtWidgets.QMessageBox.warning(None, "创建失败", f"创建桌面启动器失败：\n{e}")
            return None

    def name(self) -> str:
        return self.NAME

    def author(self) -> str:
        return "YourName"

    def description(self) -> str:
        return u"星黎整合用户助手：新手/高级双模式、启动自检、整合包检查、模组管理中心、ENB 管理、LOD自动化、自动更新与排错；管理/压缩/LOD仅高级玩家可用"

    def version(self) -> mobase.VersionInfo:
        # 暂时返回一个固定的版本号进行调试
        print("version() 返回版本 2.2.0-user")
        return mobase.VersionInfo(2, 2, 0)

    def settings(self) -> list:
        return []

    def displayName(self) -> str:
        return "星黎MO2小助手"  

    def tooltip(self) -> str:
        return "新手/高级双模式：启动自检、整合包检查、模组管理中心、ENB 管理、LOD自动化、自动更新、教程与排错；管理/压缩/LOD仅高级玩家可用"  

    def icon(self) -> QtGui.QIcon:
        icon_path = os.path.join(self.plugin_path, "icon.png") # 假设图标文件名为 icon.png
        print(f"尝试加载插件图标: {icon_path}") # 添加日志
        try:
            if os.path.exists(icon_path):
                loaded_icon = QtGui.QIcon(icon_path)
                if not loaded_icon.isNull():
                    print("插件图标加载成功。")
                    return loaded_icon
                else:
                    print(f"警告: 插件图标文件 '{icon_path}' 加载后为空，可能文件损坏或格式不支持。")
            else:
                print(f"警告: 插件图标文件未找到: {icon_path}")
        except Exception as e:
            print(f"加载插件图标时发生错误: {e}")

        # 如果加载失败或文件不存在，返回一个空的 QIcon 对象
        print("返回空的 QIcon 对象以避免 TypeError。")
        return QtGui.QIcon()
    def show_welcome_dialog(self):
        welcome_dialog = QtWidgets.QDialog()
        welcome_dialog.setWindowTitle("欢迎使用星黎 MO2 小助手")
        welcome_dialog.setWindowFlags(welcome_dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint) # 移除帮助按钮
        welcome_dialog.setMinimumWidth(560)

        layout = QtWidgets.QVBoxLayout(welcome_dialog)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # 欢迎标题
        title_label = QtWidgets.QLabel("✨ 欢迎使用星黎 MO2 小助手 ✨")
        title_font = QtGui.QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        # 欢迎信息：新手首先看到的一键启动提示
        info_label = QtWidgets.QLabel(
            "感谢你使用星黎整合。这个窗口来自【星黎 MO2 小助手】。\n\n"
            "只想玩游戏：双击桌面【星黎整合启动器.bat】。\n"
            "想看教程、检查更新或排错：双击桌面【星黎帮助与教程.bat】。\n\n"
            "以后即使不知道 MO2 里的插件入口，也可以从桌面直接进入教程。"
        )
        info_label.setAlignment(Qt.AlignCenter)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        launcher_tip = QtWidgets.QLabel(
            "✅ 游戏入口与帮助入口已经分开，不需要先学会 MO2。\n"
            "⚠️ 不熟悉 MO2 时，不要随意改左侧列表、右侧插件顺序或可执行程序。"
        )
        launcher_tip.setWordWrap(True)
        launcher_tip.setAlignment(Qt.AlignCenter)
        launcher_tip.setStyleSheet(
            "QLabel { background-color: #fff2cc; color: #5f4300; border: 1px solid #d6b656; "
            "border-radius: 6px; padding: 10px; font-size: 12px; }"
        )
        layout.addWidget(launcher_tip)

        # 按钮布局
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setSpacing(10)

        # 按钮样式 (可以复用 display 中的样式)
        button_style = """
        QPushButton {
            background-color: #4a86e8; color: white; border: none;
            padding: 10px 15px; border-radius: 5px; font-size: 12px;
        }
        QPushButton:hover { background-color: #3a76d8; }
        QPushButton:pressed { background-color: #2a66c8; }
        """

        tutorial_button = QtWidgets.QPushButton("📖 打开新手教程")
        tutorial_button.setStyleSheet(button_style)
        def open_tutorial_and_close():
            welcome_dialog.accept()
            QTimer.singleShot(0, self.show_tutorial_window)
        tutorial_button.clicked.connect(open_tutorial_and_close)
        button_layout.addWidget(tutorial_button)

        open_button = QtWidgets.QPushButton("🛠 打开小助手")
        open_button.setStyleSheet(button_style.replace("#4a86e8", "#5f6f82").replace("#3a76d8", "#536273").replace("#2a66c8", "#475566"))
        def open_and_close():
            welcome_dialog.accept()
            QTimer.singleShot(0, self.display)
        open_button.clicked.connect(open_and_close)
        button_layout.addWidget(open_button)

        layout.addLayout(button_layout)
        welcome_dialog.setLayout(layout)

        # 只有欢迎窗口实际显示并结束后才记录完成状态。
        # 如果创建/显示过程中异常退出，下次启动仍按首次启动处理。
        welcome_dialog.exec()
        self._mark_welcome_seen()


    def _set_update_home_status(self, status) -> None:
        """Update the compact update indicator on the home page."""
        if isinstance(status, dict):
            self._xingli_update_status = dict(status)
        data = getattr(self, "_xingli_update_status", {}) or {}
        state = str(data.get("state", "unknown"))
        latest = str(data.get("latest", ""))
        if state == "update_available":
            text = "🟠 有新版本 {}".format(latest or "")
            tip = "发现星黎助手新版本，点击查看并更新。"
            button_text = "更新助手"
        elif state == "up_to_date":
            text = "🟢 已是最新版本 {}".format(self.local_version)
            tip = "启动时检查已完成，当前版本是最新版本。"
            button_text = "检查助手"
        elif state == "checking":
            text = "🔵 正在检查更新…"
            tip = "正在连接国内更新源。"
            button_text = "检查助手"
        elif state == "error":
            text = "⚪ 暂时无法检查更新"
            tip = str(data.get("message", "请稍后手动检查。"))
            button_text = "重试助手"
        elif state == "disabled":
            text = "⚪ 自动检查已关闭"
            tip = "可以在更新页面重新开启。"
            button_text = "助手设置"
        else:
            text = "⚪ 更新状态尚未检查"
            tip = "打开 MO2 后会自动检查一次，也可以手动检查。"
            button_text = "检查助手"
        if self.update_status_label is not None:
            try:
                self.update_status_label.setText(text)
                self.update_status_label.setToolTip(tip)
            except Exception:
                pass
        if self.assistant_update_button is not None:
            try:
                self.assistant_update_button.setText(button_text)
                self.assistant_update_button.setToolTip(tip)
            except Exception:
                pass

    def _set_modpack_update_home_status(self, status) -> None:
        """Update the compact modpack-content update indicator."""
        if isinstance(status, dict):
            self._xingli_modpack_update_status = dict(status)
        data = getattr(self, "_xingli_modpack_update_status", {}) or {}
        state = str(data.get("state", "unknown"))
        installed = str(data.get("installed", "") or "")
        latest = str(data.get("latest", "") or "")
        if state == "update_available":
            text = "🟠 有新版本 {}".format(latest)
            tip = "整合包内容有新版本，确认后可打开作者网盘页面。"
            button_text = "获取整合包"
        elif state == "up_to_date":
            text = "🟢 已是最新版本 {}".format(installed or latest)
            tip = "当前整合包内容版本已是最新。"
            button_text = "检查整合包"
        elif state == "checking":
            text = "🔵 正在检查…"
            tip = "正在检查整合包内容版本。"
            button_text = "检查整合包"
        elif state == "disabled":
            text = "⚪ 更新提示尚未启用"
            tip = str(data.get("message", "作者尚未启用整合包更新提示。"))
            button_text = "检查整合包"
        elif state == "unconfigured":
            text = "⚪ 未记录整合包版本"
            tip = str(data.get("message", "作者尚未配置本机整合包版本。"))
            button_text = "检查整合包"
        elif state == "error":
            text = "⚪ 暂时无法检查"
            tip = str(data.get("message", "请稍后重试。"))
            button_text = "重试整合包"
        else:
            text = "⚪ 尚未检查"
            tip = "启动后会自动检查整合包内容版本。"
            button_text = "检查整合包"
        if self.modpack_update_status_label is not None:
            try:
                self.modpack_update_status_label.setText(text)
                self.modpack_update_status_label.setToolTip(tip)
            except Exception:
                pass
        if self.modpack_update_button is not None:
            try:
                self.modpack_update_button.setText(button_text)
                self.modpack_update_button.setToolTip(tip)
            except Exception:
                pass

    def check_all_updates(self):
        """Refresh assistant and modpack update states without ambiguous routing."""
        try:
            auto_updater.manual_home_check(self)
        except Exception:
            logger.exception("手动检查助手版本失败")
        try:
            modpack_updater.manual_check(self, prompt_when_new=False)
        except Exception:
            logger.exception("手动检查整合包版本失败")

    def show_modpack_update(self):
        """Check modpack content updates or open the published download page."""
        try:
            return modpack_updater.handle_home_action(self)
        except Exception as e:
            logger.exception("整合包更新检查失败")
            QtWidgets.QMessageBox.warning(
                self.window if hasattr(self, "window") else None,
                "整合包更新",
                "无法检查整合包更新：\n{}".format(e),
            )

    def show_modpack_health(self):
        """Open the read-only modpack baseline / health check center."""
        try:
            return modpack_health.show_modpack_health(
                self.window if hasattr(self, "window") else None,
                self.organizer,
                self.plugin_path,
                self.local_version,
            )
        except Exception as e:
            logger.exception("打开整合包检查失败")
            QtWidgets.QMessageBox.critical(
                self.window if hasattr(self, "window") else None,
                "打开失败",
                "无法打开整合包检查：\n{}".format(e),
            )

    def display(self) -> None:
        self.window = QtWidgets.QDialog()
        self.version_label = None
        self.window.setWindowTitle("星黎 MO2 小助手")
        self.window.setWindowFlags(
            self.window.windowFlags() | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
        )

        self.window.setStyleSheet("""
            QDialog { background: #f5f6f8; }
            QLabel#HomeTitle { color: #202124; }
            QLabel#SectionTitle { color: #5f6368; font-weight: 600; }
            QFrame#InfoCard, QFrame#ActionCard {
                background: white;
                border: 1px solid #dfe3e8;
                border-radius: 9px;
            }
            QPushButton {
                min-height: 34px;
                padding: 6px 12px;
                border: 1px solid #cfd5dc;
                border-radius: 6px;
                background: white;
                color: #202124;
            }
            QPushButton:hover { background: #f2f5f9; border-color: #aeb7c2; }
            QPushButton:pressed { background: #e8edf3; }
            QPushButton#PrimaryAction {
                background: #3f78d7;
                color: white;
                border-color: #3f78d7;
                font-weight: 600;
            }
            QPushButton#PrimaryAction:hover { background: #356bc2; }
            QPushButton#SubtleAction {
                background: transparent;
                border: none;
                color: #3569b8;
                min-height: 28px;
                padding: 3px 8px;
            }
            QComboBox {
                min-height: 28px;
                padding: 2px 8px;
                border: 1px solid #cfd5dc;
                border-radius: 5px;
                background: white;
            }
            QLineEdit {
                min-height: 28px;
                padding: 2px 8px;
                border: 1px solid #d8dde4;
                border-radius: 5px;
                background: #fafbfc;
                color: #4a4f55;
            }
        """)

        main_layout = QtWidgets.QVBoxLayout(self.window)
        main_layout.setContentsMargins(18, 16, 18, 14)
        main_layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)
        title_label = QtWidgets.QLabel("星黎 MO2 小助手")
        title_label.setObjectName("HomeTitle")
        title_font = QtGui.QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        header.addWidget(title_label)
        header.addStretch(1)

        mode_label = QtWidgets.QLabel("模式")
        mode_label.setStyleSheet("color:#6b7076;")
        header.addWidget(mode_label)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItem("新手玩家", "novice")
        self.mode_combo.addItem("高级玩家", "advanced")
        current_mode_index = self.mode_combo.findData(self.experience_mode)
        if current_mode_index >= 0:
            self.mode_combo.setCurrentIndex(current_mode_index)
        self.mode_combo.setToolTip("新手模式仅显示日常使用功能；高级模式显示整合包维护工具。")
        self.mode_combo.currentIndexChanged.connect(self._on_experience_mode_changed)
        header.addWidget(self.mode_combo)
        main_layout.addLayout(header)

        info_card = QtWidgets.QFrame()
        info_card.setObjectName("InfoCard")
        info_layout = QtWidgets.QVBoxLayout(info_card)
        info_layout.setContentsMargins(12, 10, 12, 10)
        info_layout.setSpacing(7)

        path_row = QtWidgets.QHBoxLayout()
        path_label = QtWidgets.QLabel("游戏路径")
        path_label.setMinimumWidth(60)
        path_label.setStyleSheet("font-weight:600;")
        path_row.addWidget(path_label)
        self.game_path_label = QtWidgets.QLineEdit()
        self.game_path_label.setReadOnly(True)
        if self.game_path:
            self.game_path_label.setText(self.game_path)
            self.game_path_label.setToolTip(self.game_path)
        else:
            self.game_path_label.setText("未找到有效游戏路径")
            self.game_path_label.setToolTip("部分功能可能无法使用，请重新扫描或手动设置。")
        path_row.addWidget(self.game_path_label, 1)
        path_btn = QtWidgets.QPushButton("路径设置")
        path_btn.setMaximumWidth(86)
        path_btn.clicked.connect(self._manually_set_game_path_and_refresh_ui)
        path_row.addWidget(path_btn)
        info_layout.addLayout(path_row)

        status_row = QtWidgets.QHBoxLayout()
        assistant_status_title = QtWidgets.QLabel("助手更新")
        assistant_status_title.setMinimumWidth(60)
        assistant_status_title.setStyleSheet("font-weight:600;")
        status_row.addWidget(assistant_status_title)
        self.update_status_label = QtWidgets.QLabel()
        self.update_status_label.setStyleSheet("color:#4d5359;")
        status_row.addWidget(self.update_status_label, 1)
        self.assistant_update_button = QtWidgets.QPushButton("检查助手")
        self.assistant_update_button.setMaximumWidth(100)
        self.assistant_update_button.clicked.connect(self.show_auto_updater)
        status_row.addWidget(self.assistant_update_button)
        info_layout.addLayout(status_row)

        pack_status_row = QtWidgets.QHBoxLayout()
        pack_status_title = QtWidgets.QLabel("整合包更新")
        pack_status_title.setMinimumWidth(60)
        pack_status_title.setStyleSheet("font-weight:600;")
        pack_status_row.addWidget(pack_status_title)
        self.modpack_update_status_label = QtWidgets.QLabel()
        self.modpack_update_status_label.setStyleSheet("color:#4d5359;")
        pack_status_row.addWidget(self.modpack_update_status_label, 1)
        self.modpack_update_button = QtWidgets.QPushButton("检查")
        self.modpack_update_button.setMaximumWidth(100)
        self.modpack_update_button.clicked.connect(self.show_modpack_update)
        pack_status_row.addWidget(self.modpack_update_button)
        info_layout.addLayout(pack_status_row)

        main_layout.addWidget(info_card)
        self._set_update_home_status(getattr(self, "_xingli_update_status", {}))
        self._set_modpack_update_home_status(getattr(self, "_xingli_modpack_update_status", {}))

        launcher_hint = QtWidgets.QLabel(
            "只想玩游戏：桌面【星黎整合启动器.bat】；需要教程/排错：桌面【星黎帮助与教程.bat】。"
        )
        launcher_hint.setWordWrap(True)
        launcher_hint.setStyleSheet("color:#5f6368; padding:0 2px;")
        main_layout.addWidget(launcher_hint)

        daily_title = QtWidgets.QLabel("常用功能")
        daily_title.setObjectName("SectionTitle")
        main_layout.addWidget(daily_title)

        daily_card = QtWidgets.QFrame()
        daily_card.setObjectName("ActionCard")
        daily_grid = QtWidgets.QGridLayout(daily_card)
        daily_grid.setContentsMargins(10, 10, 10, 10)
        daily_grid.setHorizontalSpacing(8)
        daily_grid.setVerticalSpacing(8)

        user_assistant_button = QtWidgets.QPushButton("🎮  用户助手")
        user_assistant_button.setObjectName("PrimaryAction")
        user_assistant_button.setToolTip("启动游戏、环境自检和常见问题修复。")
        user_assistant_button.clicked.connect(self.show_user_assistant)
        daily_grid.addWidget(user_assistant_button, 0, 0)

        health_button = QtWidgets.QPushButton("🩺  整合包检查")
        health_button.setToolTip("检查 MOD 启停、顺序和配置是否偏离当前基准。")
        health_button.clicked.connect(self.show_modpack_health)
        daily_grid.addWidget(health_button, 0, 1)

        tutorial_button = QtWidgets.QPushButton("📖  新手指南")
        tutorial_button.clicked.connect(self.open_tutorial)
        daily_grid.addWidget(tutorial_button, 1, 0)

        self.update_button = QtWidgets.QPushButton("↻  检查全部更新")
        self.update_button.setToolTip("同时刷新星黎助手版本和整合包内容版本。")
        self.update_button.clicked.connect(self.check_all_updates)
        daily_grid.addWidget(self.update_button, 1, 1)
        main_layout.addWidget(daily_card)
        self._set_update_home_status(getattr(self, "_xingli_update_status", {}))

        self.advanced_section_title = QtWidgets.QLabel("整合包维护")
        self.advanced_section_title.setObjectName("SectionTitle")
        main_layout.addWidget(self.advanced_section_title)

        self.advanced_card = QtWidgets.QFrame()
        self.advanced_card.setObjectName("ActionCard")
        advanced_grid = QtWidgets.QGridLayout(self.advanced_card)
        advanced_grid.setContentsMargins(10, 10, 10, 10)
        advanced_grid.setHorizontalSpacing(8)
        advanced_grid.setVerticalSpacing(8)

        self.mod_manager_button = QtWidgets.QPushButton("📦  模组管理")
        self.mod_manager_button.clicked.connect(self.show_mod_manager)
        advanced_grid.addWidget(self.mod_manager_button, 0, 0)

        self.lod_pipeline_button = QtWidgets.QPushButton("🌲  LOD 自动化")
        self.lod_pipeline_button.clicked.connect(self.show_lod_pipeline)
        advanced_grid.addWidget(self.lod_pipeline_button, 0, 1)

        self.compressor_button = QtWidgets.QPushButton("🗜  模组压缩")
        self.compressor_button.clicked.connect(self.show_mod_compressor)
        advanced_grid.addWidget(self.compressor_button, 1, 0)

        self.more_tools_button = QtWidgets.QPushButton("⋯  更多维护工具")
        more_menu = QtWidgets.QMenu(self.more_tools_button)
        enb_action = more_menu.addAction("ENB 管理")
        enb_action.triggered.connect(self.manage_enb)
        dsd_action = more_menu.addAction("生成 DSD 配置")
        dsd_action.triggered.connect(self.show_dsd_config_dialog)
        crash_action = more_menu.addAction("崩溃日志查看器")
        crash_action.triggered.connect(self.show_crash_log_viewer)
        pgpatcher_action = more_menu.addAction("PBR / PGPatcher")
        pgpatcher_action.triggered.connect(self.launch_pgpatcher)
        more_menu.addSeparator()
        page_action = more_menu.addAction("虚拟内存信息")
        page_action.triggered.connect(self._get_page_file_info)
        system_action = more_menu.addAction("打开系统设置")
        system_action.triggered.connect(self._open_system_settings)
        self.more_tools_button.setMenu(more_menu)
        advanced_grid.addWidget(self.more_tools_button, 1, 1)
        main_layout.addWidget(self.advanced_card)

        self._advanced_mode_widgets = [
            self.advanced_section_title,
            self.advanced_card,
        ]
        self._apply_experience_mode_ui()

        footer = QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        self.version_label = QtWidgets.QLabel("版本 {}".format(self.local_version))
        self.version_label.setStyleSheet("color:#8a8f95; font-size:10px;")
        footer.addWidget(self.version_label)
        main_layout.addLayout(footer)

        self.window.setMinimumSize(540, 440)
        self.window.resize(610, 545 if self.experience_mode == "advanced" else 445)
        self.window.exec()


    def show_mod_manager(self):
        """打开星黎模组管理中心。"""
        if self.experience_mode != "advanced":
            QtWidgets.QMessageBox.information(
                self.window if hasattr(self, "window") else None,
                "高级玩家功能",
                "模组管理中心仅对高级玩家模式开放。\n请先将功能模式切换为‘高级玩家’。",
            )
            return
        try:
            return mod_manager.show_mod_manager(
                self.window if hasattr(self, "window") else None,
                self.organizer,
            )
        except Exception as e:
            logger.exception("打开模组管理中心失败")
            QtWidgets.QMessageBox.critical(
                self.window if hasattr(self, "window") else None,
                "模组管理中心",
                "无法打开模组管理中心：\n{}".format(e),
            )

    def show_lod_pipeline(self):
        """打开 Grass Cache / xLODGen / TexGen / DynDOLOD 自动化流水线。"""
        if self.experience_mode != "advanced":
            QtWidgets.QMessageBox.information(
                self.window if hasattr(self, "window") else None,
                "高级玩家功能",
                "LOD 自动化流水线仅对高级玩家模式开放。\n请先将功能模式切换为“高级玩家”。",
            )
            return
        try:
            lod_pipeline.show_lod_pipeline(
                self.window if hasattr(self, "window") else None,
                self.organizer,
                self.plugin_path,
            )
        except Exception as e:
            logger.exception("打开 LOD 自动化流水线失败")
            QtWidgets.QMessageBox.critical(
                self.window if hasattr(self, "window") else None,
                "LOD 自动化",
                "无法打开 LOD 自动化流水线：\n{}".format(e),
            )

    def show_mod_compressor(self):
        """打开集成的模组压缩工具。压缩功能仅允许高级玩家模式使用。"""
        if self.experience_mode != "advanced":
            QtWidgets.QMessageBox.information(
                self.window if hasattr(self, "window") else None,
                "高级玩家功能",
                "模组压缩功能仅对高级玩家模式开放。\n请先将功能模式切换为“高级玩家”。",
            )
            return
        try:
            mod_compressor.show_mod_compressor(
                self.window if hasattr(self, "window") else None,
                self.organizer,
                experience_mode=self.experience_mode,
            )
        except Exception as e:
            logger.exception("打开模组压缩工具失败")
            QtWidgets.QMessageBox.critical(
                self.window if hasattr(self, "window") else None,
                "模组压缩工具",
                f"无法打开模组压缩工具：\n{e}",
            )

    def launch_pgpatcher(self):
        """预检并通过当前 MO2 VFS 启动 PGPatcher。"""
        parent = self.window if hasattr(self, "window") else None
        try:
            status = pgpatcher_adapter.inspect(self.organizer)
        except Exception as exc:
            logger.exception("PGPatcher 预检失败")
            QtWidgets.QMessageBox.critical(parent, "PGPatcher", "PGPatcher 预检失败：\n{}".format(exc))
            return

        exe = str(status.get("exe", "") or "")
        if not exe:
            box = QtWidgets.QMessageBox(parent)
            box.setWindowTitle("未找到 PGPatcher")
            box.setIcon(QtWidgets.QMessageBox.Icon.Warning if hasattr(QtWidgets.QMessageBox, "Icon") else QtWidgets.QMessageBox.Warning)
            box.setText("未检测到 PGPatcher.exe。\n\nTruePBR 材质需要先安装 PGPatcher，然后从 MO2 环境运行。")
            nexus_button = box.addButton("打开 Nexus 官方页", QtWidgets.QMessageBox.ButtonRole.ActionRole if hasattr(QtWidgets.QMessageBox, "ButtonRole") else QtWidgets.QMessageBox.ActionRole)
            github_button = box.addButton("打开 GitHub", QtWidgets.QMessageBox.ButtonRole.ActionRole if hasattr(QtWidgets.QMessageBox, "ButtonRole") else QtWidgets.QMessageBox.ActionRole)
            box.addButton("关闭", QtWidgets.QMessageBox.ButtonRole.RejectRole if hasattr(QtWidgets.QMessageBox, "ButtonRole") else QtWidgets.QMessageBox.RejectRole)
            box.exec() if hasattr(box, "exec") else box.exec_()
            clicked = box.clickedButton()
            if clicked is nexus_button:
                webbrowser.open(pgpatcher_adapter.OFFICIAL_NEXUS_URL)
            elif clicked is github_button:
                webbrowser.open(pgpatcher_adapter.OFFICIAL_GITHUB_URL)
            return

        if not bool(status.get("instance_ini_ok", False)):
            QtWidgets.QMessageBox.warning(
                parent,
                "PGPatcher：MO2 实例路径异常",
                "当前 MO2 基础目录下没有找到 ModOrganizer.ini：\n{}\n\n"
                "PGPatcher 的 MO2 Instance Location 必须指向包含 ModOrganizer.ini 的实例目录。".format(
                    status.get("instance_path", "")
                ),
            )
            return

        dyndolod = list(status.get("active_dyndolod", []) or [])
        if dyndolod:
            QtWidgets.QMessageBox.warning(
                parent,
                "PGPatcher：请先禁用 DynDOLOD 输出",
                "检测到以下 DynDOLOD 输出仍处于启用状态：\n- {}\n\n"
                "PGPatcher 应在 TexGen / DynDOLOD 之前运行。请先禁用这些输出，再重新启动 PGPatcher。".format(
                    "\n- ".join(dyndolod)
                ),
            )
            return

        active_outputs = list(status.get("active_outputs", []) or [])
        if active_outputs:
            yes = QtWidgets.QMessageBox.StandardButton.Yes if hasattr(QtWidgets.QMessageBox, "StandardButton") else QtWidgets.QMessageBox.Yes
            no = QtWidgets.QMessageBox.StandardButton.No if hasattr(QtWidgets.QMessageBox, "StandardButton") else QtWidgets.QMessageBox.No
            reply = QtWidgets.QMessageBox.question(
                parent,
                "PGPatcher：需要禁用旧输出",
                "PGPatcher 官方流程要求运行前禁用旧的输出 MOD。\n\n"
                "当前启用：\n- {}\n\n"
                "是否由星黎小助手先禁用它们，再启动 PGPatcher？".format("\n- ".join(active_outputs)),
                yes | no,
            )
            if reply != yes:
                return
            ok, failures = pgpatcher_adapter.disable_output_mods(self.organizer, active_outputs)
            if not ok:
                QtWidgets.QMessageBox.critical(
                    parent,
                    "PGPatcher",
                    "无法禁用以下输出 MOD：\n- {}\n\n请手动禁用后再试。".format("\n- ".join(failures)),
                )
                return

        texgen = list(status.get("active_texgen", []) or [])
        details = [
            "将通过当前 MO2 VFS 启动 PGPatcher。",
            "",
            "PGPatcher：{}".format(exe),
            "MO2 Instance Location：{}".format(status.get("instance_path", "")),
            "推荐 Output Directory：{}".format(status.get("recommended_output", "")),
            "",
            "首次配置时选择 Mod Manager = MO2，Output 建议直接指向独立 PGPatcher_Output MOD，并关闭 ZIP 输出。",
            "补丁完成后回到 MO2 启用 PGPatcher_Output；随后再运行 TexGen / DynDOLOD。",
            "TruePBR 需要 Community Shaders 的 PBR 功能。",
            "Skyrim 1.5.97–1.6.659 还需要 Backported Extended ESL Support (BEES)。",
        ]
        if texgen:
            details.extend(["", "注意：当前 TexGen 输出已启用。PGPatcher 运行后建议重新生成 TexGen / DynDOLOD。"] )
        QtWidgets.QMessageBox.information(parent, "PGPatcher 适配", "\n".join(details))
        try:
            pgpatcher_adapter.launch(self.organizer, exe)
        except Exception as exc:
            logger.exception("启动 PGPatcher 失败")
            QtWidgets.QMessageBox.critical(parent, "PGPatcher", "启动 PGPatcher 失败：\n{}".format(exc))

    def show_user_assistant(self):
        """打开用户助手：启动游戏、自检环境、修复 SKSE 路径。"""
        try:
            user_assistant.show_user_assistant(self)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self.window if hasattr(self, "window") else None,
                "用户助手打开失败",
                "打开星黎用户助手失败: {}".format(str(e))
            )


    def show_auto_updater(self):
        """打开自动更新窗口。"""
        try:
            auto_updater.show_auto_updater(self)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self.window if hasattr(self, "window") else None,
                "自动更新打开失败",
                "打开星黎自动更新失败: {}".format(str(e))
            )

    def _get_game_path_from_registry(self):
        """从注册表获取游戏安装路径"""
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition"
            )
            return winreg.QueryValueEx(key, "Installed Path")[0]
        except Exception:
            return None

    def _prompt_for_game_path(self) -> Optional[str]:
        """弹出文件夹选择对话框让用户手动选择游戏路径，进行初步验证并返回有效目录或 None"""
        QtWidgets.QMessageBox.information(
            None,
            "未找到游戏路径",
            "未能自动找到有效的游戏安装路径。\n请手动选择您的《上古卷轴V：天际特别版》游戏根目录。"
        )
        selected_path = QtWidgets.QFileDialog.getExistingDirectory(
            None,
            "请选择游戏根目录 (包含 SkyrimSE.exe 或 skse64_loader.exe 的文件夹)",
            os.path.expanduser("~")
        )

        if selected_path:
            # 使用 _validate_game_path 进行检查，简化逻辑
            if self._validate_game_path(selected_path):
                 # 验证函数内部会打印成功信息，这里可以简单确认
                 QtWidgets.QMessageBox.information(
                     None,
                     "路径确认",
                     f"已选择有效路径：{selected_path}"
                 )
                 return selected_path # 返回目录路径
            else:
                 # _validate_game_path 失败，说明未找到 exe
                 QtWidgets.QMessageBox.critical(
                     None,
                     "错误",
                     f"选择的目录 {selected_path} 似乎不是有效的游戏根目录（未找到 SkyrimSE.exe 或 skse64_loader.exe）。"
                 )
                 return None
        else:
            # 用户取消了选择
            QtWidgets.QMessageBox.warning(
                None,
                "操作取消",
                "用户取消了路径选择。"
            )
            return None
    # --- 新增：手动设置游戏路径的处理函数 ---
    def _manually_set_game_path(self) -> Optional[str]: # 添加返回类型提示
        """
        响应“设置游戏路径”按钮点击，
        提示用户选择路径，验证并保存。
        """
        print("用户请求手动设置游戏路径...")
        selected_path = self._prompt_for_game_path() # 返回有效目录或 None

        if selected_path:
            # _prompt_for_game_path 内部已经验证过，这里直接使用
            print(f"用户选择了有效路径: {selected_path}")
            # 更新实例变量
            self.game_path = selected_path
            # 写入 INI 文件
            self._write_game_path_to_ini(selected_path)
            # 提示用户成功
            QtWidgets.QMessageBox.information(
                None,
                "路径已更新",
                f"游戏路径已成功设置为:\n{selected_path}\n并已保存。"
            )
        else:
            print("用户取消了手动设置或选择了无效路径。")

    def _manually_set_game_path_and_refresh_ui(self):
        """调用手动设置路径，如果成功则更新 UI 或提示重启。如果已有路径，先询问用户。"""
        print("尝试手动设置游戏路径并刷新 UI...")

        proceed_with_manual_set = True # 默认继续

        # 检查是否已存在有效路径
        if self.game_path and self._validate_game_path(self.game_path):
            print(f"当前已设置有效路径: {self.game_path}。询问用户是否更改。")
            reply = QtWidgets.QMessageBox.question(
                self.window, # 父窗口
                "确认更改路径",
                f"当前已设置游戏路径:\n{self.game_path}\n\n您确定要重新设置吗？",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No # 默认按钮为 No
            )
            if reply == QtWidgets.QMessageBox.StandardButton.No:
                print("用户取消了更改路径。")
                proceed_with_manual_set = False # 用户取消，不继续

        if proceed_with_manual_set:
            # 调用手动设置逻辑
            new_path = self._manually_set_game_path() # 它现在返回新路径或 None
            if new_path:
                print(f"手动设置成功，新路径: {new_path}")
                self.game_path = new_path  # 确保更新实例变量
                # 更新 UI 上的标签
                if self.game_path_label:
                    self.game_path_label.setText(new_path)
                    self.game_path_label.setToolTip(new_path)
                    # 简单的做法是提示用户重新打开窗口以应用所有更改
                    if self.window:
                        QtWidgets.QMessageBox.information(self.window, "路径已更新", f"游戏路径已设置为:\n{self.game_path}\n\n建议重新打开小助手窗口以确保所有功能正常。")
                        # 理论上可以动态替换按钮，但关闭重开更简单可靠
                        self.window.close() # 关闭窗口让用户重开
                else:
                     print("错误：无法找到 game_path_label 来更新 UI。")
            else:
                # 不显示警告消息框，因为用户可能已经成功修改路径但取消了后续操作
                print("手动设置未完成或失败，UI 未更新。")
        else:
            # 用户在确认对话框中选择了 "No"
            print("操作已取消，未进行路径设置。")

    def open_tutorial(self): # 保持这一行并确保缩进正确
        try:
            self.show_tutorial_window()  # 修改为正确的方法名称
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                None,
                "错误",
                "打开教程失败: {}".format(str(e))
            )
    def show_tutorial_window(self):
        # 创建一个窗口，用于显示教程分类和教程列表
        tutorial_window = QtWidgets.QDialog()
        tutorial_window.setWindowTitle("星黎整合包教程中心")
        tutorial_window.setWindowFlags(tutorial_window.windowFlags() | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)

        # 创建主布局
        main_layout = QtWidgets.QVBoxLayout()
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        # 添加标题
        title_label = QtWidgets.QLabel("教程资源中心")
        title_font = QtGui.QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)
        
        # 添加说明文本
        description_label = QtWidgets.QLabel("内置教程与当前小助手同步；外部视频仅作为补充参考")
        description_label.setStyleSheet("color: #606060;")
        description_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(description_label)
        
        # 添加分隔线
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        main_layout.addWidget(line)

        # 创建水平布局用于分类选择
        category_layout = QtWidgets.QHBoxLayout()
        
        # 分类选择
        category_label = QtWidgets.QLabel("选择分类:")
        category_label.setStyleSheet("font-weight: bold;")
        category_layout.addWidget(category_label)

        category_combo = QtWidgets.QComboBox()
        category_combo.setStyleSheet("""
            QComboBox {
                border: 1px solid #c0c0c0;
                border-radius: 3px;
                padding: 5px;
                min-width: 200px;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 20px;
                border-left: 1px solid #c0c0c0;
            }
        """)
        category_combo.addItems(TUTORIAL_CATEGORIES.keys())
        category_layout.addWidget(category_combo)
        
        # 添加搜索框
        search_layout = QtWidgets.QHBoxLayout()
        search_label = QtWidgets.QLabel("搜索:")
        search_label.setStyleSheet("font-weight: bold;")
        search_layout.addWidget(search_label)
        
        search_input = QtWidgets.QLineEdit()
        search_input.setPlaceholderText("输入关键词搜索教程...")
        search_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #c0c0c0;
                border-radius: 3px;
                padding: 5px;
            }
        """)
        search_layout.addWidget(search_input)
        
        # 将分类和搜索布局添加到主布局
        main_layout.addLayout(category_layout)
        main_layout.addLayout(search_layout)

        # 教程列表
        tutorial_list = QtWidgets.QListWidget()
        tutorial_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #c0c0c0;
                border-radius: 3px;
                padding: 5px;
                background-color: #ffffff;
                alternate-background-color: #f7f7f7;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #e0e0e0;
            }
            QListWidget::item:hover {
                background-color: #e6f0ff;
            }
            QListWidget::item:selected {
                background-color: #cce0ff;
                color: black;
            }
        """)
        tutorial_list.setAlternatingRowColors(True)
        tutorial_list.itemDoubleClicked.connect(lambda item: self.open_tutorial_entry(item.text()))
        main_layout.addWidget(tutorial_list)
        
        # 添加提示标签
        hint_label = QtWidgets.QLabel("提示：双击内置教程直接阅读；外部教程会打开浏览器")
        hint_label.setStyleSheet("color: #808080; font-style: italic;")
        hint_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(hint_label)
        
        # 添加按钮布局
        button_layout = QtWidgets.QHBoxLayout()
        
        # 打开按钮
        open_button = QtWidgets.QPushButton("打开选中教程")
        open_button.setStyleSheet("""
            QPushButton {
                background-color: #4a86e8;
                color: white;
                border: none;
                border-radius: 3px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #3a76d8;
            }
            QPushButton:pressed {
                background-color: #2a66c8;
            }
        """)
        open_button.clicked.connect(lambda: self.open_tutorial_entry(tutorial_list.currentItem().text()) if tutorial_list.currentItem() else None)
        button_layout.addWidget(open_button)
        
        # 关闭按钮
        close_button = QtWidgets.QPushButton("关闭")
        close_button.setStyleSheet("""
            QPushButton {
                background-color: #f0f0f0;
                border: 1px solid #c0c0c0;
                border-radius: 3px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
            QPushButton:pressed {
                background-color: #d0d0d0;
            }
        """)
        close_button.clicked.connect(tutorial_window.close)
        button_layout.addWidget(close_button)
        
        main_layout.addLayout(button_layout)

        # 更新教程列表函数
        def update_tutorials():
            selected_category = category_combo.currentText()
            search_text = search_input.text().lower()
            tutorials = TUTORIAL_CATEGORIES[selected_category]
            tutorial_list.clear()
            
            for tutorial in tutorials:
                # 如果搜索框有内容，则过滤
                if search_text and search_text not in tutorial["name"].lower():
                    continue
                tutorial_list.addItem(tutorial["name"])

        # 连接信号
        category_combo.currentIndexChanged.connect(update_tutorials)
        search_input.textChanged.connect(update_tutorials)

        # 初始更新
        update_tutorials()

        tutorial_window.setLayout(main_layout)
        tutorial_window.resize(500, 500)
        tutorial_window.exec()
    def open_tutorial_entry(self, tutorial_name):
        for category in TUTORIAL_CATEGORIES.values():
            for tutorial in category:
                if tutorial.get("name") != tutorial_name:
                    continue
                content = str(tutorial.get("content", "") or "").strip()
                if content:
                    dialog = QtWidgets.QDialog(self.window if hasattr(self, "window") else None)
                    dialog.setWindowTitle(tutorial_name)
                    dialog.resize(620, 430)
                    layout = QtWidgets.QVBoxLayout(dialog)
                    title = QtWidgets.QLabel(tutorial_name)
                    font = QtGui.QFont()
                    font.setPointSize(12)
                    font.setBold(True)
                    title.setFont(font)
                    layout.addWidget(title)
                    text = QtWidgets.QPlainTextEdit()
                    text.setReadOnly(True)
                    text.setPlainText(content)
                    layout.addWidget(text, 1)
                    close = QtWidgets.QPushButton("关闭")
                    close.clicked.connect(dialog.accept)
                    layout.addWidget(close)
                    dialog.exec()
                    return
                url = str(tutorial.get("url", "") or "").strip()
                if url:
                    webbrowser.open(url)
                return

    def open_tutorial_url(self, tutorial_name):
        """兼容旧入口。"""
        return self.open_tutorial_entry(tutorial_name)

    def update_plugin(self):
        """兼容旧入口：统一打开新的安全更新窗口。"""
        return self.show_auto_updater()
    
    def manage_enb(self):
        # --- 新增：检查游戏路径是否已设置 ---
        if not self.game_path:
            QtWidgets.QMessageBox.critical(
                None,
                "错误：未找到游戏路径",
                "未能找到有效的游戏安装路径。\n"
                "请确保在插件初始化时已成功找到或选择了游戏目录。\n"
                "ENB 管理功能无法使用。"
            )
            return # 阻止窗口打开
        # --- 结束新增 ---

        # 创建 ENB 管理窗口
        enb_window = QtWidgets.QDialog()
        enb_window.setWindowTitle("ENB 管理")
        enb_window.setWindowFlags(enb_window.windowFlags() | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)

        # 创建主布局
        main_layout = QtWidgets.QVBoxLayout()
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        # 添加标题标签
        title_label = QtWidgets.QLabel("ENB 预设管理")
        title_font = QtGui.QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)
        
        # 添加说明文本
        info_text = QtWidgets.QLabel("选择一个ENB预设，然后点击「导入ENB」按钮将其应用到游戏中。")
        info_text.setWordWrap(True)
        info_text.setStyleSheet("color: #666; margin-bottom: 10px;")
        main_layout.addWidget(info_text)

        # 创建列表和按钮的水平布局
        content_layout = QtWidgets.QHBoxLayout()
        
        # 左侧：ENB列表部分
        list_layout = QtWidgets.QVBoxLayout()
        
        # 初始化实例变量 self.enb_list
        enb_list_label = QtWidgets.QLabel("可用ENB预设:")
        enb_list_label.setStyleSheet("font-weight: bold;")
        list_layout.addWidget(enb_list_label)
        
        self.enb_list = QtWidgets.QListWidget()
        self.enb_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ccc;
                border-radius: 4px;
                background-color: #f8f8f8;
                padding: 5px;
            }
            QListWidget::item {
                padding: 5px;
                border-bottom: 1px solid #eee;
            }
            QListWidget::item:selected {
                background-color: #4a86e8;
                color: white;
            }
        """)
        list_layout.addWidget(self.enb_list)
        
        content_layout.addLayout(list_layout)
        
        # 右侧：按钮部分
        button_layout = QtWidgets.QVBoxLayout()
        button_layout.setAlignment(Qt.AlignTop)
        button_layout.setSpacing(10)
        
        # 按钮样式
        button_style = """
        QPushButton {
            background-color: #4a86e8;
            color: white;
            border: none;
            padding: 10px;
            border-radius: 5px;
            font-size: 12px;
            min-height: 40px;
            min-width: 120px;
        }
        QPushButton:hover {
            background-color: #3a76d8;
        }
        QPushButton:pressed {
            background-color: #2a66c8;
        }
        """
        
        # 安装、导入和禁用 ENB 按钮
        install_button = QtWidgets.QPushButton("安装 ENB")
        install_button.setStyleSheet(button_style.replace("#4a86e8", "#4CAF50").replace("#3a76d8", "#45a049").replace("#2a66c8", "#367c39")) # Green color
        install_button.setIcon(QtGui.QIcon.fromTheme("list-add"))
        install_button.setToolTip("从文件夹安装新的ENB预设") 

        start_button = QtWidgets.QPushButton("应用 ENB")
        start_button.setStyleSheet(button_style)
        start_button.setIcon(QtGui.QIcon.fromTheme("document-import"))
        start_button.setToolTip("将选中的ENB预设应用到游戏")

        stop_button = QtWidgets.QPushButton("禁用 ENB")
        stop_button.setStyleSheet(button_style.replace("#4a86e8", "#e84a4a").replace("#3a76d8", "#d83a3a").replace("#2a66c8", "#c82a2a")) # Red color
        stop_button.setIcon(QtGui.QIcon.fromTheme("process-stop"))
        stop_button.setToolTip("移除当前应用的ENB文件")

        button_layout.addWidget(install_button) 
        button_layout.addWidget(start_button)
        button_layout.addWidget(stop_button)
        button_layout.addStretch()
        
        content_layout.addLayout(button_layout)
        main_layout.addLayout(content_layout)
        
        # 添加状态标签
        self.enb_status_label = QtWidgets.QLabel("")
        self.enb_status_label.setStyleSheet("color: #666; font-style: italic;")
        self.enb_status_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self.enb_status_label)

        # --- 移除旧的路径查找逻辑 (Lines 994-1034) ---
        # (代码已被删除)
        # --- 结束移除 ---

        # 检查游戏路径是否存在
        if not os.path.exists(self.game_path):
            print(f"游戏路径 {self.game_path} 不存在，尝试提示用户选择。") # 添加调试信息
            # 尝试让用户手动选择路径
            # _prompt_for_game_path 返回 skse_loader.exe 的路径或 None
            skse_loader_path = self._prompt_for_game_path()
            if skse_loader_path and os.path.exists(skse_loader_path):
                # 用户选择了有效路径，更新 self.game_path 为游戏根目录
                self.game_path = os.path.dirname(skse_loader_path)
                print(f"用户选择了新的游戏路径: {self.game_path}") # 添加调试信息
            else:
                # 用户取消或选择了无效路径
                print("用户未选择有效的游戏路径，无法继续 ENB 管理。") # 添加调试信息
                # 直接返回，因为 _prompt_for_game_path 内部已经提示过错误
                return # 无法获取有效路径，退出 ENB 管理

        # 检查 ENB 备份路径是否存在
        self.enb_backup_path = os.path.join(self.game_path, "ENB备份")
        if not os.path.exists(self.enb_backup_path):
            try:
                os.makedirs(self.enb_backup_path)
                QtWidgets.QMessageBox.information(
                    None,
                    "提示",
                    f"ENB备份文件夹不存在，已自动创建:\n{self.enb_backup_path}"
                )
            except OSError as e:
                QtWidgets.QMessageBox.critical(
                    None,
                    "错误",
                    f"创建ENB备份文件夹失败: {e}"
                )
                return

        # 填充 ENB 列表 (调用新的辅助方法)
        self.refresh_enb_list()

        # 定义需要移动的文件和文件夹列表
        self.enb_files_and_folders = [
            "enbseries",
            "reshade-shaders",
            "d3d11.dll",
            "d3dcompiler_46e.dll",
            "enblocal.ini",
            "enbseries.ini",
            "dxgi.dll"
        ]

        # 连接按钮信号
        install_button.clicked.connect(self.install_enb) # 连接安装按钮
        start_button.clicked.connect(self.start_enb)
        stop_button.clicked.connect(self.stop_enb)
        
        # 设置窗口布局
        enb_window.setLayout(main_layout)
        enb_window.setMinimumSize(500, 400)
        enb_window.exec()
        # 启动 ENB 功能
    def start_enb(self):
        try:
            # 检查 ENB 列表是否初始化
            if not hasattr(self, 'enb_list') or self.enb_list is None:
                QtWidgets.QMessageBox.critical(
                    None,
                    "错误",
                    "ENB 列表未初始化，请先打开 ENB 管理窗口。"
                )
                return

            selected_item = self.enb_list.currentItem()
            if not selected_item:
                QtWidgets.QMessageBox.warning(
                    None,
                    "警告",
                    "请先选择一个 ENB！"
                )
                return

            enb_name = selected_item.text()
            enb_source_path = os.path.join(self.enb_backup_path, enb_name)
            game_path = self.game_path

            # 验证源路径是否存在
            if not os.path.exists(enb_source_path):
                QtWidgets.QMessageBox.critical(
                    None,
                    "错误",
                    f"ENB 源文件夹不存在: {enb_source_path}"
                )
                return

            # 步骤 1: 删除游戏目录中的旧 ENB 文件
            for item in self.enb_files_and_folders:
                target_path = os.path.join(game_path, item)
                if os.path.exists(target_path):
                    try:
                        if os.path.isfile(target_path):
                            os.remove(target_path)
                        elif os.path.isdir(target_path):
                            shutil.rmtree(target_path)
                    except Exception as e:
                        QtWidgets.QMessageBox.critical(
                            None,
                            "错误",
                            f"删除文件失败: {target_path}\n错误信息: {str(e)}"
                        )
                        return

            # 步骤 2: 复制新 ENB 到游戏目录
            for item in self.enb_files_and_folders:
                source_item = os.path.join(enb_source_path, item)
                target_item = os.path.join(game_path, item)

                try:
                    if os.path.isfile(source_item):
                        shutil.copy2(source_item, target_item)  # 复制文件并保留元数据
                    elif os.path.isdir(source_item):
                        shutil.copytree(
                            source_item,
                            target_item,
                            dirs_exist_ok=True  # 允许覆盖目录
                        )
                except Exception as e:
                    QtWidgets.QMessageBox.critical(
                        None,
                        "错误",
                        f"复制文件失败: {source_item} → {target_item}\n错误信息: {str(e)}"
                    )
                    return

            QtWidgets.QMessageBox.information(
                None,
                "成功",
                f"ENB [{enb_name}] 已成功部署到游戏目录！"
            )

        except Exception as e:
            QtWidgets.QMessageBox.critical(
                None,
                "未知错误",
                f"操作失败: {str(e)}"
            )


    # 关闭 ENB 功能
    def stop_enb(self):
        game_path = self.game_path
        for item in self.enb_files_and_folders:
                target_path = os.path.join(game_path, item)
                if os.path.exists(target_path):
                    try:
                        if os.path.isfile(target_path):
                            os.remove(target_path)
                        elif os.path.isdir(target_path):
                            shutil.rmtree(target_path)
                    except Exception as e:
                        QtWidgets.QMessageBox.critical(
                            None,
                            "错误",
                            f"删除文件失败: {target_path}\n错误信息: {str(e)}"
                        )
                        return
        QtWidgets.QMessageBox.information(
        None,
        "成功",
        f"ENB已成功禁用！"
        )
    # 新增：安装 ENB 的方法
    def install_enb(self):
        try:
            # 1. 选择源文件夹
            source_dir = QtWidgets.QFileDialog.getExistingDirectory(
                None,
                "选择包含 ENB 预设文件的文件夹",
                self.game_path 
            )
            if not source_dir:
                return # 用户取消

            # 2. 获取预设名称
            preset_name, ok = QtWidgets.QInputDialog.getText(
                None,
                "输入 ENB 预设名称",
                "为这个 ENB 预设命名:",
                QtWidgets.QLineEdit.Normal,
                os.path.basename(source_dir) # 建议使用源文件夹名称作为默认值
            )
            if not ok or not preset_name.strip():
                QtWidgets.QMessageBox.warning(None, "取消", "未提供有效的预设名称。")
                return

            preset_name = preset_name.strip()
            target_path = os.path.join(self.enb_backup_path, preset_name)

            # 3. 检查是否已存在并处理覆盖
            if os.path.exists(target_path):
                # 使用 PyQt6/PyQt5 兼容的方式引用 StandardButton
                try:
                    # 尝试 PyQt6
                    msg_box_buttons = QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
                    no_button = QtWidgets.QMessageBox.StandardButton.No
                except AttributeError:
                    # 回退到 PyQt5
                    msg_box_buttons = QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
                    no_button = QtWidgets.QMessageBox.No

                reply = QtWidgets.QMessageBox.question(
                    None,
                    "确认覆盖",
                    f"名为 '{preset_name}' 的 ENB 预设已存在。\n是否要覆盖它？",
                    msg_box_buttons,
                    no_button
                )

                # 同样需要兼容 PyQt6/PyQt5 的返回值比较
                try:
                    # 尝试 PyQt6
                    should_continue = (reply == QtWidgets.QMessageBox.StandardButton.Yes)
                except AttributeError:
                     # 回退到 PyQt5
                    should_continue = (reply == QtWidgets.QMessageBox.Yes)

                if not should_continue:
                    QtWidgets.QMessageBox.information(None, "操作取消", "安装已取消。")
                    return
                else:
                    try:
                        shutil.rmtree(target_path) # 覆盖前先删除旧的
                    except Exception as e:
                         QtWidgets.QMessageBox.critical(None, "错误", f"无法删除旧的预设文件夹: {target_path}\n错误: {str(e)}")
                         return

            # 4. 创建目录并复制文件
            try:
                shutil.copytree(source_dir, target_path)

                # 5. 刷新列表
                self.refresh_enb_list()
                QtWidgets.QMessageBox.information(None, "成功", f"ENB 预设 '{preset_name}' 已成功安装！")

            except Exception as e:
                QtWidgets.QMessageBox.critical(None, "安装失败", f"复制 ENB 文件时出错: {str(e)}")
                # 清理可能部分创建的文件夹
                if os.path.exists(target_path):
                    try:
                        shutil.rmtree(target_path)
                    except Exception:
                        pass # 忽略清理错误

        except Exception as e:
            QtWidgets.QMessageBox.critical(None, "未知错误", f"安装 ENB 时发生错误: {str(e)}")


    # 新增：刷新 ENB 列表的方法
    def refresh_enb_list(self):
        # 检查 ENB 列表控件和备份路径是否存在
        if not hasattr(self, 'enb_list') or self.enb_list is None:
            print("错误: ENB 列表控件未初始化。") # 或者记录日志
            return
        if not hasattr(self, 'enb_backup_path') or not self.enb_backup_path or not os.path.exists(self.enb_backup_path):
             if hasattr(self, 'enb_status_label'):
                 self.enb_status_label.setText("错误: ENB备份路径无效或不存在。")
             print(f"错误: ENB 备份路径无效或不存在: {getattr(self, 'enb_backup_path', '未设置')}")
             return # 如果路径问题无法解决，则退出

        self.enb_list.clear()
        try:
            presets = [d for d in os.listdir(self.enb_backup_path) if os.path.isdir(os.path.join(self.enb_backup_path, d))]
            if presets:
                self.enb_list.addItems(presets)
                if hasattr(self, 'enb_status_label'):
                    self.enb_status_label.setText(f"找到 {len(presets)} 个 ENB 预设。")
            else:
                if hasattr(self, 'enb_status_label'):
                    self.enb_status_label.setText("在 ENB备份 文件夹中未找到任何预设。")

        except Exception as e:
            QtWidgets.QMessageBox.critical(None, "列表刷新错误", f"刷新 ENB 列表时出错: {str(e)}")
            if hasattr(self, 'enb_status_label'):
                self.enb_status_label.setText("刷新列表时出错。")


    def show_resolution_settings(self):
        try:
            # 构建配置文件路径
            config_path = os.path.join(
                self.organizer.modsPath(),
                "显示修复-SSE Display Tweaks",
                "SKSE",
                "Plugins",
                "SSEDisplayTweaks.ini"
            )

            # 预处理INI文件（关键修复）
            if os.path.exists(config_path):
                # 使用utf-8-sig自动处理BOM
                with open(config_path, 'r', encoding='utf-8-sig') as file:
                    content = file.read()
                    
                    # 强制转换为标准INI格式
                    content = content.lstrip('\ufeff')  # 确保移除BOM
                    content = content.replace('\r\n', '\n').replace('\r', '\n')  # 统一换行符
                    
                    # 确保有[Render]节
                    if '[Render]' not in content:
                        content = '[Render]\n' + content
                        
                    # 临时文件路径
                    temp_path = config_path + ".tmp"
                    with open(temp_path, 'w', encoding='utf-8') as cleaned_file:
                        cleaned_file.write(content)
                        
                # 替换原文件
                shutil.move(temp_path, config_path)
            
            # 读取配置（使用预处理后的文件）
            config = configparser.ConfigParser()
            try:
                config.read(config_path, encoding='utf-8')
            except Exception as e:
                QtWidgets.QMessageBox.critical(None, "配置文件错误",
                    f"无法解析配置文件:\n{str(e)}\n"
                    "请确保SSEDisplayTweaks.ini格式正确！")
                return
                
            # 创建分辨率设置窗口
            resolution_window = QtWidgets.QDialog()
            resolution_window.setWindowTitle("分辨率设置")
            resolution_window.setWindowFlags(resolution_window.windowFlags() | Qt.WindowCloseButtonHint)
            
            # 创建主布局
            main_layout = QtWidgets.QVBoxLayout()
            main_layout.setSpacing(10)
            main_layout.setContentsMargins(15, 15, 15, 15)
            
            # 添加标题标签
            title_label = QtWidgets.QLabel("游戏分辨率设置")
            title_font = QtGui.QFont()
            title_font.setPointSize(14)
            title_font.setBold(True)
            title_label.setFont(title_font)
            title_label.setAlignment(Qt.AlignCenter)
            main_layout.addWidget(title_label)
            
            # 添加说明文本
            info_text = QtWidgets.QLabel("在这里可以设置游戏的分辨率和窗口模式，设置将在重启游戏后生效。")
            info_text.setWordWrap(True)
            info_text.setStyleSheet("color: #666; margin-bottom: 10px;")
            main_layout.addWidget(info_text)
            
            # 创建表单布局
            form_layout = QtWidgets.QFormLayout()
            form_layout.setVerticalSpacing(10)
            form_layout.setHorizontalSpacing(15)
            
            # 当前分辨率显示
            try:
                current_res = config.get("Render", "Resolution")
            except (configparser.NoOptionError, configparser.NoSectionError):
                current_res = "未设置"
                
            current_res_value = QtWidgets.QLabel(current_res)
            current_res_value.setStyleSheet("font-weight: bold; color: #4a86e8;")
            form_layout.addRow("当前分辨率:", current_res_value)
            
            # 分辨率输入框
            self.res_input = QtWidgets.QLineEdit()
            self.res_input.setPlaceholderText("例如：1920x1080")
            self.res_input.setStyleSheet("""
                QLineEdit {
                    padding: 8px;
                    border: 1px solid #ccc;
                    border-radius: 4px;
                    background-color: #f8f8f8;
                }
                QLineEdit:focus {
                    border: 1px solid #4a86e8;
                }
            """)
            form_layout.addRow("新分辨率:", self.res_input)
            
            # 窗口模式选择
            mode_group = QtWidgets.QGroupBox("窗口模式")
            mode_group.setStyleSheet("""
                QGroupBox {
                    font-weight: bold;
                    border: 1px solid #ccc;
                    border-radius: 4px;
                    margin-top: 10px;
                    padding-top: 10px;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 10px;
                    padding: 0 5px;
                }
            """)
            
            mode_layout = QtWidgets.QVBoxLayout()
            
            self.fullscreen_check = QtWidgets.QCheckBox("全屏模式")
            self.borderless_check = QtWidgets.QCheckBox("无边框窗口")
            
            checkbox_style = """
                QCheckBox {
                    spacing: 8px;
                }
                QCheckBox::indicator {
                    width: 18px;
                    height: 18px;
                }
                QCheckBox::indicator:unchecked {
                    border: 1px solid #ccc;
                    background-color: white;
                    border-radius: 3px;
                }
                QCheckBox::indicator:checked {
                    background-color: #4a86e8;
                    border: 1px solid #4a86e8;
                    border-radius: 3px;
                }
            """
            
            self.fullscreen_check.setStyleSheet(checkbox_style)
            self.borderless_check.setStyleSheet(checkbox_style)
            
            self.fullscreen_check.clicked.connect(self.update_window_mode)
            self.borderless_check.clicked.connect(self.update_window_mode)
            
            # 更新初始状态
            try:
                fullscreen = config.getboolean("Render", "Fullscreen", fallback=False)
                borderless = config.getboolean("Render", "Borderless", fallback=False)
                self.fullscreen_check.setChecked(fullscreen)
                self.borderless_check.setChecked(borderless)
            except Exception:
                pass
                
            mode_layout.addWidget(self.fullscreen_check)
            mode_layout.addWidget(self.borderless_check)
            mode_group.setLayout(mode_layout)
            
            # 自动分辨率MOD开关
            self.auto_res_check = QtWidgets.QCheckBox("启用自动分辨率调整")
            self.auto_res_check.setStyleSheet(checkbox_style)
            auto_res_enabled = self.mod_state.is_active(self.AUTO_RESOLUTION_MOD_NAME, default=False)
            self.auto_res_check.setChecked(auto_res_enabled)
            
            # 添加表单布局到主布局
            main_layout.addLayout(form_layout)
            main_layout.addWidget(mode_group)
            main_layout.addWidget(self.auto_res_check)
            
            # 添加按钮
            button_layout = QtWidgets.QHBoxLayout()
            button_layout.setSpacing(10)
            
            # 按钮样式
            button_style = """
            QPushButton {
                background-color: #4a86e8;
                color: white;
                border: none;
                padding: 10px;
                border-radius: 5px;
                font-size: 12px;
                min-height: 40px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #3a76d8;
            }
            QPushButton:pressed {
                background-color: #2a66c8;
            }
            """
            
            cancel_btn = QtWidgets.QPushButton("取消")
            cancel_btn.setStyleSheet(button_style.replace("#4a86e8", "#999").replace("#3a76d8", "#888").replace("#2a66c8", "#777"))
            cancel_btn.clicked.connect(resolution_window.reject)
            
            confirm_btn = QtWidgets.QPushButton("确认设置")
            confirm_btn.setStyleSheet(button_style)
            confirm_btn.clicked.connect(lambda: self.apply_resolution_settings(config_path) or resolution_window.accept())
            
            button_layout.addWidget(cancel_btn)
            button_layout.addWidget(confirm_btn)
            
            main_layout.addLayout(button_layout)
            
            # 设置窗口布局
            resolution_window.setLayout(main_layout)
            resolution_window.setMinimumWidth(350)
            resolution_window.exec()
        except FileNotFoundError:
            QtWidgets.QMessageBox.critical(None, "文件未找到",
                "SSEDisplayTweaks.ini文件不存在！\n"
                "请确认已安装SSE Display Tweaks模组")
            return
        except Exception as e:
            QtWidgets.QMessageBox.critical(None, "未知错误",
                f"初始化分辨率设置失败:\n{str(e)}")
            return

    def apply_resolution_settings(self, config_path):
        try:
            # 创建必要目录结构
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            
            # 读取时使用utf-8-sig
            config = configparser.ConfigParser()
            config.read(config_path, encoding='utf-8-sig')
            # 验证分辨率格式
            resolution = self.res_input.text().strip()
            if resolution:
                if not re.match(r"^\d+x\d+$", resolution):
                    QtWidgets.QMessageBox.warning(None, "格式错误", "分辨率格式不正确，请使用 宽x高 格式（例如：1920x1080）")
                    return

            # 确保文件存在并符合格式
            try:
                # 添加默认节头
                default_section = '[Render]\n'
                with open(config_path, 'r+', encoding='utf-8') as file:
                    content = file.read()
                    if not content.startswith('['):
                        content = default_section + content
                        file.seek(0)
                        file.write(content)
                        file.truncate()
            except FileNotFoundError:
                # 如果文件不存在，创建一个空文件
                with open(config_path, 'w', encoding='utf-8') as file:
                    file.write('[Render]\n')

            # 写入SSEDisplayTweaks.ini
            config = configparser.ConfigParser()
            config.read(config_path, encoding='utf-8')
            
            if not config.has_section("Render"):
                config.add_section("Render")
            
            # 获取当前配置
            current_res = config.get("Render", "Resolution", fallback="未设置")
            current_fullscreen = config.getboolean("Render", "Fullscreen", fallback=False)
            current_borderless = config.getboolean("Render", "Borderless", fallback=False)
            
            # 更新配置
            if resolution:
                config.set("Render", "Resolution", resolution)
            config.set("Render", "Fullscreen", str(self.fullscreen_check.isChecked()).lower())
            config.set("Render", "Borderless", str(self.borderless_check.isChecked()).lower())
            
            # 保存配置到原始路径
            with open(config_path, 'w', encoding='utf-8') as configfile:
                config.write(configfile)
            
            # 确定目标路径（overwrite目录）
            overwrite_path = os.path.join(self.organizer.overwritePath(), "SKSE", "Plugins", "SSEDisplayTweaks.ini")
            
            # 创建目标目录（如果不存在）
            os.makedirs(os.path.dirname(overwrite_path), exist_ok=True)
            
            try:
                # 备份目标文件
                if os.path.exists(overwrite_path):
                    shutil.copy(overwrite_path, overwrite_path + ".bak")
                
                # 复制到overwrite目录
                shutil.copy(config_path, overwrite_path)
                
                # 处理自动分辨率MOD
                try:
                    current_active = self.mod_state.is_active(self.AUTO_RESOLUTION_MOD_NAME, default=False)
                    target_active = bool(self.auto_res_check.isChecked())

                    if current_active != target_active:
                        result = self.mod_state.set_active([self.AUTO_RESOLUTION_MOD_NAME], target_active)
                        if result.failures:
                            raise RuntimeError("无法修改 MOD 状态：{}".format(", ".join(result.failures)))
                except Exception as e:
                    QtWidgets.QMessageBox.warning(None, "MOD状态错误", f"无法修改自动分辨率MOD状态: {str(e)}")
                
                QtWidgets.QMessageBox.information(
                    None,
                    "设置成功",
                    "分辨率设置已保存，部分修改需要重启游戏生效！"
                )
            except Exception as e:
                QtWidgets.QMessageBox.critical(None, "写入失败", f"无法保存设置: {str(e)}")
            with open(config_path, 'w', encoding='utf-8') as configfile:
                config.write(configfile)
                
        except Exception as e:
            QtWidgets.QMessageBox.critical(None, "保存失败",
                f"无法保存设置:\n{str(e)}\n"
                "请检查文件权限和防病毒软件设置！")
            return

    def update_window_mode(self):
        pass # 添加 pass 语句以修复空函数体错误
    # +++ 添加版本比较函数 (带缩进修复和健壮性改进) +++
    def _compare_versions(self, version1, version2):
        """比较两个版本号。version1 > version2 返回 1, 相等返回 0, 小于返回 -1。"""
        # 确保输入是字符串
        if not isinstance(version1, str) or not isinstance(version2, str):
            print(f"版本比较错误：输入不是字符串 ({type(version1)}, {type(version2)})")
            return 0

        try:
            # 使用正则表达式提取版本号中的数字部分
            v1_parts_str = re.findall(r'\d+', version1)
            v2_parts_str = re.findall(r'\d+', version2)

            # 转换为整数列表
            v1_parts = [int(p) for p in v1_parts_str]
            v2_parts = [int(p) for p in v2_parts_str]

            # 处理无法提取数字的情况 (例如 "Unknown" 或空字符串)
            if not v1_parts: v1_parts = [0]
            if not v2_parts: v2_parts = [0]

            # 逐部分比较
            for i in range(max(len(v1_parts), len(v2_parts))):
                v1_num = v1_parts[i] if i < len(v1_parts) else 0
                v2_num = v2_parts[i] if i < len(v2_parts) else 0

                if v1_num > v2_num:
                    return 1
                elif v1_num < v2_num:
                    return -1

            # 所有部分都相等
            return 0
        except ValueError as e:
            # 处理转换整数时的错误
            print(f"比较版本时转换数字出错 ('{version1}' vs '{version2}'): {e}")
            return 0
        except Exception as e:
            # 捕获其他潜在错误
            print(f"比较版本时发生未知错误 ('{version1}' vs '{version2}'): {e}")
            return 0
    # +++ 结束添加 +++

    # 添加一个方法来更新 ini 文件，供 network 模块调用
    def update_local_version_in_config(self, new_version: str):

        # (重复/错位的 update_local_version_in_config 代码已移除)
        old_version = self.local_version
        self.local_version = new_version # 同时更新内存中的版本
        print(f"本地版本已从 {old_version} 更新为 {self.local_version}")

        # 如果 UI 正在显示，更新本地版本标签。
        if hasattr(self, 'version_label') and self.version_label:
            try:
                self.version_label.setText(f"本地版本: {self.local_version}")
            except Exception as e:
                print(f"更新 UI 版本标签时出错: {e}")


    # --- DSD Generator 功能 ---

    def _read_dsd_settings_from_ini(self):
        """从 settings.ini 读取 DSD 相关设置"""
        config = configparser.ConfigParser()
        try:
            if os.path.exists(self.settings_path):
                config.read(self.settings_path, encoding='utf-8')
                if 'DSDExtension' in config:
                    self.dsd_exe_path = config['DSDExtension'].get('ExePath', '')
                    self.dsd_copy_enabled = config['DSDExtension'].getboolean('CopyEnabled', False)
                    print(f"从 {self.SETTINGS_FILE_NAME} 读取 DSD 设置: Path='{self.dsd_exe_path}', Copy={self.dsd_copy_enabled}")
                else:
                    print(f"信息: {self.SETTINGS_FILE_NAME} 中未找到 [DSDExtension] 部分。将使用默认值。")
                    self.dsd_exe_path = ""
                    self.dsd_copy_enabled = False
            else:
                print(f"信息: 未找到 {self.SETTINGS_FILE_NAME}。将使用默认 DSD 设置。")
                self.dsd_exe_path = ""
                self.dsd_copy_enabled = False
        except Exception as e:
            print(f"读取 {self.SETTINGS_FILE_NAME} 中的 DSD 设置时出错: {e}。将使用默认值。")
            self.dsd_exe_path = ""
            self.dsd_copy_enabled = False

    def _write_dsd_settings_to_ini(self):
        """将 DSD 相关设置写入 settings.ini"""
        config = configparser.ConfigParser()
        # 先读取现有文件，避免覆盖其他设置
        if os.path.exists(self.settings_path):
            try:
                config.read(self.settings_path, encoding='utf-8')
            except Exception as e:
                print(f"读取现有 {self.SETTINGS_FILE_NAME} 失败: {e}。将创建新文件或覆盖。")
                config = configparser.ConfigParser() # 重置以防万一

        if 'DSDExtension' not in config:
            config['DSDExtension'] = {}
        config['DSDExtension']['ExePath'] = self.dsd_exe_path
        config['DSDExtension']['CopyEnabled'] = str(self.dsd_copy_enabled) # 存储为字符串

        try:
            with open(self.settings_path, 'w', encoding='utf-8') as configfile:
                config.write(configfile)
            print(f"已将 DSD 设置写入 {self.SETTINGS_FILE_NAME}")
        except Exception as e:
            print(f"写入 {self.SETTINGS_FILE_NAME} 时出错: {e}")

    def _read_dsd_blacklist(self) -> List[str]:
        """从 dsd_blacklist.txt 读取黑名单并缓存"""
        self.dsd_blacklist_cache = []
        if os.path.exists(self.dsd_blacklist_path):
            try:
                with open(self.dsd_blacklist_path, 'r', encoding='utf-8') as f:
                    self.dsd_blacklist_cache = [line.strip() for line in f if line.strip()]
                print(f"已从 {self.dsd_blacklist_path} 加载 {len(self.dsd_blacklist_cache)} 条黑名单规则。")
            except Exception as e:
                print(f"读取黑名单文件 {self.dsd_blacklist_path} 时出错: {e}")
        else:
            print(f"信息: 未找到黑名单文件 {self.dsd_blacklist_path}。")
        return self.dsd_blacklist_cache

    def _save_dsd_blacklist(self, blacklist_list: List[str]):
        """将黑名单列表写入 dsd_blacklist.txt"""
        try:
            with open(self.dsd_blacklist_path, 'w', encoding='utf-8') as f:
                for item in blacklist_list:
                    f.write(item + '\n')
            self.dsd_blacklist_cache = blacklist_list # 更新缓存
            print(f"已将 {len(blacklist_list)} 条规则保存到 {self.dsd_blacklist_path}")
        except Exception as e:
            print(f"保存黑名单文件 {self.dsd_blacklist_path} 时出错: {e}")
            QtWidgets.QMessageBox.warning(None, "保存失败", f"无法保存黑名单文件:\n{e}")

    def _get_mod_priority(self, mod_name: str) -> int:
        """获取 Mod 的优先级"""
        # 确保 organizer 存在
        if not self.organizer:
            print("错误: Organizer 未初始化，无法获取 Mod 优先级。")
            return -1 # 返回一个无效的优先级
        try:
            return self.organizer.modList().priority(mod_name)
        except Exception as e:
            print(f"获取 Mod '{mod_name}' 优先级时出错: {e}")
            return -1 # 出错时也返回无效优先级

    def show_dsd_config_dialog(self):
       """显示 DSD 生成器的配置对话框"""
       dialog = DSDConfigDialog(self.window) # 父窗口设为 self.window
       dialog.set_exe_path(self.dsd_exe_path)
       dialog.set_copy_enabled(self.dsd_copy_enabled)
       dialog.set_blacklist(self.dsd_blacklist_cache) # 使用缓存的黑名单

       if dialog.exec_() == QtWidgets.QDialog.Accepted:
           new_exe_path = dialog.get_exe_path()
           new_copy_enabled = dialog.get_copy_enabled()
           new_blacklist = dialog.get_blacklist()

           # 检查路径是否有效
           if not new_exe_path or not os.path.exists(new_exe_path) or not os.path.isfile(new_exe_path):
                QtWidgets.QMessageBox.critical(
                    self.window,
                    "错误",
                    f"无效的 ESP2DSD 可执行文件路径:\n{new_exe_path}\n\n请确保文件存在且是一个有效的程序。"
                )
                return # 阻止继续执行

           # 保存设置
           self.dsd_exe_path = new_exe_path
           self.dsd_copy_enabled = new_copy_enabled
           self._write_dsd_settings_to_ini() # 写入 INI
           self._save_dsd_blacklist(new_blacklist) # 保存黑名单到文件

           # 确认是否开始生成
           reply = QtWidgets.QMessageBox.question(
               self.window,
               "确认操作",
               "即将扫描所有启用的 Mod 并生成 DSD 配置文件。\n\n"
               "这可能需要一些时间，具体取决于 Mod 数量。\n\n"
               "是否继续？",
               QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
               QtWidgets.QMessageBox.No # 默认选 No
           )

           if reply == QtWidgets.QMessageBox.Yes:
               try:
                   # 在单独的线程或使用 QProgressDialog 运行，避免阻塞 UI
                   self.run_dsd_generation()
               except Exception as e:
                   print(f"DSD 生成过程中发生错误: {e}")
                   QtWidgets.QMessageBox.critical(
                       self.window,
                       "生成失败",
                       f"生成 DSD 配置文件时遇到错误:\n\n{str(e)}"
                   )
           else:
               print("用户取消了 DSD 生成操作。")


    def run_dsd_generation(self):
        """执行 DSD 配置文件的生成过程，包含进度显示"""
        esp2dsd_exe = self.dsd_exe_path
        copy_enabled = self.dsd_copy_enabled
        blacklist = [b.lower() for b in self.dsd_blacklist_cache] # 使用缓存的黑名单并转小写

        if not self.organizer:
            QtWidgets.QMessageBox.critical(self.window, "错误", "Mod Organizer 对象未初始化！")
            return

        # --- 进度条设置 ---
        progress_dialog = QtWidgets.QProgressDialog(
            "正在扫描 Mod 并生成 DSD 配置...",
            "取消", # Cancel button text
            0,     # Minimum value
            100,   # Maximum value (will be updated later)
            self.window # Parent widget
        )
        progress_dialog.setWindowModality(Qt.ApplicationModal) # 模态对话框，阻止其他操作
        progress_dialog.setWindowTitle("DSD 生成进度")
        progress_dialog.setValue(0)
        progress_dialog.show()
        QCoreApplication.processEvents() # 确保对话框立即显示

        try:
            # --- 扫描 Mod 和插件 ---
            progress_dialog.setLabelText("正在扫描启用的 Mod...")
            progress_dialog.setValue(5) # 初始进度
            QCoreApplication.processEvents()

            mods = [record.internal_name for record in self.mod_state.active_records()]
            original_files = {}  # 存储原始插件 {relative_path: absolute_path}
            translation_files = {}  # 存储翻译插件 {relative_path: {'path': abs_path, 'original': orig_abs_path, 'mod_name': mod_name}}

            total_mods = len(mods)
            current_mod_index = 0
            for mod_name in mods:
                current_mod_index += 1
                progress_value = 5 + int(45 * (current_mod_index / total_mods)) # 扫描占 45% 进度
                progress_dialog.setValue(progress_value)
                progress_dialog.setLabelText(f"正在扫描 Mod: {mod_name} ({current_mod_index}/{total_mods})")
                if progress_dialog.wasCanceled():
                    raise InterruptedError("用户取消了操作。")
                QCoreApplication.processEvents()

                mod = self.organizer.modList().getMod(mod_name)
                if not mod: continue
                mod_path = mod.absolutePath()

                for root, _, files in os.walk(mod_path):
                    for file in files:
                        file_lower = file.lower()
                        if file_lower.endswith(('.esp', '.esm', '.esl')):
                            # 跳过黑名单
                            if file_lower in blacklist:
                                print(f"跳过黑名单插件: {file}")
                                continue

                            full_path = os.path.join(root, file)
                            relative_path = os.path.relpath(full_path, mod_path).lower() # 相对路径也用小写比较

                            # 检查是否为翻译文件
                            if relative_path in original_files:
                                # 简单的大小检查 (可能需要更复杂的逻辑)
                                try:
                                    orig_size = os.path.getsize(original_files[relative_path])
                                    trans_size = os.path.getsize(full_path)
                                    # 大小差异过大，可能不是翻译 (阈值可调整)
                                    if trans_size > orig_size * 1.5 or trans_size < orig_size * 0.5:
                                        # 更新原始文件为当前优先级更高的文件
                                        original_files[relative_path] = full_path
                                        print(f"更新原始文件记录: {relative_path} -> {full_path}")
                                        continue
                                except OSError: # 文件可能不存在或无权限
                                     continue # 跳过此文件

                                # 记录翻译文件，如果优先级更高则覆盖
                                current_priority = self._get_mod_priority(mod_name)
                                existing_priority = -1
                                if relative_path in translation_files:
                                     existing_priority = self._get_mod_priority(translation_files[relative_path]['mod_name'])

                                if current_priority > existing_priority:
                                     translation_files[relative_path] = {
                                         'path': full_path,
                                         'original': original_files[relative_path],
                                         'mod_name': mod_name
                                     }
                                     print(f"记录翻译文件: {relative_path} (来自 {mod_name})")

                            else:
                                # 记录为原始文件
                                original_files[relative_path] = full_path
                                print(f"记录原始文件: {relative_path} -> {full_path}")

            if not translation_files:
                raise ValueError("未在启用的 Mod 中找到任何可能的翻译插件！")

            # --- 生成 DSD 配置 ---
            progress_dialog.setMaximum(len(translation_files)) # 更新进度条最大值
            progress_dialog.setValue(0) # 重置进度值
            QCoreApplication.processEvents()

            # 创建输出 Mod 目录
            timestamp = datetime.now().strftime("%y-%m-%d-%H-%M")
            newMod_name = f"DSD_Configs_{timestamp}"
            newMod_dir = os.path.join(self.organizer.modsPath(), newMod_name)
            dsd_output_base = os.path.join(newMod_dir, "SKSE", "Plugins", "DynamicStringDistributor")
            os.makedirs(dsd_output_base, exist_ok=True)
            print(f"创建输出 Mod 目录: {newMod_dir}")

            processed_count = 0
            errors = []
            for rel_path, info in translation_files.items():
                processed_count += 1
                plugin_basename = os.path.basename(info['path'])
                progress_dialog.setValue(processed_count)
                progress_dialog.setLabelText(f"正在处理: {plugin_basename} ({processed_count}/{len(translation_files)})")
                if progress_dialog.wasCanceled():
                    raise InterruptedError("用户取消了操作。")
                QCoreApplication.processEvents()

                # esp2dsd.exe 输出目录和文件
                esp2dsd_cwd = os.path.dirname(esp2dsd_exe)
                esp2dsd_output_dir_rel = "output" # esp2dsd.exe 默认输出到 cwd 下的 output
                plugin_name_no_ext = os.path.splitext(plugin_basename)[0]
                expected_generated_file_rel = os.path.join(
                    esp2dsd_output_dir_rel,
                    f"{plugin_name_no_ext}_output{os.path.splitext(plugin_basename)[1]}.json"
                )
                expected_generated_file_abs = os.path.join(esp2dsd_cwd, expected_generated_file_rel)

                # 目标输出目录和文件 (在我们的新 Mod 里)
                target_output_dir = os.path.join(dsd_output_base, plugin_basename) # 按插件名分子目录
                target_output_file = os.path.join(target_output_dir, f"{plugin_basename}.json")

                print(f"处理插件: {plugin_basename}")
                print(f"  原始文件: {info['original']}")
                print(f"  翻译文件: {info['path']}")
                print(f"  预期生成文件: {expected_generated_file_abs}")
                print(f"  目标文件: {target_output_file}")

                try:
                    # 清理旧的输出文件（如果存在）
                    if os.path.exists(expected_generated_file_abs):
                        os.remove(expected_generated_file_abs)

                    # 调用 esp2dsd.exe
                    startupinfo = None
                    if os.name == 'nt':
                        startupinfo = subprocess.STARTUPINFO()
                        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                        startupinfo.wShowWindow = subprocess.SW_HIDE # 隐藏命令行窗口

                    result = subprocess.run(
                        [esp2dsd_exe, info['original'], info['path']],
                        check=True, # 如果 esp2dsd 返回非 0 退出码，则抛出 CalledProcessError
                        capture_output=True,
                        text=True,
                        encoding='utf-8', # 尝试指定编码
                        errors='ignore', # 忽略解码错误
                        cwd=esp2dsd_cwd, # 设置工作目录为 exe 所在目录
                        startupinfo=startupinfo
                    )
                    print(f"  esp2dsd 输出:\n{result.stdout}\n{result.stderr}")

                    # 检查并移动生成的文件
                    if os.path.exists(expected_generated_file_abs):
                        if os.path.getsize(expected_generated_file_abs) > 2: # 检查是否为空 JSON "{}"
                            os.makedirs(target_output_dir, exist_ok=True)
                            os.replace(expected_generated_file_abs, target_output_file) # 使用 replace 原子移动
                            print(f"  成功生成并移动配置文件到: {target_output_file}")

                            # 处理复制和重命名选项
                            if copy_enabled:
                                try:
                                    translation_plugin_path = info['path']
                                    translation_plugin_dir = os.path.dirname(translation_plugin_path)
                                    hidden_plugin_path = translation_plugin_path + ".mohidden"

                                    # 目标复制目录
                                    copy_to_dir_base = os.path.join(translation_plugin_dir, "SKSE", "Plugins", "DynamicStringDistributor")
                                    copy_to_dir_plugin = os.path.join(copy_to_dir_base, plugin_basename)
                                    copy_to_file = os.path.join(copy_to_dir_plugin, f"{plugin_basename}.json")

                                    # 确保目标目录存在
                                    os.makedirs(copy_to_dir_plugin, exist_ok=True)

                                    # 复制文件
                                    shutil.copy2(target_output_file, copy_to_file)
                                    print(f"  已复制配置文件到翻译 Mod: {copy_to_file}")

                                    # 重命名原翻译插件 (检查目标 .mohidden 是否已存在)
                                    if not os.path.exists(hidden_plugin_path):
                                        if os.path.exists(translation_plugin_path) and os.access(translation_plugin_path, os.W_OK):
                                             os.rename(translation_plugin_path, hidden_plugin_path)
                                             print(f"  已重命名翻译插件: {translation_plugin_path} -> {hidden_plugin_path}")
                                        else:
                                             print(f"  警告: 无法访问或写入翻译插件进行重命名: {translation_plugin_path}")
                                             errors.append(f"{plugin_basename}: 无法重命名原始翻译插件（权限问题？）")
                                    else:
                                        print(f"  警告: {hidden_plugin_path} 已存在，跳过重命名。")

                                except Exception as copy_err:
                                    print(f"  错误: 处理复制/重命名时出错: {copy_err}")
                                    errors.append(f"{plugin_basename}: 复制/重命名失败 - {copy_err}")
                        else:
                            print(f"  警告: 生成的 JSON 文件为空或过小，已跳过: {expected_generated_file_abs}")
                            os.remove(expected_generated_file_abs) # 删除空文件
                            errors.append(f"{plugin_basename}: 生成的 JSON 文件为空")
                    else:
                        print(f"  错误: 未找到预期的输出文件: {expected_generated_file_abs}")
                        errors.append(f"{plugin_basename}: esp2dsd 未生成预期的输出文件")

                except subprocess.CalledProcessError as e:
                    print(f"  错误: esp2dsd.exe 执行失败 (返回码 {e.returncode}):\nstdout: {e.stdout}\nstderr: {e.stderr}")
                    errors.append(f"{plugin_basename}: esp2dsd.exe 执行失败 - {e.stderr[:100]}...") # 只记录部分 stderr
                except InterruptedError: # 用户取消
                     raise
                except Exception as e:
                    print(f"  错误: 处理 {plugin_basename} 时发生未知错误: {e}")
                    errors.append(f"{plugin_basename}: 未知错误 - {e}")

            # --- 完成处理 ---
            progress_dialog.close() # 关闭进度条

            # 显示结果消息
            if not errors:
                QtWidgets.QMessageBox.information(
                    self.window,
                    "成功",
                    f"已成功为 {len(translation_files)} 个翻译插件生成 DSD 配置文件。\n\n"
                    f"配置文件已保存在新的 Mod 中:\n'{newMod_name}'\n\n"
                    "请在 Mod Organizer 2 中启用此新 Mod。"
                )
            else:
                error_details = "\n".join([f"- {err}" for err in errors[:10]]) # 最多显示 10 条错误
                if len(errors) > 10:
                    error_details += f"\n- ...以及其他 {len(errors) - 10} 个错误。"

                QtWidgets.QMessageBox.warning(
                    self.window,
                    "部分成功",
                    f"DSD 配置生成完成，但遇到以下 {len(errors)} 个错误:\n\n"
                    f"{error_details}\n\n"
                    f"成功的配置文件已保存在 Mod:\n'{newMod_name}'\n\n"
                    "请检查日志和上述错误详情。"
                )

            # 刷新 MO2 列表以显示新 Mod
            self.organizer.refresh(True) # True 表示强制刷新

        except InterruptedError:
             progress_dialog.close()
             QtWidgets.QMessageBox.information(self.window, "已取消", "DSD 配置生成操作已被用户取消。")
        except ValueError as e: # 特别处理未找到翻译文件的错误
             progress_dialog.close()
             QtWidgets.QMessageBox.warning(self.window, "未找到文件", str(e))
        except Exception as e:
            progress_dialog.close()
            print(f"DSD 生成过程中发生严重错误: {e}")
            import traceback
            traceback.print_exc() # 打印详细的回溯信息到控制台
            QtWidgets.QMessageBox.critical(
                self.window,
                "生成失败",
                f"生成 DSD 配置文件时遇到严重错误:\n\n{str(e)}\n\n请查看 MO2 日志获取详细信息。"
            )


  
    def show_crash_log_viewer(self):
       """查找并显示 NetScriptFramework 崩溃日志"""
       try:
           # 确保 organizer 存在且已初始化
           if not self.organizer:
               QtWidgets.QMessageBox.critical(self.window, "错误", "MO2 Organizer 未初始化。")
               return

           overwrite_path = self.organizer.overwritePath()
           if not overwrite_path:
                QtWidgets.QMessageBox.critical(self.window, "错误", "无法获取 MO2 Overwrite 路径。")
                return

           crash_log_dir = os.path.join(overwrite_path, "NetScriptFramework", "Crash")

           if not os.path.isdir(crash_log_dir):
               QtWidgets.QMessageBox.warning(
                   self.window, "未找到目录",
                   f"未找到 NetScriptFramework 崩溃日志目录:\n{crash_log_dir}\n\n请确保 NetScriptFramework 已安装并生成过日志。"
               )
               return

           log_files = []
           try:
               # 获取目录下所有文件及其修改时间
               entries = [os.path.join(crash_log_dir, f) for f in os.listdir(crash_log_dir)]
               # 过滤出文件并按修改时间降序排序
               log_files = sorted(
                   [f for f in entries if os.path.isfile(f) and f.lower().endswith('.txt')], 
                   key=os.path.getmtime,
                   reverse=True
               )
           except OSError as e:
                QtWidgets.QMessageBox.critical(
                   self.window, "读取错误",
                   f"读取日志目录时出错:\n{crash_log_dir}\n\n错误: {e}"
               )
                return


           if not log_files:
               QtWidgets.QMessageBox.information(
                   self.window, "无日志文件",
                   f"在以下目录中未找到 .txt 崩溃日志文件:\n{crash_log_dir}"
               )
               return

           # 创建对话框
           dialog = QtWidgets.QDialog(self.window) # 父窗口设为 self.window
           dialog.setWindowTitle("NetScriptFramework 崩溃日志查看器")
           dialog.setMinimumWidth(600) # 增加宽度以显示长文件名
           dialog.setMinimumHeight(400)
           # 设置为模态对话框，阻止与主窗口交互直到关闭
           dialog.setWindowModality(Qt.WindowModal)

           layout = QtWidgets.QVBoxLayout(dialog)

           label = QtWidgets.QLabel("找到以下崩溃日志 (按时间倒序排列):")
           layout.addWidget(label)

           list_widget = QtWidgets.QListWidget()
           # 存储完整路径，显示文件名和修改时间
           for log_path in log_files:
               try:
                   mod_time = datetime.fromtimestamp(os.path.getmtime(log_path)).strftime('%Y-%m-%d %H:%M:%S')
                   display_text = f"{os.path.basename(log_path)} ({mod_time})"
               except Exception: # 处理获取时间可能发生的错误
                   display_text = os.path.basename(log_path)

               item = QtWidgets.QListWidgetItem(display_text)
               item.setData(Qt.UserRole, log_path) # 将完整路径存储在 UserRole 中
               item.setToolTip(log_path) # 鼠标悬停显示完整路径
               list_widget.addItem(item)

           layout.addWidget(list_widget)

           # 按钮
           button_box = QtWidgets.QDialogButtonBox()
           open_button = button_box.addButton("打开分析网站并复制内容", QtWidgets.QDialogButtonBox.AcceptRole)
           cancel_button = button_box.addButton(QtWidgets.QDialogButtonBox.Cancel)


           def on_open():
               selected_items = list_widget.selectedItems()
               if selected_items:
                   selected_log_path = selected_items[0].data(Qt.UserRole)
                   self.open_log_in_browser(selected_log_path)
                   dialog.accept() # 打开后关闭对话框
               else:
                   QtWidgets.QMessageBox.warning(dialog, "未选择", "请先选择一个日志文件。")


           open_button.clicked.connect(on_open) # 连接 accept 信号到 on_open
           cancel_button.clicked.connect(dialog.reject) # 连接 reject 信号到 dialog.reject
           list_widget.itemDoubleClicked.connect(on_open) # 双击也打开

           layout.addWidget(button_box)
           dialog.setLayout(layout)
           dialog.exec() # 使用 exec() 显示模态对话框

       except AttributeError as e:
            # 更具体的错误检查
            if 'IOrganizer' in str(e) and 'overwritePath' in str(e):
                 QtWidgets.QMessageBox.critical(self.window, "错误", "无法获取 MO2 Overwrite 路径。Organizer 可能未正确初始化或 MO2 版本不兼容。")
            else:
                 QtWidgets.QMessageBox.critical(self.window, "属性错误", f"访问 MO2 Organizer 属性时出错: {e}")
            import traceback
            traceback.print_exc()
       except Exception as e:
            QtWidgets.QMessageBox.critical(self.window, "未知错误", f"显示崩溃日志时发生错误: {e}")
            import traceback
            traceback.print_exc()


    def _get_page_file_info(self):
        """获取虚拟内存信息并显示"""
        try:
            current_size_gb, recommended_size_gb, status = page_file_checker.get_page_file_info()
            msg = f"当前虚拟内存: {current_size_gb:.2f} GB\n" \
                  f"推荐虚拟内存: {recommended_size_gb:.2f} GB\n" \
                  f"状态: {status}"
            QtWidgets.QMessageBox.information(self.window, "虚拟内存信息", msg)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self.window, "错误", f"获取虚拟内存信息失败: {e}")

    def _set_page_file_size(self):
        """设置虚拟内存大小"""
        try:
            # 假设我们提供一个简单的输入框让用户输入大小
            size_gb, ok = QtWidgets.QInputDialog.getDouble(self.window, "设置虚拟内存", "请输入虚拟内存大小 (GB):",
                                                        value=0.0, min=0.0, decimals=2)
            if ok:
                size_mb = int(size_gb * 1024) # 转换为 MB
                utils.set_pagefile_size(size_mb, size_mb) # 假设设置初始大小和最大大小相同
                QtWidgets.QMessageBox.information(self.window, "成功", f"已尝试将虚拟内存设置为 {size_gb:.2f} GB。请重启系统以应用更改。")
            else:
                QtWidgets.QMessageBox.information(self.window, "取消", "已取消设置虚拟内存。")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self.window, "错误", f"设置虚拟内存失败: {e}")

    def _get_page_file_info(self):
        """获取虚拟内存信息并显示"""
        try:
            pagefiles_info = utils.get_pagefiles_size()
            
            if not pagefiles_info:
                msg = "未找到虚拟内存文件。"
                QtWidgets.QMessageBox.information(self.window, "虚拟内存信息", msg)
                return

            total_current_mb = sum(info[1] for info in pagefiles_info)
            total_max_mb = sum(info[2] for info in pagefiles_info)
            
            msg = "当前虚拟内存设置:\n"
            for drive, current_mb, max_mb in pagefiles_info:
                msg += f"驱动器 {drive.upper()}: 当前 {current_mb / 1024:.2f} GB, 最大 {max_mb / 1024:.2f} GB\n"
            
            msg += f"\n总计: 当前 {total_current_mb / 1024:.2f} GB, 最大 {total_max_mb / 1024:.2f} GB"
            
            status = "自定义" if any(info[1] != info[2] for info in pagefiles_info) else "系统管理" # 简化判断
            msg += f"\n状态: {status}"

            QtWidgets.QMessageBox.information(self.window, "虚拟内存信息", msg)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self.window, "错误", f"获取虚拟内存信息失败: {e}")

    def _set_page_file_size(self):
        """设置虚拟内存大小"""
        try:
            size_gb, ok = QtWidgets.QInputDialog.getDouble(self.window, "设置虚拟内存", "请输入虚拟内存大小 (GB):",
                                                        value=0.0, min=0.0, decimals=2)
            if ok:
                size_mb = int(size_gb * 1024) # 转换为 MB
                page_file_manager.set_page_file_size(size_mb)
                QtWidgets.QMessageBox.information(self.window, "成功", f"已尝试将虚拟内存设置为 {size_gb:.2f} GB。请重启系统以应用更改。")
            else:
                QtWidgets.QMessageBox.information(self.window, "取消", "已取消设置虚拟内存。")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self.window, "错误", f"设置虚拟内存失败: {e}")

    def _reset_page_file(self):
        """重置虚拟内存为系统管理"""
        try:
            utils.reset_pagefile_to_system_managed()
            QtWidgets.QMessageBox.information(self.window, "成功", "已尝试将虚拟内存重置为系统管理。请重启系统以应用更改。")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self.window, "错误", f"重置虚拟内存失败: {e}")

    def _open_system_settings(self):
        """打开系统虚拟内存设置界面"""
        try:
            utils.open_virtual_memory_settings()
            QtWidgets.QMessageBox.information(self.window, "提示", "已尝试打开系统虚拟内存设置界面。")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self.window, "错误", f"打开系统设置失败: {e}")

    def open_log_in_browser(self, log_path: str):
       """打开指定网页，将日志内容复制到剪贴板，并提示用户"""
       target_url = "https://phostwood.github.io/crash-analyzer/skyrim.html"
       log_content = ""

       try:
           # 尝试用 UTF-8 读取，如果失败，尝试 GBK (或系统默认编码)
           try:
               with open(log_path, 'r', encoding='utf-8', errors='ignore') as f: 
                   log_content = f.read()
           except OSError as e_os:
                QtWidgets.QMessageBox.critical(
                       self.window, "文件错误",
                       f"打开日志文件时出错:\n{log_path}\n\n错误: {e_os}"
                   )
                return


           # 打开网页
           print(f"正在打开网页: {target_url}")
           if not webbrowser.open(target_url):
               print(f"警告: webbrowser.open 返回 False，可能无法自动打开浏览器。")
               QtWidgets.QMessageBox.warning(
                   self.window, "无法打开浏览器",
                   f"无法自动打开网页: {target_url}\n\n请手动复制此链接并在浏览器中打开。"
               )
               # 即使无法打开浏览器，也尝试复制内容

           # 复制到剪贴板
           try:
               clipboard = QtWidgets.QApplication.clipboard() # 使用 QtWidgets.QApplication
               if clipboard is None:
                   raise RuntimeError("无法获取系统剪贴板。")
               clipboard.setText(log_content)
               print(f"已将 '{os.path.basename(log_path)}' 的内容复制到剪贴板。")

               # 提示用户
               QtWidgets.QMessageBox.information(
                   self.window, "操作提示",
                   f"已尝试在浏览器中打开日志分析网站。\n\n'{os.path.basename(log_path)}' 的内容已复制到剪贴板。\n\n请在浏览器中访问 {target_url} 并粘贴内容进行分析。"
               )

           except Exception as e_clip:
               print(f"剪贴板错误: {e_clip}")
               QtWidgets.QMessageBox.warning(
                   self.window, "剪贴板错误",
                   f"无法将日志内容复制到剪贴板:\n{e_clip}\n\n您可以尝试手动复制文件内容。\n文件路径: {log_path}"
               )
               # 提示用户手动复制
               QtWidgets.QMessageBox.information(
                   self.window, "操作提示",
                   f"已尝试在浏览器中打开日志分析网站。\n\n无法自动复制日志内容到剪贴板。\n\n请手动复制以下文件的内容:\n{log_path}\n\n然后在浏览器中访问 {target_url} 并粘贴内容进行分析。"
               )


       except FileNotFoundError:
           QtWidgets.QMessageBox.critical(self.window, "文件未找到", f"日志文件不存在:\n{log_path}")
       except Exception as e:
           QtWidgets.QMessageBox.critical(self.window, "未知错误", f"处理日志文件时发生错误: {e}")
           import traceback
           traceback.print_exc()


class DSDConfigDialog(QtWidgets.QDialog):
   def __init__(self, parent=None):
       super().__init__(parent)

       self.setWindowTitle("DSD 生成器设置")
       self.setMinimumWidth(450) # 稍微加宽一点

       layout = QtWidgets.QVBoxLayout(self)
       layout.setSpacing(10)
       layout.setContentsMargins(15, 15, 15, 15)

       # --- ESP2DSD 路径设置 ---
       path_group = QtWidgets.QGroupBox("ESP2DSD 可执行文件路径")
       path_layout = QtWidgets.QHBoxLayout()

       self.path_edit = QtWidgets.QLineEdit()
       self.path_edit.setPlaceholderText("选择 esp2dsd.exe 文件")
       path_layout.addWidget(self.path_edit)

       browse_button = QtWidgets.QPushButton("浏览...")
       browse_button.clicked.connect(self.browse_exe)
       path_layout.addWidget(browse_button)
       path_group.setLayout(path_layout)
       layout.addWidget(path_group)

       # --- 选项设置 ---
       options_group = QtWidgets.QGroupBox("生成选项")
       options_layout = QtWidgets.QVBoxLayout()
       self.copy_checkbox = QtWidgets.QCheckBox("将生成的配置文件复制到翻译补丁目录")
       self.copy_checkbox.setToolTip(
           "生成后会把配置复制到对应翻译 MOD，并暂时隐藏原翻译插件文件。"
           "此操作会修改 MOD 文件，请先确认已备份。"
       )
       options_layout.addWidget(self.copy_checkbox)
       options_group.setLayout(options_layout)
       layout.addWidget(options_group)


       # --- 黑名单管理 ---
       blacklist_group = QtWidgets.QGroupBox("忽略列表 (每行一个插件名，例如：Skyrim.esm)")
       blacklist_layout = QtWidgets.QVBoxLayout()
       self.blacklist_edit = QtWidgets.QTextEdit()
       self.blacklist_edit.setPlaceholderText("输入要忽略的插件文件名 (不区分大小写)，例如：\nUpdate.esm\nDawnguard.esm")
       self.blacklist_edit.setMaximumHeight(150) # 增加一点高度
       blacklist_layout.addWidget(self.blacklist_edit)
       blacklist_group.setLayout(blacklist_layout)
       layout.addWidget(blacklist_group)

       # --- 确定和取消按钮 ---
       button_box = QtWidgets.QDialogButtonBox(
           QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
       )
       button_box.accepted.connect(self.accept)
       button_box.rejected.connect(self.reject)
       layout.addWidget(button_box)

       self.setLayout(layout)

   def browse_exe(self):

       start_dir = os.environ.get('ProgramFiles', '') # 获取 Program Files 路径，失败则为空

       file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
           self,
           "选择 ESP2DSD 可执行文件",
           start_dir, # 从 Program Files 开始浏览
           "可执行文件 (*.exe);;所有文件 (*.*)"
       )
       if file_path:
           self.path_edit.setText(file_path)

   def get_exe_path(self) -> str:
       return self.path_edit.text().strip() # 去除前后空格

   def set_exe_path(self, path: str):
       self.path_edit.setText(path)

   def get_copy_enabled(self) -> bool:
       return self.copy_checkbox.isChecked()

   def set_copy_enabled(self, enabled: bool):
       self.copy_checkbox.setChecked(enabled)

   def get_blacklist(self) -> List[str]:
       # 从 QTextEdit 获取文本，按行分割，并移除空行和首尾空格
       return [line.strip() for line in self.blacklist_edit.toPlainText().split('\n') if line.strip()]

   def set_blacklist(self, blacklist: List[str]):
       # 将列表合并为带换行符的字符串
       self.blacklist_edit.setPlainText('\n'.join(blacklist))



def createPlugin():
   return ConsolidationController()
