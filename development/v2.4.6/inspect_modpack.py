from pathlib import Path
import sys
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
zip_path = Path("update/releases/xingli_Little_assistant_v2.4.5_update.zip")
if not zip_path.is_file():
    raise SystemExit("baseline ZIP missing")
with zipfile.ZipFile(zip_path) as z:
    names = z.namelist()
    print("=== MATCHING FILE NAMES ===")
    for name in names:
        if any(k in name.lower() for k in ("modpack", "version", "identity", "controller", "mainmenu_sync")):
            print(name)
    targets = [
        "xingli_Little_assistant/modpack_release.json",
        "xingli_Little_assistant/modpack_update_sources.json",
        "xingli_Little_assistant/mainmenu_sync.py",
        "xingli_Little_assistant/modpack_updater.py",
        "xingli_Little_assistant/instance_identity.py",
    ]
    print("=== TARGET MODULES ===")
    for name in targets:
        if name not in names:
            continue
        print("\\n===== FILE: " + name + " =====")
        raw = z.read(name)
        try:
            print(raw.decode("utf-8-sig"))
        except UnicodeDecodeError:
            print(raw[:2000].decode("utf-8", errors="replace"))
