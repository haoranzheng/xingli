# coding=utf-8

import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
WORK = REPO / ".work"
ROOT = WORK / "xingli_Little_assistant"
BASE_ZIP = REPO / "update" / "releases" / "xingli_Little_assistant_v2.1.1_update.zip"
CANDIDATE = WORK / "xingli_Little_assistant_v2.2.0_candidate.zip"
REPORT = WORK / "phase-a-test-report.txt"
MANIFEST = WORK / "candidate-manifest.json"


def report(line: str) -> None:
    print(line)
    with REPORT.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def expand_and_transform() -> None:
    if WORK.exists():
        shutil.rmtree(str(WORK))
    WORK.mkdir(parents=True)
    with zipfile.ZipFile(str(BASE_ZIP)) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError("2.1.1 base ZIP integrity failure: " + bad)
        z.extractall(str(WORK))
    if not (ROOT / "consolidation_controller.py").is_file():
        raise RuntimeError("2.1.1 plugin root missing")
    subprocess.run(
        [sys.executable, str(HERE / "apply_phase_a.py"), str(ROOT)],
        check=True,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
    )
    report("Base expansion + Phase A transform: PASS")


def compatibility_gates() -> None:
    py_files = sorted(ROOT.rglob("*.py"))
    for path in py_files:
        source = path.read_text(encoding="utf-8-sig")
        try:
            ast.parse(source, filename=str(path), feature_version=(3, 8))
        except TypeError:
            ast.parse(source, filename=str(path), feature_version=8)
    report("Python 3.8 AST gate: PASS ({} files)".format(len(py_files)))

    forbidden = {"pluginList", "onPluginStateChanged", "onModStateChanged", "onPluginMoved", "waitForApplication"}
    findings = []
    for path in py_files:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
                if name in forbidden:
                    findings.append("{}:{}:{}".format(path.name, node.lineno, name))
    if findings:
        raise RuntimeError("forbidden MO2 runtime calls: " + ", ".join(findings))
    report("MO2 2.4.4 runtime API gate: PASS")

    controller = (ROOT / "consolidation_controller.py").read_text(encoding="utf-8-sig")
    required = [
        'DEFAULT_VERSION = "2.2.0"',
        "mobase.VersionInfo(2, 2, 0)",
        "instance_identity.resolve_instance_identity",
        "onboarding_state.should_show_welcome",
        "onboarding_state.mark_welcome_seen",
        "welcome_dialog.exec()\n        self._mark_welcome_seen()",
    ]
    missing = [x for x in required if x not in controller]
    if missing:
        raise RuntimeError("Phase A controller integration missing: {!r}".format(missing))
    if "_plugin_data_state_dir" in controller:
        raise RuntimeError("legacy in-tree onboarding state helper still present")
    start = controller.index("    def _modpack_root")
    end = controller.index("    def _consume_help_request", start)
    onboarding_block = controller[start:end]
    if "pluginDataPath" in onboarding_block or "_XingliState" in onboarding_block:
        raise RuntimeError("onboarding still references distributable-tree state")
    report("Phase A controller integration gate: PASS")


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def windows_acceptance() -> None:
    if os.name != "nt":
        raise RuntimeError("Windows acceptance must run on a Windows runner")

    identity = load_module("instance_identity")
    onboarding = load_module("onboarding_state")
    sandbox = Path(tempfile.mkdtemp(prefix="xingli-v220-test-"))
    previous_local = os.environ.get("LOCALAPPDATA")
    try:
        local = sandbox / "LocalAppData"
        os.environ["LOCALAPPDATA"] = str(local)

        original = sandbox / "XingliPack"
        original.mkdir()
        first_id, method = identity.resolve_instance_identity(str(original))
        if method != "win32-file-id":
            raise AssertionError("Windows test unexpectedly used identity fallback: " + method)
        same_id, _ = identity.resolve_instance_identity(str(original))
        assert same_id == first_id, "same directory changed instance id"
        report("A same physical directory identity: PASS")

        moved = sandbox / "XingliPackMoved"
        os.rename(str(original), str(moved))
        moved_id, moved_method = identity.resolve_instance_identity(str(moved))
        assert moved_method == "win32-file-id"
        assert moved_id == first_id, "moving same directory on volume changed instance id"
        report("B same-volume directory move identity: PASS")

        shutil.rmtree(str(moved))
        moved.mkdir()
        recreated_id, recreated_method = identity.resolve_instance_identity(str(moved))
        assert recreated_method == "win32-file-id"
        assert recreated_id != first_id, "delete/recreate at same path reused old instance id"
        report("C delete and recreate same path becomes new instance: PASS")

        second_copy = sandbox / "SecondExtraction"
        second_copy.mkdir()
        second_id, second_method = identity.resolve_instance_identity(str(second_copy))
        assert second_method == "win32-file-id"
        assert second_id not in (first_id, recreated_id), "new extraction directory reused an instance id"
        report("D separate extraction becomes new instance: PASS")

        assert onboarding.should_show_welcome(second_id) is True
        state = Path(onboarding.state_path(second_id))
        assert str(state).lower().startswith(str(local).lower()), "state is not under LOCALAPPDATA"
        assert not str(state).lower().startswith(str(second_copy).lower()), "state leaked into modpack tree"
        onboarding.mark_welcome_seen(second_id)
        assert state.is_file(), "onboarding state was not written"
        data = json.loads(state.read_text(encoding="utf-8"))
        assert data == {"schema": 1, "first_run_completed": True}
        assert onboarding.should_show_welcome(second_id) is False
        report("E first run then second launch state: PASS")

        state.unlink()
        assert onboarding.should_show_welcome(second_id) is True
        report("F deleting LocalAppData state restores first run: PASS")

        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("{broken json", encoding="utf-8")
        assert onboarding.should_show_welcome(second_id) is True
        assert Path(str(state) + ".corrupt").is_file(), "corrupt state was not quarantined"
        report("G corrupt onboarding state fails open to welcome: PASS")

        legacy = second_copy / "_XingliState" / "onboarding.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({"schema": 1, "welcome_seen": True}), encoding="utf-8")
        current = Path(onboarding.state_path(second_id))
        if current.exists():
            current.unlink()
        corrupt = Path(str(current) + ".corrupt")
        if corrupt.exists():
            corrupt.unlink()
        assert onboarding.should_show_welcome(second_id) is True, "legacy in-tree marker suppressed fresh onboarding"
        report("H legacy packaged welcome_seen cannot suppress new instance: PASS")

        version_independent_id, _ = identity.resolve_instance_identity(str(second_copy))
        assert version_independent_id == second_id, "instance identity unexpectedly depends on assistant version"
        report("I assistant version independence: PASS")
        report("Windows clean-first-run acceptance tests: 9/9 PASS")
    finally:
        if previous_local is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = previous_local
        shutil.rmtree(str(sandbox), ignore_errors=True)


