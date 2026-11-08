from pathlib import Path
import zipfile

zip_path = Path("update/releases/xingli_Little_assistant_v2.4.5_update.zip")
if not zip_path.is_file():
    raise SystemExit("baseline ZIP missing")
with zipfile.ZipFile(zip_path) as z:
    names = z.namelist()
    print("=== MATCHING FILE NAMES ===")
    for name in names:
        if any(k in name.lower() for k in ("modpack", "version", "identity", "controller")):
            print(name)
    print("=== MODULES WITH MODPACK/VERSION STATE ===")
    for name in names:
        if not name.endswith(".py"):
            continue
        data = z.read(name)
        try:
            source = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            continue
        low = source.lower()
        if ("modpack" in low or "installed_version" in low or
                "pack_id" in low or "version.ini" in low):
            print("\\n===== FILE: " + name + " =====")
            print(source)
