# coding: utf-8
"""Produce the complete v2.3.0 ZIP from the published v2.2.0 plugin.

No local MO2 installation is required. This script is intended for CI and
performs strict anchor checks; any divergence blocks publishing.
"""
import ast
import hashlib
import io
import json
import pathlib
import re
import zipfile
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
BASE = ROOT / "update/releases/xingli_Little_assistant_v2.2.0_update.zip"
DEST = ROOT / "update/releases/xingli_Little_assistant_v2.3.0_update.zip"
MANIFEST = ROOT / "update/latest.json"
PREFIX = "xingli_Little_assistant/"
VERSION = "2.3.0"

def replace_once(source, old, new, label):
    found = source.count(old)
    if found != 1:
        raise AssertionError("%s: required anchor count 1, got %s" % (label, found))
    return source.replace(old, new, 1)

def modify_controller(data):
    s=data.decode("utf-8-sig").replace("\r\n", "\n")
    s=replace_once(s,'DEFAULT_VERSION = "2.2.0"','DEFAULT_VERSION = "2.3.0"',"default version")
    s=replace_once(s,'version() 返回版本 2.2.0-user','version() 返回版本 2.3.0-user',"diagnostic version")
    s=replace_once(s,'return mobase.VersionInfo(2, 2, 0)','return mobase.VersionInfo(2, 3, 0)',"MO2 version")
    s=replace_once(s,'''        self._help_request_timer.start()
        return True''','''        self._help_request_timer.start()
        # MO2 has successfully completed Xingli init (even if the helper UI stays closed).
        QTimer.singleShot(0, lambda: auto_updater.mark_startup_healthy(self))
        return True''',"ready marker")
    return s.encode("utf-8")

