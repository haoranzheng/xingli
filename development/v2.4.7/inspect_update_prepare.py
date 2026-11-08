# coding: utf-8
import sys, re, zipfile
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
zpath=Path("update/releases/xingli_Little_assistant_v2.4.6_update.zip")
with zipfile.ZipFile(zpath) as z:
 print("ARCHIVE_OK", z.testzip(), len(z.namelist()))
 for p in z.namelist():
  if any(x in p.lower() for x in ("updat", "version", "install")): print("ZIPFILE",p)
 for p in z.namelist():
  if not p.endswith(".py"): continue
  s=z.read(p).decode("utf-8-sig",errors="replace")
  lines=s.splitlines()
  hits=[i for i,line in enumerate(lines) if any(x in line for x in ("更新包版本信息无效", "版本信息无效", "更新包", "prepare_update", "prepare_install", "package_version", "version.ini", "local_version"))]
  if not hits: continue
  print("=== MODULE",p,len(lines),"LINES HITS",hits[:100],"===")
  spans=[]
  for i in hits:
   if any(abs(i-j)<10 for j in spans): continue
   spans.append(i)
   lo=max(0,i-22); hi=min(len(lines),i+32)
   print("--- BLOCK",lo+1,hi,"---")
   for idx in range(lo,hi): print(str(idx+1)+":",lines[idx])
 for p in ("xingli_Little_assistant/version.ini","xingli_Little_assistant/modpack_release.json"):
  print("=== CONTENT",p,"===");print(z.read(p).decode("utf-8-sig"))
