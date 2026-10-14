#!/usr/bin/env python3
# coding: utf-8
"""Publish raw MO2 sorting files already committed to GitHub; no ZIP.

Expected:
  sort/releases/<slug>/v<version>/modlist.txt
or:
  sort/releases/<slug>/v<version>/plugins.txt
  sort/releases/<slug>/v<version>/loadorder.txt
  sort/releases/<slug>/v<version>/lockedorder.txt

A complete unit is published only after all its files pass validation.
Existing sort manifest releases are preserved. GitHub/Gitee use direct TXT URLs.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("sort_update_core",ROOT/"development/v2.4.2/sort_update_core.py")
core=importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def gather(root=ROOT):
    folder=Path(root)/"sort/releases"
    result=[]
    if not folder.exists():
        return result
    for release_dir in sorted(x for x in folder.iterdir() if x.is_dir()):
        slug=release_dir.name
        if not core.SLUG.fullmatch(slug):
            raise RuntimeError("Invalid sorting release ID: "+slug)
        for version_dir in sorted(x for x in release_dir.iterdir() if x.is_dir()):
            name=version_dir.name
            if not name.startswith("v"):
                raise RuntimeError("Version folder must start with v: "+name)
            version=name[1:]
            core._version(version)
            present={p.name for p in version_dir.iterdir() if p.is_file()}
            if any(x not in set(core.KINDS["mods"]+core.KINDS["plugins"]) for x in present):
                raise RuntimeError("Unexpected file in sort release folder "+str(version_dir))
            if not present:
                continue
            if present=={"modlist.txt"}:
                kind="mods"
            elif present==set(core.KINDS["plugins"]):
                kind="plugins"
            else:
                # A partial upload is NOT made visible to users.
                print("SKIPPED_INCOMPLETE",slug,version,sorted(present))
                continue
            descriptors={}
            for filename in core.KINDS[kind]:
                path=version_dir/filename
                body=path.read_bytes()
                core._check_text(filename,body)
                if path.is_symlink() or len(body)>core.MAX_TEXT:
                    raise RuntimeError("Invalid sorting release file: "+str(path))
                rel="sort/releases/%s/%s/%s"%(slug,name,filename)
                descriptors[filename]={
                    "size":len(body),
                    "sha256":hashlib.sha256(body).hexdigest(),
                    "mirrors":[
                        {"name":"Gitee 国内主源","url":"https://gitee.com/HR-world/xingli/raw/main/"+rel},
                        {"name":"GitHub 备用源","url":"https://raw.githubusercontent.com/haoranzheng/xingli/main/"+rel}
                    ]
                }
            title=("星黎左侧 MOD 排序 " if kind=="mods" else "星黎右侧插件排序 ")+name
            result.append({
                "id":slug,"version":version,"kind":kind,
                "title":title,
                "changelog":"官方排序文件更新：下载保存为当前 Profile 的新增备份；不会直接改变现有排序。",
                "files":descriptors,
            })
    return result


def generate(root=ROOT):
    root=Path(root)
    manifest=root/"sort/latest.json"
    original=json.loads(manifest.read_text(encoding="utf-8-sig"))
    core.validate_manifest(original)
    records=gather(root)
    by_id={x["id"]:x for x in original["releases"]}
    for item in records:
        old=by_id.get(item["id"])
        if old is None or core._version(item["version"])>=core._version(old["version"]):
            by_id[item["id"]]=item
    original["releases"]=sorted(by_id.values(),key=lambda x:x["id"])
    original["published_at"]=datetime.now(timezone.utc).isoformat(timespec="seconds")
    core.validate_manifest(original)
    return original,records


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write",action="store_true",help="write manifest after validation")
    args=ap.parse_args()
    manifest,ready=generate()
    print("READY_SORT_RELEASES",len(ready))
    for item in ready:
        print(item["id"],item["version"],item["kind"],
              ",".join(item["files"]))
    if args.write and ready:
        destination=ROOT/"sort/latest.json"
        destination.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print("UPDATED",destination)
    elif not ready:
        print("No complete sorting release. Official manifest left unchanged.")


if __name__=="__main__":
    main()