def hygiene_gates() -> None:
    text_files = [
        p for p in ROOT.rglob("*")
        if p.is_file() and p.suffix.lower() in {".py", ".ps1", ".md", ".ini", ".json", ".txt", ".bat"}
    ]
    privacy_patterns = {
        "windows user path": re.compile(r"C:[\\/]Users[\\/]", re.I),
        "known author machine username": re.compile(r"\bm1660\b", re.I),
        "github token": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
        "openai style secret": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    }
    hits = []
    for path in text_files:
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        for label, pattern in privacy_patterns.items():
            if pattern.search(text):
                hits.append("{}:{}".format(label, path.relative_to(ROOT)))
    if hits:
        raise RuntimeError("privacy gate failed: " + ", ".join(hits))

    runtime_hits = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        parts = [x.lower() for x in rel.parts]
        name = path.name.lower()
        if any(x in {"logs", "__pycache__", "_xingliupdates", ".xingli_local"} for x in parts):
            runtime_hits.append(str(rel))
        elif name.endswith((".log", ".download", ".pyc")) or ".log." in name or name in {
            "pending_update.json", "pending_health_repair.json", "onboarding.json"
        }:
            runtime_hits.append(str(rel))
    if runtime_hits:
        raise RuntimeError("runtime state leaked into candidate: " + ", ".join(runtime_hits[:30]))
    report("Privacy/runtime-state hygiene gate: PASS")


def build_candidate() -> None:
    if CANDIDATE.exists():
        CANDIDATE.unlink()
    with zipfile.ZipFile(str(CANDIDATE), "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(x for x in ROOT.rglob("*") if x.is_file()):
            z.write(str(path), path.relative_to(WORK).as_posix())
    with zipfile.ZipFile(str(CANDIDATE)) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError("candidate ZIP integrity failure: " + bad)
        names = z.namelist()
        required = [
            "xingli_Little_assistant/instance_identity.py",
            "xingli_Little_assistant/onboarding_state.py",
            "xingli_Little_assistant/consolidation_controller.py",
            "xingli_Little_assistant/version.ini",
        ]
        missing = [x for x in required if x not in names]
        if missing:
            raise RuntimeError("candidate package missing: {!r}".format(missing))
        forbidden = [
            x for x in names
            if x.lower().endswith("/onboarding.json") or "/_xinglistate/" in x.replace("\\", "/").lower()
        ]
        if forbidden:
            raise RuntimeError("onboarding runtime state in candidate ZIP: {!r}".format(forbidden))

    data = CANDIDATE.read_bytes()
    manifest = {
        "version": "2.2.0-candidate",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "base": "2.1.1",
        "scope": "Clean First-Run Foundation only",
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report("Candidate ZIP integrity/release-content gate: PASS")
    report("Candidate SHA256: " + manifest["sha256"])
    report("Candidate size: {} bytes".format(manifest["size"]))


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    if REPORT.exists():
        REPORT.unlink()
    expand_and_transform()
    compatibility_gates()
    windows_acceptance()
    hygiene_gates()
    build_candidate()
    report("Xingli Assistant 2.2 Phase A CI: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
