#!/usr/bin/env python3
# coding: utf-8
"""Patch real v2.4.1 plugin release to support raw sorting TXT release records."""
import ast
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
import zipfile

ROOT=Path(__file__).resolve().parents[2]
INPUT=ROOT/"update/releases/xingli_Little_assistant_v2.4.1_update.zip"
OUTPUT=ROOT/"update/releases/xingli_Little_assistant_v2.4.2_update.zip"
LATEST=ROOT/"update/latest.json"
PREFIX="xingli_Little_assistant/"

def change_once(text, old, new, name):
    if text.count(old)!=1:
        raise RuntimeError("Expected exactly one %s; found %d"%(name,text.count(old)))
    return text.replace(old,new,1)

def main():
    with zipfile.ZipFile(INPUT) as archive:
        assert archive.testzip() is None
        files={item.filename:archive.read(item) for item in archive.infolist()
               if not item.is_dir()}
    controller=PREFIX+"consolidation_controller.py"
    core=PREFIX+"sort_update_core.py"
    s=files[controller].decode("utf-8-sig").replace("\r\n","\n")
    s=change_once(s,'DEFAULT_VERSION = "2.4.1"',
                  'DEFAULT_VERSION = "2.4.2"',"default version")
    s=change_once(s,'version() 返回版本 2.4.1-user',
                  'version() 返回版本 2.4.2-user',"diagnostics")
    s=change_once(s,'return mobase.VersionInfo(2, 4, 1)',
                  'return mobase.VersionInfo(2, 4, 2)',"MO2 plugin version")
    files[controller]=s.encode("utf-8")
    files[core]=(ROOT/"development/v2.4.2/sort_update_core.py").read_bytes()
    files[PREFIX+"version.ini"]=b"[Version]\nlocal = 2.4.2\n"
    files[PREFIX+"CHANGELOG_v2.4.2.md"]=(
        "# 星黎 v2.4.2：无需 ZIP 的排序文本文件直传\n\n"
        "- 原始 modlist.txt、plugins.txt、loadorder.txt、lockedorder.txt 可分别存入 GitHub/Gitee 仓库。\n"
        "- 每个文件独立大小和 SHA256 校验，右侧三件套全部通过后才生成 MO2 可还原备份。\n"
        "- 兼容旧 v2.4.1 的 ZIP 排序推送清单，不需要转换旧数据。\n"
        "- 继续保留唯一日期序号命名和当前 Profile 的原有排序；不自动执行恢复。\n"
        "- 暂时屏蔽单独 MOD 更新保持不变。\n"
    ).encode("utf-8")
    for name,body in files.items():
        if name.endswith(".py"):
            ast.parse(body.decode("utf-8-sig"),filename=name,feature_version=(3,8))
    assert b"if \"files\" in record" in files[core]
    assert b"def install_as_backups" in files[core]
    assert b"mod_update_ui.maybe_auto_check(self)" not in files[controller]
    assert b"sort_update_ui.startup_check(self)" in files[controller]
    with zipfile.ZipFile(OUTPUT,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name in sorted(files):
            z.writestr(name,files[name])
    with zipfile.ZipFile(OUTPUT) as z:
        assert z.testzip() is None
        assert len(z.namelist())==len(files)
    blob=OUTPUT.read_bytes()
    data=json.loads(LATEST.read_text(encoding="utf-8-sig"))
    assert data["version"]=="2.4.1"
    data["version"]="2.4.2"
    data["title"]="星黎 MO2 小助手 2.4.2：排序文件直传"
    data["changelog"]="排序推送支持不打包 ZIP，直接下载 GitHub/Gitee 原始 txt 文件并分别校验后导入 MO2 备份列表。兼容旧 ZIP 排序包；独立 MOD 更新继续屏蔽。"
    data["published_at"]=datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["package"]={
        "filename":OUTPUT.name, "size":len(blob),
        "sha256":hashlib.sha256(blob).hexdigest(),
        "mirrors":[
            {"name":"Gitee 国内主源","url":"https://gitee.com/HR-world/xingli/raw/main/update/releases/"+OUTPUT.name},
            {"name":"GitHub 备用源","url":"https://raw.githubusercontent.com/haoranzheng/xingli/main/update/releases/"+OUTPUT.name}
        ]
    }
    LATEST.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("BUILD_242_PASS",len(blob),data["package"]["sha256"])

if __name__=="__main__":
    main()
