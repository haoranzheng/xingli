#!/usr/bin/env python3
# coding: utf-8
"""Offline tests: no ZIP needed and no live MO2 sorting file is overwritten."""
from datetime import datetime
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
def load(name,file):
    s=importlib.util.spec_from_file_location(name,ROOT/"development/v2.4.2"/file)
    obj=importlib.util.module_from_spec(s)
    s.loader.exec_module(obj)
    return obj
core=load("sort_update_core", "sort_update_core.py")
publisher=load("publish_raw_sort","publish_raw_sort.py")


def record(file_contents,kind):
    slug="xingli-left-20261014" if kind=="mods" else "xingli-right-20261014"
    files={}
    for name,blob in file_contents.items():
        files[name]={"size":len(blob),"sha256":hashlib.sha256(blob).hexdigest(),
                     "mirrors":[{"name":"Github", "url":"https://raw.githubusercontent.com/haoranzheng/xingli/main/"+name}]}
    return {"id":slug,"version":"1.0.0","kind":kind,"files":files,
            "title":"test"}


class Response:
    def __init__(self,body,url):self.stream=io.BytesIO(body);self.url=url
    def __enter__(self):return self
    def __exit__(self,*args):return None
    def geturl(self):return self.url
    def read(self,n=-1):return self.stream.read(n)


class Tests(unittest.TestCase):
    def test_raw_left_record(self):
        data={"modlist.txt":b"# generated\r\n+A\r\n-B\r\n"}
        entry=record(data,"mods")
        values={"schema":1,"product":core.PRODUCT,"pack_id":core.PACK_ID,"releases":[entry]}
        self.assertEqual(core.validate_manifest(values)[0]["files"]["modlist.txt"]["size"],len(data["modlist.txt"]))

    def test_raw_right_requires_full_trio(self):
        data={"plugins.txt":b"*A.esm\n","loadorder.txt":b"A.esm\n"}
        entry=record(data,"plugins")
        with self.assertRaises(core.SortError):
            core.validate_manifest({"schema":1,"product":core.PRODUCT,"pack_id":core.PACK_ID,"releases":[entry]})

    def test_bad_sha_rejected(self):
        record0=record({"modlist.txt":b"+A\n"},"mods")
        record0["files"]["modlist.txt"]["sha256"]="0"*64
        link=record0["files"]["modlist.txt"]["mirrors"][0]["url"]
        with patch.object(core.urllib.request,"urlopen",return_value=Response(b"+A\n",link)):
            with self.assertRaises(core.SortError):
                core.download_order(record0)

    def test_download_direct_three_files(self):
        b={"plugins.txt":b"*A.esp\r\n","loadorder.txt":b"A.esp\r\n",
           "lockedorder.txt":b"# MO2\r\n"}
        record0=record(b,"plugins")
        urls={rec["mirrors"][0]["url"]:b[name] for name,rec in record0["files"].items()}
        def urlopen(req,timeout):
            url=req.full_url
            return Response(urls[url],url)
        with patch.object(core.urllib.request,"urlopen",side_effect=urlopen):
            self.assertEqual(core.download_order(record0),b)

    def test_partial_right_download_fails_without_writing(self):
        b={"plugins.txt":b"*A.esp\r\n","loadorder.txt":b"A.esp\r\n",
           "lockedorder.txt":b"# MO2\r\n"}
        item=record(b,"plugins")
        urls={rec["mirrors"][0]["url"]:b[name] for name,rec in item["files"].items()}
        urls[item["files"]["lockedorder.txt"]["mirrors"][0]["url"]]=b"BROKEN"
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)
            (path/"modlist.txt").write_bytes(b"+KEEP\n")
            def urlopen(req,timeout):
                return Response(urls[req.full_url],req.full_url)
            with patch.object(core.urllib.request,"urlopen",side_effect=urlopen):
                with self.assertRaises(core.SortError):
                    data=core.download_order(item)
                    core.install_as_backups(path,data,"plugins")
            self.assertEqual(sorted(f.name for f in path.iterdir()),["modlist.txt"])

    def test_raw_preserve_live_collision(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            (p/"modlist.txt").write_bytes(b"+ORIGINAL\n")
            t=datetime(2026,10,14,14,27,0)
            first=core.install_as_backups(p,{"modlist.txt":b"+REMOTE\n"},"mods",clock=t)
            second=core.install_as_backups(p,{"modlist.txt":b"+REMOTE\n"},"mods",clock=t)
            self.assertTrue(first["suffix"].endswith("_001"))
            self.assertTrue(second["suffix"].endswith("_002"))
            self.assertEqual((p/"modlist.txt").read_bytes(),b"+ORIGINAL\n")

    def test_publisher_requires_complete_trio(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            base=root/"sort/releases/xingli-right-20261014/v1.0.0"
            base.mkdir(parents=True)
            (base/"plugins.txt").write_bytes(b"*A.esp\n")
            self.assertEqual(publisher.gather(root),[])
            (base/"loadorder.txt").write_bytes(b"A.esp\n")
            (base/"lockedorder.txt").write_bytes(b"# generated\n")
            outputs=publisher.gather(root)
            self.assertEqual(len(outputs),1)
            self.assertEqual(set(outputs[0]["files"]),set(core.KINDS["plugins"]))
            self.assertEqual(outputs[0]["files"]["lockedorder.txt"]["sha256"],
                             hashlib.sha256(b"# generated\n").hexdigest())

    def test_publisher_github_paths_not_archives(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            p=root/"sort/releases/xingli-left-20261014/v1.0.0"
            p.mkdir(parents=True)
            (p/"modlist.txt").write_bytes(b"+A\n")
            items=publisher.gather(root)
            self.assertEqual(len(items),1)
            link=items[0]["files"]["modlist.txt"]["mirrors"][1]["url"]
            self.assertTrue(link.endswith("/sort/releases/xingli-left-20261014/v1.0.0/modlist.txt"))

    def test_legacy_archive_backward_compatibility(self):
        import zipfile
        f=io.BytesIO()
        with zipfile.ZipFile(f,"w") as z:
            z.writestr("modlist.txt",b"+A\n")
        zdata=f.getvalue()
        item=record({"modlist.txt":b"+A\n"},"mods")
        del item["files"]
        item["package"]={"size":len(zdata),"sha256":hashlib.sha256(zdata).hexdigest(),
                         "mirrors":[{"name":"raw","url":"https://raw.githubusercontent.com/a.zip"}]}
        v={"schema":1,"product":core.PRODUCT,"pack_id":core.PACK_ID,"releases":[item]}
        self.assertIn("package",core.validate_manifest(v)[0])

    def test_no_unsafe_urls(self):
        record0=record({"modlist.txt":b"+A\n"},"mods")
        record0["files"]["modlist.txt"]["mirrors"][0]["url"]="http://localhost/file.txt"
        with self.assertRaises(core.SortError):
            core.validate_manifest({"schema":1,"product":core.PRODUCT,
                                    "pack_id":core.PACK_ID,"releases":[record0]})


if __name__=="__main__":
    unittest.main(verbosity=2)
