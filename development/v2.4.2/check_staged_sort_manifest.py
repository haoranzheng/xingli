#!/usr/bin/env python3
# coding: utf-8
"""Check catalog SHA256/size against Git's staged blobs, not worktree files.

Git for Windows can silently normalize CRLF to LF during 'git add'.
The published URL serves the staged/committed blob, so checking only the
working tree allows an invalid catalog to ship.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "sort_update_core", ROOT / "development/v2.4.2/sort_update_core.py")
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


def verify_staged_catalog(root=ROOT):
    root = Path(root)
    manifest = root / "sort/latest.json"
    releases = core.validate_manifest(json.loads(manifest.read_text(encoding="utf-8-sig")))
    count = 0
    for release in releases:
        for name, descriptor in release.get("files", {}).items():
            path = "sort/releases/{}/v{}/{}".format(
                release["id"], release["version"], name)
            try:
                completed = subprocess.run(
                    ["git", "show", ":" + path],
                    cwd=str(root), check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except subprocess.CalledProcessError as exc:
                detail = exc.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError("Sorting file absent from Git index: {} ({})".format(
                    path, detail)) from exc
            data = completed.stdout
            actual_hash = hashlib.sha256(data).hexdigest()
            if len(data) != descriptor["size"] or actual_hash != descriptor["sha256"]:
                raise RuntimeError(
                    "Catalog mismatch after git add: {} "
                    "declared size={} sha256={}, staged size={} sha256={}".format(
                        path, descriptor["size"], descriptor["sha256"],
                        len(data), actual_hash))
            count += 1
    return count


if __name__ == "__main__":
    print("STAGED_SORT_CATALOG_VALID", verify_staged_catalog())
