# coding: utf-8
"""Offline regression tests for MO2-native restore-visible sorting backups."""
from datetime import datetime
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("sort_update_core",ROOT/"development/v2.4.1/sort_update_core.py")
core=importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def payload(files):
    f=io.BytesIO()
    with zipfile.ZipFile(f,"w",zipfile.ZIP_DEFLATED) as z:
        for name,data in files.items():
            z.writestr(name,data)
    return f.getvalue()


def release(blob,kind="mods"):
    return {"id":"sorting-01","version":"1.2.0","kind":kind,
            "title":"Test","changelog":"test",
            "package":{"size":len(blob),"sha256":hashlib.sha256(blob).hexdigest(),
                       "mirrors":[{"name":"Gitee","url":"https://gitee.com/owner/example/raw/main/sort/releases/a.zip"}]}}


class Response:
    def __init__(self,data,url):
        self.data=io.BytesIO(data);self.url=url
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,n=-1):return self.data.read(n)
    def geturl(self):return self.url


class Organizer:
    def __init__(self,path):self.path=Path(path)
    def profilePath(self):return str(self.path)
    def profileName(self):return self.path.name
    def pluginDataPath(self):return str(self.path.parent/"plugin_data")


class Tests(unittest.TestCase):
    def test_empty_catalog(self):
        data=json.loads((ROOT/"sort/latest.json").read_text(encoding="utf-8"))
        self.assertEqual(core.validate_manifest(data),[])

    def test_manifest_rejects_wrong_identity(self):
        with self.assertRaises(core.SortError):
            core.validate_manifest({"schema":1,"product":"other","pack_id":"xingli-skyrim","releases":[]})

    def test_manifest_accepts_author_record(self):
        blob=payload({"modlist.txt":b"+B\r\n-A\r\n"})
        record=release(blob)
        data={"schema":1,"product":core.PRODUCT,"pack_id":core.PACK_ID,"releases":[record]}
        self.assertEqual(core.validate_manifest(data)[0]["kind"],"mods")

    def test_duplicate_or_insecure_mirror(self):
        blob=payload({"modlist.txt":b"+A\n"})
        record=release(blob)
        record["package"]["mirrors"][0]["url"]="http://example.com/download"
        with self.assertRaises(core.SortError):
            core.validate_manifest({"schema":1,"product":core.PRODUCT,"pack_id":core.PACK_ID,"releases":[record]})

    def test_modlist_preserves_mo2_reverse_order_bytes(self):
        lines=b"# comment\r\n+High Priority\r\n-Low Priority\r\n"
        contents=core.decode_order_archive(payload({"modlist.txt":lines}),"mods")
        self.assertEqual(contents["modlist.txt"],lines)

    def test_plugins_requires_all_three_for_mo2_restore(self):
        bundle={"plugins.txt":b"# plugins\r\n*A.esm\r\n",
                "loadorder.txt":b"A.esm\r\n",
                "lockedorder.txt":b""}
        self.assertEqual(set(core.decode_order_archive(payload(bundle),"plugins")),set(bundle))
        del bundle["lockedorder.txt"]
        with self.assertRaises(core.SortError):
            core.decode_order_archive(payload(bundle),"plugins")

    def test_zip_unsafe_or_extra_file_rejected(self):
        for name in ("../../modlist.txt","nested/modlist.txt","modlist.txt.exe"):
            with self.assertRaises(core.SortError):
                core.decode_order_archive(payload({name:b"+A\n"}),"mods")

    def test_duplicate_mod_names_rejected(self):
        with self.assertRaises(core.SortError):
            core.decode_order_archive(payload({"modlist.txt":b"+A\n-A\n"}),"mods")

    def test_download_sha_integrity(self):
        blob=payload({"modlist.txt":b"+A\n"})
        rec=release(blob)
        url=rec["package"]["mirrors"][0]["url"]
        with patch.object(core.urllib.request,"urlopen",return_value=Response(blob,url)):
            output=core.download_order(rec)
            self.assertEqual(output["modlist.txt"],b"+A\n")
        rec["package"]["sha256"]="0"*64
        with patch.object(core.urllib.request,"urlopen",return_value=Response(blob,url)):
            with self.assertRaises(core.SortError):
                core.download_order(rec)

    def test_left_backup_never_changes_live_and_allocates_sequence(self):
        with tempfile.TemporaryDirectory() as d:
            profile=Path(d)/"profiles"/"current"
            profile.mkdir(parents=True)
            live=profile/"modlist.txt"
            live.write_bytes(b"+LOCAL\n")
            fixed=datetime(2026,10,14,13,22,0)
            data={"modlist.txt":b"+REMOTE\n"}
            first=core.install_as_backups(profile,data,"mods",clock=fixed)
            second=core.install_as_backups(profile,data,"mods",clock=fixed)
            self.assertEqual(first["suffix"],"2026_10_14_13_22_00_Xingli_001")
            self.assertEqual(second["suffix"],"2026_10_14_13_22_00_Xingli_002")
            self.assertEqual(live.read_bytes(),b"+LOCAL\n")
            self.assertEqual(Path(first["paths"][0]).read_bytes(),b"+REMOTE\n")

    def test_right_trio_identical_suffix_and_existing_backups_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            profile=Path(d)
            (profile/"modlist.txt").write_bytes(b"+Local\n")
            files={"plugins.txt":b"*A.esm\n","loadorder.txt":b"A.esm\n",
                   "lockedorder.txt":b""}
            (profile/"plugins.txt").write_bytes(b"*Old.esp\n")
            existing=profile/"plugins.txt.2026_10_14_13_22_00_Xingli_001"
            existing.write_bytes(b"EXISTING")
            result=core.install_as_backups(profile,files,"plugins",clock=datetime(2026,10,14,13,22,0))
            self.assertEqual(result["suffix"],"2026_10_14_13_22_00_Xingli_002")
            self.assertEqual(len(result["paths"]),3)
            self.assertTrue(all(p.endswith(result["suffix"]) for p in result["paths"]))
            self.assertEqual(existing.read_bytes(),b"EXISTING")
            self.assertEqual((profile/"plugins.txt").read_bytes(),b"*Old.esp\n")

    def test_profile_path_must_match_current(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"myprofile"
            p.mkdir()
            organizer=Organizer(p)
            with self.assertRaises(core.SortError):
                core.profile_folder(organizer)
            (p/"modlist.txt").write_bytes(b"+A\n")
            self.assertEqual(core.profile_folder(organizer),p)

    def test_profile_receipts_are_isolated_and_mark_download(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"MyProfile"
            p.mkdir()
            (p/"modlist.txt").write_bytes(b"+A\n")
            organizer=Organizer(p)
            rec=release(payload({"modlist.txt":b"+A\n"}))
            self.assertEqual(len(core.unseen([rec],{})),1)
            receipt=core.receipt_path(organizer)
            core.save_receipt(receipt,rec)
            self.assertEqual(core.unseen([rec],core.read_receipts(receipt)),[])


if __name__=="__main__":
    unittest.main(verbosity=2)
