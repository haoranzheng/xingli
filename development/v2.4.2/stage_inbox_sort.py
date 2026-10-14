#!/usr/bin/env python3
# coding: utf-8
"""Accept author's plain MO2 text files as a *single* GitHub web upload.

Drop all four files into sort/inbox/YYYYMMDD/:
  modlist.txt (or modlist(2).txt), plugins.txt, loadorder.txt, lockedorder.txt.
On a complete set, preserve bytes verbatim, stage native releases, then publish
SHA256 URLs through sort/latest.json. Incomplete sets never notify players.
"""
import importlib.util
from datetime import datetime
from pathlib import Path
import json
import re

ROOT=Path(__file__).resolve().parents[2]
module_spec=importlib.util.spec_from_file_location(
    "sort_core", ROOT/"development/v2.4.2/sort_update_core.py")
core=importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(core)
p_spec=importlib.util.spec_from_file_location(
    "sort_pub", ROOT/"development/v2.4.2/publish_raw_sort.py")
publisher=importlib.util.module_from_spec(p_spec)
p_spec.loader.exec_module(publisher)

MODLIST_PATTERN=re.compile(r"^modlist(?:\(\d+\))?\.txt$",re.I)
OTHER_FILES={"plugins.txt","loadorder.txt","lockedorder.txt"}

def install_staged(source, dest):
    data=source.read_bytes()
    core._check_text(dest.name,data)
    if len(data)>core.MAX_TEXT:
        raise RuntimeError("Sorting file too large: "+str(source))
    if dest.exists():
        if dest.read_bytes()!=data:
            raise RuntimeError("Existing published sort version is immutable: "+str(dest))
        return False
    dest.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive creation ensures no authored release is overwritten.
    with dest.open("xb") as output:
        output.write(data)
    return True


def stage_one(root, inbox):
    root=Path(root)
    inbox=Path(inbox)
    date_name=inbox.name
    try:
        if len(date_name)!=8 or datetime.strptime(date_name,"%Y%m%d").strftime("%Y%m%d")!=date_name:
            raise ValueError()
    except ValueError:
        raise RuntimeError("Inbox folder must be a real YYYYMMDD date: "+date_name)
    present={p.name:p for p in inbox.iterdir() if p.is_file() and p.suffix.lower()==".txt"}
    modlist=[name for name in present if MODLIST_PATTERN.fullmatch(name)]
    if len(modlist)>1:
        raise RuntimeError("Multiple modlist files in "+str(inbox))
    unexpected=set(present)-set(modlist)-OTHER_FILES
    if unexpected:
        raise RuntimeError("Unexpected sorting text files: "+repr(sorted(unexpected)))
    if not modlist or not OTHER_FILES.issubset(present):
        print("INBOX_INCOMPLETE",date_name,sorted(present))
        return False
    original={
        "modlist.txt":present[modlist[0]],
        **{name:present[name] for name in sorted(OTHER_FILES)},
    }
    # Validate all members BEFORE touching release directory.
    for name,src in original.items():
        if src.is_symlink() or len(src.read_bytes())>core.MAX_TEXT:
            raise RuntimeError("Unsafe sorting source file: "+str(src))
        core._check_text(name,src.read_bytes())
    dests={
        "modlist.txt":root/"sort/releases"/("xingli-modlist-"+date_name+"-r01")/"v1.0.0"/"modlist.txt",
        **{name:root/"sort/releases"/("xingli-plugins-"+date_name+"-r01")/"v1.0.0"/name
           for name in OTHER_FILES},
    }
    for name,src in original.items():
        dest=dests[name]
        if dest.exists() and dest.read_bytes()!=src.read_bytes():
            raise RuntimeError("A published path already contains different sorting data: "+str(dest))
    changes=0
    for name,src in original.items():
        changes+=int(install_staged(src,dests[name]))
    print("INBOX_READY",date_name,"NEW_FILES",changes)
    return True


def main():
    inbox_root=ROOT/"sort/inbox"
    complete=0
    for folder in sorted(inbox_root.iterdir()) if inbox_root.exists() else []:
        if folder.is_dir():
            complete+=int(stage_one(ROOT,folder))
    if complete:
        manifest,records=publisher.generate(ROOT)
        if not records:
            raise RuntimeError("Completed upload did not produce sortable release records")
        path=ROOT/"sort/latest.json"
        path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print("SORT_MANIFEST_PUBLISHED",len(records))
    else:
        print("NO_COMPLETE_UPLOAD: no changes to sort/latest.json")


if __name__=="__main__":
    main()
