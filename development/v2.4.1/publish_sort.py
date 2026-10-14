#!/usr/bin/env python3
# coding: utf-8
"""Publish a small MO2-native sorting backup to Gitee/GitHub via git.

Never publishes a real ordering without an author-selected local source.
The source files are copied unchanged to ZIP root; MO2 restores them later.
"""
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import zipfile
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("sort_update_core",ROOT/"development/v2.4.1/sort_update_core.py")
core=importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--kind",required=True,choices=["mods","plugins"])
    ap.add_argument("--id",required=True)
    ap.add_argument("--version",required=True)
    ap.add_argument("--title",required=True)
    ap.add_argument("--changelog",required=True)
    ap.add_argument("--modlist",type=Path)
    ap.add_argument("--plugins",type=Path)
    ap.add_argument("--loadorder",type=Path)
    ap.add_argument("--lockedorder",type=Path)
    ap.add_argument("--dry-run",action="store_true")
    args=ap.parse_args()
    if not core.SLUG.fullmatch(args.id):
        ap.error("Invalid release ID")
    core._version(args.version)
    requested={"modlist.txt":args.modlist,"plugins.txt":args.plugins,
               "loadorder.txt":args.loadorder,"lockedorder.txt":args.lockedorder}
    required=set(core.KINDS[args.kind])
    files={}
    for name,path in requested.items():
        if name in required:
            if path is None or not path.is_file():
                ap.error("Missing required profile sorting file "+name)
            files[name]=core._check_text(name,path.read_bytes())
        elif path is not None:
            ap.error(name+" is not valid for kind "+args.kind)
    fileobj=io.BytesIO()
    with zipfile.ZipFile(fileobj,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name in sorted(files):
            z.writestr(name,files[name])
    payload=fileobj.getvalue()
    if len(payload)>core.MAX_DOWNLOAD:
        ap.error("Sorting backup too large")
    core.decode_order_archive(payload,args.kind)
    filename="%s_v%s_%s.zip"%(args.id,args.version,args.kind)
    output=ROOT/"sort/releases"/filename
    if output.exists():
        ap.error("Release ZIP already exists: refusing overwrite")
    manifest=ROOT/"sort/latest.json"
    data=json.loads(manifest.read_text(encoding="utf-8-sig"))
    core.validate_manifest(data)
    rec={
        "id":args.id,"version":args.version,"kind":args.kind,
        "title":args.title,"changelog":args.changelog,
        "package":{
            "size":len(payload),
            "sha256":hashlib.sha256(payload).hexdigest(),
            "mirrors":[
                {"name":"Gitee 国内主源","url":"https://gitee.com/HR-world/xingli/raw/main/sort/releases/"+filename},
                {"name":"GitHub 备用源","url":"https://raw.githubusercontent.com/haoranzheng/xingli/main/sort/releases/"+filename}
            ]
        }
    }
    data["releases"]=[x for x in data["releases"] if x["id"]!=rec["id"]]+[rec]
    data["releases"].sort(key=lambda x:x["id"])
    core.validate_manifest(data)
    print("Backup type:",args.kind)
    print("SHA256:",rec["package"]["sha256"])
    print("Size:",len(payload))
    print("Files:",", ".join(sorted(files)))
    if args.dry_run:
        print(json.dumps(rec,ensure_ascii=False,indent=2))
        print("DRY RUN: no files written")
        return
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_bytes(payload)
    data["published_at"]=datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Written",output)
    print("Written",manifest)
    print("Push both files to GitHub main and confirm Gitee mirror before prompting users.")


if __name__=="__main__":
    main()
