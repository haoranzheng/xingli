# coding: utf-8
"""Offline regression tests: only unequivocal archive IDs may change MO2."""
import importlib.util
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("xingli_nexus_archive", HERE / "nexus_archive_metadata.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class MockGame:
    def gameNexusName(self):
        return "skyrimspecialedition"


class MockMod:
    def __init__(self, archive, nexus_id=10, url="", name="Test"):
        self.archive = archive
        self.nexus_id = nexus_id
        self.web_url = url
        self.mod_name = name
        self.actions = []

    def installationFile(self):
        return self.archive

    def nexusId(self):
        return self.nexus_id

    def url(self):
        return self.web_url

    def name(self):
        return self.mod_name

    def setNexusID(self, value):
        self.actions.append(("setNexusID", value))
        self.nexus_id = value

    def setUrl(self, value):
        self.actions.append(("setUrl", value))
        self.web_url = value


class MockList:
    def __init__(self, mods):
        self.mods = {mod.name(): mod for mod in mods}

    def allMods(self):
        return list(self.mods)

    def getMod(self, name):
        return self.mods.get(name)


class MockOrganizer:
    def __init__(self, mods):
        self.mods = MockList(mods)
        self.notifications = []

    def managedGame(self):
        return MockGame()

    def modList(self):
        return self.mods

    def modDataChanged(self, mod):
        self.notifications.append(mod.name())


class ArchiveMetadataTests(unittest.TestCase):
    examples = (
        ("LOD Model Library - FOMOD 87521 1.8.3 2026-06-14T01-13Z cE8hkAahF.7z", 87521),
        ("TreeLODGen 195679 1.0.6 2026-10-19T04-53Z VMSnJrggR.zip", 195679),
    )

    def test_timestamp_archive_examples(self):
        for archive, expected in self.examples:
            with self.subTest(archive=archive):
                self.assertEqual(core.parse_archive_identity(archive)[0], expected)

    def test_legacy_format(self):
        self.assertEqual(core.parse_archive_identity("LOD Model Library - FOMOD-87521-1-8-3-1712345678.7z"),
                         (87521, "1.8.3"))

    def test_strict_parser_rejects_unrelated_numbers(self):
        bad = (
            "TreeLODGen 195679 1.0.6.7z",  # missing timestamp + slug
            "My Patch 10.1 2026-10-19.7z",
            "Random10 Something 120.7z",
            "TreeLODGen 195679 1.0.6 2026-10-19T04-53Z VMSnJrggR.exe",
            "TreeLODGen 195679 1.0.6 2026-10-19T04-53Z ?unsafe?.zip",
            "SkyUI_6.11_trg5df.zip",  # Nexus itself no longer promises ID in names
        )
        for item in bad:
            with self.subTest(item=item):
                self.assertIsNone(core.parse_archive_identity(item))

    def test_corrects_buggy_id_ten_and_standard_url(self):
        archive, expected_id = self.examples[0]
        old = "https://www.nexusmods.com/skyrimspecialedition/mods/10"
        mod = MockMod("C:/MO2/downloads/" + archive, 10, old)
        organizer = MockOrganizer([mod])
        proposals = core.find_candidates(organizer)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0][1]["mod_id"], expected_id)
        self.assertEqual(mod.actions, [])  # Preview never writes
        self.assertTrue(core.apply(organizer, mod, proposals[0][1]))
        self.assertEqual(mod.nexusId(), expected_id)
        self.assertEqual(mod.url(), "https://www.nexusmods.com/skyrimspecialedition/mods/87521")
        self.assertEqual(organizer.notifications, ["Test"])

    def test_corrects_second_sample_and_blank_link(self):
        mod = MockMod("D:\\MO2\\downloads\\" + self.examples[1][0], 10, "")
        organizer = MockOrganizer([mod])
        proposal = core.propose(mod, core.nexus_game_domain(organizer))
        self.assertEqual(proposal["mod_id"], 195679)
        self.assertTrue(core.apply(organizer, mod, proposal))
        self.assertIn(("setNexusID", 195679), mod.actions)

    def test_does_not_overwrite_other_positive_id(self):
        mod = MockMod(self.examples[0][0], 87522, "")
        self.assertIsNone(core.propose(mod, "skyrimspecialedition"))
        self.assertEqual(mod.actions, [])

    def test_does_not_rewrite_custom_url(self):
        mod = MockMod(self.examples[0][0], 10, "https://example.com/my-guide")
        organizer = MockOrganizer([mod])
        proposal = core.propose(mod, "skyrimspecialedition")
        self.assertTrue(proposal["change_id"])
        self.assertFalse(proposal["change_url"])
        self.assertTrue(core.apply(organizer, mod, proposal))
        self.assertEqual(mod.url(), "https://example.com/my-guide")

    def test_valid_id_ten_not_touched(self):
        archive = "Real Mod 10 1.0.0 2026-10-19T04-53Z VMSnJrggR.7z"
        mod = MockMod(archive, 10, "https://www.nexusmods.com/skyrimspecialedition/mods/10")
        self.assertIsNone(core.propose(mod, "skyrimspecialedition"))

    def test_does_not_follow_untrusted_domain(self):
        mod = MockMod(self.examples[0][0], 10, "https://example.com/redirect/10")
        organizer = MockOrganizer([mod])
        proposal = core.propose(mod, "skyrimspecialedition")
        self.assertFalse(proposal["change_url"])
        self.assertTrue(core.apply(organizer, mod, proposal))
        self.assertEqual(mod.web_url, "https://example.com/redirect/10")

    def test_stale_proposal_never_writes(self):
        mod = MockMod(self.examples[0][0], 10, "")
        organizer = MockOrganizer([mod])
        proposal = core.propose(mod, "skyrimspecialedition")
        mod.nexus_id = 87522
        self.assertFalse(core.apply(organizer, mod, proposal))
        self.assertFalse(mod.actions)


if __name__ == "__main__":
    unittest.main(verbosity=2)