def modify_updater(data):
    s=data.decode("utf-8-sig").replace("\r\n", "\n")
    # The 2.2.0 checker already runs at MO2 startup. Notify users only when newer.
    anchor='''                _publish_home_status(controller, state, latest=latest, source=source)'''
    insert='''                _publish_home_status(controller, state, latest=latest, source=source)
                if state == "update_available" and respect_auto_setting:
                    # This popup belongs to the MO2 lifecycle, not the Xingli window.
                    # checked is a QThread signal, delivered on the Qt UI thread.
                    QTimer.singleShot(0, lambda: _prompt_startup_update(controller, manifest))'''
    s=replace_once(s,anchor,insert,"show startup notification")
    s=replace_once(s,'''def maybe_auto_check(controller, parent=None) -> None:
    """Check once after MO2 starts. Never interrupts the user; home page shows the result."""''','''def maybe_auto_check(controller, parent=None) -> None:
    """Check once after MO2 starts, prompting even before Xingli UI is opened."""''',"autocheck docs")
    append = '''

def mark_startup_healthy(controller) -> None:
    """Mark a successful MO2 plugin initialization for the external update watchdog."""
    try:
        version = str(getattr(controller, "local_version", "") or "")
        if not version or not all(part.isdigit() for part in version.split(".")):
            return
        path = os.path.join(updates_dir(controller), "startup_health_" + version + ".json")
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump({"version": version, "ready": True,
                       "time": datetime.now().isoformat(timespec="seconds")}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        logger.exception("Cannot write startup health marker")


def _prompt_startup_update(controller, manifest) -> None:
    """New-version popup, independent of opening the helper window."""
    try:
        version = str(manifest.get("version", ""))
        if compare_versions(version, getattr(controller, "local_version", "0.0.0")) <= 0:
            return
        if getattr(controller, "_xingli_startup_prompted_version", None) == version:
            return
        settings = get_update_settings(controller)
        if str(settings.get("skippedversion", "")) == version:
            return
        controller._xingli_startup_prompted_version = version
        parent = None
        try:
            parent = controller._parentWidget()
        except Exception:
            pass
        dialog = QtWidgets.QMessageBox(parent)
        dialog.setWindowTitle("星黎小助手 · 发现新版本")
        dialog.setText("发现星黎小助手新版本 %s（当前 %s）" %
                       (version, getattr(controller, "local_version", "未知")))
        dialog.setInformativeText(str(manifest.get("changelog", "未提供更新说明。"))[:2500])
        roles = getattr(QtWidgets.QMessageBox, "ButtonRole", QtWidgets.QMessageBox)
        view = dialog.addButton("查看并更新", roles.AcceptRole)
        later = dialog.addButton("稍后提醒", roles.RejectRole)
        dialog.setDefaultButton(view)
        _exec_dialog(dialog)
        if dialog.clickedButton() is view:
            show_auto_updater(controller, parent=parent)
    except Exception:
        logger.exception("Startup update popup failed")
'''
    s += append

    # Improve installer reliability; never discard leftovers from an interrupted swap.
    s=replace_once(s,
'''    if (Test-Path -LiteralPath $NewDir) {{ Remove-Item -LiteralPath $NewDir -Recurse -Force }}
    if (Test-Path -LiteralPath $OldDir) {{ Remove-Item -LiteralPath $OldDir -Recurse -Force }}''',
'''    if (Test-Path -LiteralPath $NewDir) {{ throw '发现未清理的 __new__ 目录；请先恢复或清理后重试。' }}
    if (Test-Path -LiteralPath $OldDir) {{ throw '发现未清理的 __old__ 目录；为保护旧版已终止更新。' }}''',"interrupted swap protection")

    s=replace_once(s,
'''$UpdateRoot = {update_root}
$LogDir = {log_dir}''',
'''$UpdateRoot = {update_root}
$HealthMarker = Join-Path $UpdateRoot ('startup_health_' + $Version + '.json')
$LogDir = {log_dir}''',"health marker for watchdog")

    # A failed relaunch may indicate an incompatible update. Ask MO2 to close
    # gently, recover the retained backup once it exits, and restart old MO2.
    old='''if (Test-Path -LiteralPath $Mo2Exe) {{ Start-Process -FilePath $Mo2Exe }}
Start-Sleep -Seconds 2'''
    new='''if (Test-Path -LiteralPath $Mo2Exe) {{
    try {{
        if (Test-Path -LiteralPath $HealthMarker) {{ Remove-Item -LiteralPath $HealthMarker -Force }}
        Start-Process -FilePath $Mo2Exe
        $result = Get-Content -LiteralPath $ResultPath -Raw | ConvertFrom-Json
        if ($result.status -eq 'success') {{
            $healthy = $false
            for ($attempt = 0; $attempt -lt 90; $attempt++) {{
                if (Test-Path -LiteralPath $HealthMarker) {{ $healthy = $true; break }}
                Start-Sleep -Seconds 1
            }}
            if (-not $healthy -and (Test-Path -LiteralPath $BackupDir)) {{
                Write-Host '[警告] 新版本没有完成启动健康确认，尝试正常关闭 MO2 并回滚。'
                foreach ($proc in @(Get-Process -Name 'ModOrganizer' -ErrorAction SilentlyContinue)) {{
                    try {{ [void]$proc.CloseMainWindow() }} catch {{}}
                }}
                for ($attempt = 0; $attempt -lt 40; $attempt++) {{
                    if (-not (Get-Process -Name 'ModOrganizer' -ErrorAction SilentlyContinue)) {{ break }}
                    Start-Sleep -Seconds 1
                }}
                if (-not (Get-Process -Name 'ModOrganizer' -ErrorAction SilentlyContinue)) {{
                    $FailedDir = Join-Path $PluginsParent ('xingli_Little_assistant.__failed__' + $Stamp)
                    if (Test-Path -LiteralPath $PluginDir) {{
                        Move-Item -LiteralPath $PluginDir -Destination $FailedDir
                    }}
                    Copy-Item -LiteralPath $BackupDir -Destination $PluginDir -Recurse -Force
                    Write-Result 'rolled_back' '新版启动未通过健康确认，已自动恢复旧版本' $BackupDir
                    Start-Process -FilePath $Mo2Exe
                }} else {{
                    Write-Result 'needs_recovery' 'MO2 未退出；为避免破坏运行进程，保留备份，等待手工恢复。' $BackupDir
                }}
            }}
        }}
    }} catch {{
        Write-Host ('[注意] 启动健康监测未完成：' + $_.Exception.Message)
    }}
}}
Start-Sleep -Seconds 2'''
    s=replace_once(s,old,new,"external health rollback")
    return s.encode("utf-8")

