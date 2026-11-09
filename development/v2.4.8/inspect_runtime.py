# coding: utf-8
import zipfile
from pathlib import Path
p=Path("update/releases/xingli_Little_assistant_v2.4.7_update.zip")
with zipfile.ZipFile(p) as z:
 for name in ("xingli_Little_assistant/consolidation_controller.py",
              "xingli_Little_assistant/__init__.py",
              "xingli_Little_assistant/sort_update_ui.py"):
  s=z.read(name).decode("utf-8-sig").splitlines()
  print("\n===== FILE",name,"LINES",len(s),"=====")
  needles=("sort_update","排序备份","排序更新","show_center","startup_check","def __init__","QAction","addAction","menu")
  hits=[i for i,line in enumerate(s) if any(x in line for x in needles)]
  spans=[]
  for i in hits:
   lo=max(0,i-8);hi=min(len(s),i+12)
   if any(lo<=b and hi>=a for a,b in spans): continue
   spans.append((lo,hi))
  for lo,hi in spans:
   print("---",lo+1,hi,"---")
   for n in range(lo,hi):print("%04d"%(n+1),s[n])
