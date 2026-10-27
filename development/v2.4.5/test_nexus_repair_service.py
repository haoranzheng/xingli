# coding: utf-8
"""Regressions for lifecycle-safe MO2 metadata correction."""
import importlib
import importlib.util
import sys
import types
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
PACKAGE = "xingli_v245_test_pkg"
pkg = types.ModuleType(PACKAGE)
pkg.__path__ = [str(HERE)]
sys.modules[PACKAGE] = pkg
legacy_spec = importlib.util.spec_from_file_location(
    PACKAGE + ".nexus_archive_metadata",
    HERE.parents[0] / "v2.4.4" / "nexus_archive_metadata.py")
legacy = importlib.util.module_from_spec(legacy_spec)
sys.modules[legacy_spec.name] = legacy
legacy_spec.loader.exec_module(legacy)
service = importlib.import_module(PACKAGE + ".nexus_repair_service")


class Mod:
    def __init__(self, name, archive="", mid=10, url=""):
        self.mod_name = name
        self.archive = archive
        self.mid = mid
        self.web_url = url
        self.writes = []

    def name(self):
        return self.mod_name

    def installationFile(self):
        return self.archive

    def nexusId(self):
        return self.mid

    def url(self):
        return self.web_url

    def setNexusID(self, id):
        self.writes.append(("id", id))
        self.mid = id

    def setUrl(self, url):
        self.writes.append(("url", url))
        self.web_url = url


class ModList:
    def __init__(self, mods):
        self.mods = {x.name(): x for x in mods}
        self.lookups = 0

    def allMods(self):
        return list(self.mods)

    def getMod(self, name):
        self.lookups += 1
        return self.mods.get(name)


class Game:
    def gameNexusName(self):
        return "skyrimspecialedition"


class Organizer:
    def __init__(self, mods):
        self.list = ModList(mods)

    def modList(self):
        return self.list

    def managedGame(self):
        return Game()


class ServiceTests(unittest.TestCase):
    one = "LOD Model Library - FOMOD 87521 1.8.3 2026-06-14T01-13Z cE8hkAahF.7z"
    two = "TreeLODGen 195679 1.0.6 2026-10-19T04-53Z VMSnJrggR.7z"

    def test_preview_stores_no_native_wrappers(self):
        mod = Mod("LOD", self.one)
        organizer = Organizer([mod])
        preview = service.find_candidates(organizer)
        self.assertEqual(len(preview), 1)
        self.assertEqual(preview[0]["mod_id"], 87521)
        self.assertTrue(all(not isinstance(v, Mod) for v in preview[0].values()))
        before = organizer.list.lookups
        self.assertTrue(service.apply_by_name(organizer, "LOD", preview[0]))
        self.assertGreater(organizer.list.lookups, before)
        self.assertEqual(mod.mid, 87521)

    def test_install_hint_works_when_mo2_archive_field_is_blank(self):
        mod = Mod("TreeLOD", "", 10)
        organizer = Organizer([mod])
        self.assertIsNone(service.propose(organizer, mod))
        self.assertEqual(service.apply_new_install(organizer, "TreeLOD", self.two),
                         "applied:195679")
        self.assertEqual(mod.mid, 195679)
        self.assertFalse(service.find_candidates(organizer))

    def test_no_random_guess_from_mod_name(self):
        mod = Mod("LOD 87521", "", 10)
        self.assertEqual(service.apply_new_install(Organizer([mod]), mod.name()), "no-installation-file")
        self.assertEqual(mod.mid, 10)

    def test_good_id_preserved(self):
        mod = Mod("LOD", self.one, 87522)
        organizer = Organizer([mod])
        self.assertEqual(service.apply_new_install(organizer, "LOD"), "existing-id-or-url-protected")
        self.assertFalse(mod.writes)

    def test_stale_preview_does_not_write(self):
        mod = Mod("LOD", self.one)
        organizer = Organizer([mod])
        preview = service.find_candidates(organizer)[0]
        mod.mid = 87522
        self.assertFalse(service.apply_by_name(organizer, "LOD", preview))
        self.assertFalse(mod.writes)

    def test_custom_link_preserved_and_no_mod_data_changed(self):
        mod = Mod("LOD", self.one, 10, "https://example.org/project")
        organizer = Organizer([mod])
        proposal = service.find_candidates(organizer)[0]
        self.assertTrue(service.apply_by_name(organizer, "LOD", proposal))
        self.assertEqual(mod.mid, 87521)
        self.assertEqual(mod.web_url, "https://example.org/project")


if __name__ == "__main__":
    unittest.main(verbosity=2)