def main():
    with zipfile.ZipFile(BASE,"r") as archive:
        assert archive.testzip() is None
        files={info.filename:archive.read(info) for info in archive.infolist() if not info.is_dir()}
    controller=PREFIX+"consolidation_controller.py"
    updater=PREFIX+"auto_updater.py"
    ini=PREFIX+"version.ini"
    assert controller in files and updater in files and ini in files
    files[controller]=modify_controller(files[controller])
    files[updater]=modify_updater(files[updater])
    files[ini]=b"[Version]\\nlocal = 2.3.0\\n"
    files[PREFIX+"CHANGELOG_v2.3.0.md"]=(""
        "# v2.3.0 — 启动更新提示及自动回滚\\n\\n"
        "- MO2 启动时自动检查国内镜像源，发现新版本即弹窗；无需打开星黎小助手。\\n"
        "- 更新弹窗支持查看更新、稍后提醒；安装仍需用户明确确认。\\n"
        "- 继续使用 Gitee 主源、GitHub Raw 备用源和 SHA256 校验；不使用 GitHub Releases。\\n"
        "- 安装器保护意外遗留的中间目录，保留旧版备份。\\n"
        "- 自 v2.3.0 发起的后续更新会监测 MO2 启动健康标记，若未就绪，尝试正常关闭并自动恢复旧版。\\n"
        "- 从 v2.2.0 升级到本版仍由 v2.2.0 安装器负责执行，无法追溯增加其启动健康监测。\\n"
    ).encode("utf-8")
    for name,payload in files.items():
        if name.endswith(".py"):
            ast.parse(payload.decode("utf-8-sig"),filename=name,feature_version=(3,8))
    assert b"def _prompt_startup_update" in files[updater]
    assert b"respect_auto_setting" in files[updater]
    assert b"mark_startup_healthy(self)" in files[controller]
    assert b"health" in files[updater]
    assert b"2.3.0" in files[ini]
    assert (ROOT/"modpack/latest.json").is_file()
    DEST.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(DEST,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name in sorted(files):
            archive.writestr(name,files[name])
    with zipfile.ZipFile(DEST) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist())==len(files)
    data=DEST.read_bytes()
    manifest=json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    manifest.update(
        version=VERSION,title="星黎 MO2 小助手 2.3.0",
        published_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        changelog="MO2 启动自动检查新版本；即使未打开星黎界面也主动弹窗；更新中断保护及安装失败回滚增强，后续更新支持启动健康检测。"
    )
    package=manifest["package"]
    filename=DEST.name
    package.update(filename=filename,size=len(data),sha256=hashlib.sha256(data).hexdigest(),
                   mirrors=[
                       {"name":"Gitee 国内主源","url":"https://gitee.com/HR-world/xingli/raw/main/update/releases/"+filename},
                       {"name":"GitHub 备用源","url":"https://raw.githubusercontent.com/haoranzheng/xingli/main/update/releases/"+filename}
                   ])
    MANIFEST.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\\n",encoding="utf-8")
    print("BUILD_230_PASS",len(files),"files",len(data),"bytes",package["sha256"])
    print("Autocheck startup + popup + future installer rollback: VERIFIED STATIC")
if __name__=="__main__":
    main()
