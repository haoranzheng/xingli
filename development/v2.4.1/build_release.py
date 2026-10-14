#!/usr/bin/env python3
# coding: utf-8
"""Build v2.4.1 from published v2.4.0; preserve old functions but disable calls."""
import ast
import hashlib
import json
from pathlib import Path
import zipfile
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/"update/releases/xingli_Little_assistant_v2.4.0_update.zip"
TARGET=ROOT/"update/releases/xingli_Little_assistant_v2.4.1_update.zip"
MANIFEST=ROOT/"update/latest.json"
PREFIX="xingli_Little_assistant/"


def once(text,original,replacement,label):
    count=text.count(original)
    if count!=1:
        raise RuntimeError("%s: expected 1 anchor, got %d"%(label,count))
    return text.replace(original,replacement,1)


def main():
    with zipfile.ZipFile(BASE) as z:
        assert z.testzip() is None
        files={entry.filename:z.read(entry) for entry in z.infolist() if not entry.is_dir()}
    k=PREFIX+"consolidation_controller.py"
    s=files[k].decode("utf-8-sig").replace("\r\n","\n")
    s=once(s,'DEFAULT_VERSION = "2.4.0"',
             'DEFAULT_VERSION = "2.4.1"',"default_version")
    s=once(s,"version() 返回版本 2.4.0-user",
             "version() 返回版本 2.4.1-user","log_version")
    s=once(s,"return mobase.VersionInfo(2, 4, 0)",
             "return mobase.VersionInfo(2, 4, 1)","MO2_version")
    s=once(s,"from . import mod_update_ui\n",
             "from . import mod_update_ui\nfrom . import sort_update_ui\n","new import")
    s=once(s,"        QTimer.singleShot(4400, lambda: mod_update_ui.maybe_auto_check(self))",
             "        # Disabled temporarily: single-MOD update startup check.\n"
             "        QTimer.singleShot(5400, lambda: sort_update_ui.startup_check(self))",
             "disable MOD update; enable sorting check")
    s=once(s,
'''        mod_updates_button = QtWidgets.QPushButton("📦  单独 MOD 更新")
        mod_updates_button.setToolTip("查看并安装来自星黎镜像/R2 的指定 MOD 更新。")
        mod_updates_button.clicked.connect(self.show_mod_updates)
        daily_grid.addWidget(mod_updates_button, 2, 0)''',
'''        # The per-MOD update implementation remains packaged, but its entry
        # point and startup checks are intentionally hidden until re-enabled.
        sort_updates_button = QtWidgets.QPushButton("📑  排序备份更新")
        sort_updates_button.setToolTip("从国内镜像下载可由 MO2 原生还原的排序备份，不直接修改排序。")
        sort_updates_button.clicked.connect(self.show_sort_updates)
        daily_grid.addWidget(sort_updates_button, 2, 0)''',
             "replace daily action")
    s=once(s,"    def show_mod_updates(self):",
             '''    def show_sort_updates(self):
        """打开排序备份下载中心，不自动恢复排序。"""
        return sort_update_ui.show_center(self)

    def show_mod_updates(self):''',
             "new action method")
    files[k]=s.encode("utf-8")
    files[PREFIX+"version.ini"]=b"[Version]\nlocal = 2.4.1\n"
    for filename in ("sort_update_core.py","sort_update_ui.py"):
        files[PREFIX+filename]=(ROOT/"development/v2.4.1"/filename).read_bytes()
    files[PREFIX+"CHANGELOG_v2.4.1.md"]=(
        "# v2.4.1：排序备份更新\n\n"
        "- 暂时屏蔽单独 MOD 更新的入口和启动推送；代码保留，后续可恢复。\n"
        "- 新增排序备份独立清单，通过 Gitee 国内主源及 GitHub Raw 备用源检查和下载。\n"
        "- 左侧排序备份仅为 modlist.txt；右侧排序备份必须包含 plugins.txt、loadorder.txt、lockedorder.txt 三件套。\n"
        "- 按 MO2 原生恢复规则命名，下载后以 YYYY_MM_DD_hh_mm_ss_Xingli_序号 保存到当前 Profile。\n"
        "- 严禁覆盖现有 modlist.txt / plugins.txt / loadorder.txt / lockedorder.txt 与既有备份。\n"
        "- 下载完成后需用户自行在 MO2 原生“还原”下拉框中选择新备份，不会自动改动排序。\n"
        "- 按 Profile 记录已下载发布版本，重复启动不反复提示同一个更新。\n"
    ).encode("utf-8")
    for name,data in files.items():
        if name.endswith(".py"):
            ast.parse(data.decode("utf-8-sig"),filename=name,feature_version=(3,8))
    assert "mod_update_ui.maybe_auto_check(self)" not in s
    assert '单独 MOD 更新")' not in s
    assert "sort_update_ui.startup_check(self)" in s
    assert "sort_update_ui.show_center(self)" in s
    with zipfile.ZipFile(TARGET,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name in sorted(files):
            z.writestr(name,files[name])
    with zipfile.ZipFile(TARGET) as z:
        assert z.testzip() is None and len(z.namelist())==len(files)
    data=TARGET.read_bytes()
    manifest=json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    assert manifest["version"]=="2.4.0"
    manifest["version"]="2.4.1"
    manifest["title"]="星黎 MO2 小助手 v2.4.1 — 排序备份更新"
    manifest["published_at"]=datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest["changelog"]="暂时屏蔽单独 MOD 更新；新增 MO2 原生排序备份推送下载，日期序号唯一文件名，不覆盖现有 Profile 排序或本地备份。"
    manifest["package"]={
        "filename":TARGET.name,"size":len(data),
        "sha256":hashlib.sha256(data).hexdigest(),
        "mirrors":[
            {"name":"Gitee 国内主源","url":"https://gitee.com/HR-world/xingli/raw/main/update/releases/"+TARGET.name},
            {"name":"GitHub 备用源","url":"https://raw.githubusercontent.com/haoranzheng/xingli/main/update/releases/"+TARGET.name}
        ]
    }
    sort_manifest=json.loads((ROOT/"sort/latest.json").read_text(encoding="utf-8-sig"))
    assert sort_manifest["product"]=="xingli_sort_updates" and sort_manifest["releases"]==[]
    MANIFEST.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("BUILD_241_PASS",len(files),len(data),manifest["package"]["sha256"])


if __name__=="__main__":
    main()
