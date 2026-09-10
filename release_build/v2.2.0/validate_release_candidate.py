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
import types
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PHASE_A_CANDIDATE = REPO / '.work' / 'xingli_Little_assistant_v2.2.0_candidate.zip'
REPORT = REPO / '.work' / 'phase-a-test-report.txt'
MANIFEST = REPO / '.work' / 'candidate-manifest.json'
FINAL_WORK = REPO / '.work_pgpatcher'
FINAL_ROOT = FINAL_WORK / 'xingli_Little_assistant'


def report(line: str) -> None:
    print(line)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.open('a', encoding='utf-8') as handle:
        handle.write(line + '\n')


def run_phase_a() -> None:
    subprocess.run(
        [sys.executable, str(HERE / 'validate_and_build.py')],
        check=True,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
    )
    if not PHASE_A_CANDIDATE.is_file():
        raise RuntimeError('Phase A candidate missing')
    report('Phase A regression suite preserved: PASS')


def apply_pg_support() -> None:
    if FINAL_WORK.exists():
        shutil.rmtree(str(FINAL_WORK))
    FINAL_WORK.mkdir(parents=True)
    with zipfile.ZipFile(str(PHASE_A_CANDIDATE)) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError('Phase A candidate ZIP corrupt: ' + bad)
        z.extractall(str(FINAL_WORK))
    subprocess.run(
        [sys.executable, str(HERE / 'apply_pgpatcher_support.py'), str(FINAL_ROOT)],
        check=True,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
    )
    report('PGPatcher/TruePBR transform: PASS')


def static_gates() -> None:
    py_files = sorted(FINAL_ROOT.rglob('*.py'))
    for path in py_files:
        source = path.read_text(encoding='utf-8-sig')
        try:
            ast.parse(source, filename=str(path), feature_version=(3, 8))
        except TypeError:
            ast.parse(source, filename=str(path), feature_version=8)
    report('Final Python 3.8 AST gate: PASS ({} files)'.format(len(py_files)))

    forbidden = {'pluginList', 'onPluginStateChanged', 'onModStateChanged', 'onPluginMoved', 'waitForApplication'}
    findings = []
    for path in py_files:
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else '')
                if name in forbidden:
                    findings.append('{}:{}:{}'.format(path.name, node.lineno, name))
    if findings:
        raise RuntimeError('forbidden MO2 runtime calls: ' + ', '.join(findings))
    report('Final MO2 2.4.4 runtime API gate: PASS')

    controller = (FINAL_ROOT / 'consolidation_controller.py').read_text(encoding='utf-8-sig')
    user = (FINAL_ROOT / 'user_assistant.py').read_text(encoding='utf-8-sig')
    tutorial = (FINAL_ROOT / 'tutorial_data.py').read_text(encoding='utf-8-sig')
    adapter = (FINAL_ROOT / 'pgpatcher_adapter.py').read_text(encoding='utf-8-sig')
    changelog = (FINAL_ROOT / 'CHANGELOG_v2.2.0.md').read_text(encoding='utf-8-sig')
    gates = {
        'controller': (controller, ['from . import pgpatcher_adapter', 'PBR / PGPatcher', 'def launch_pgpatcher', 'pgpatcher_adapter.launch(self.organizer, exe)']),
        'user assistant': (user, ['🧱 PBR / PGPatcher', 'pgpatcher_adapter.inspect(self.organizer)']),
        'tutorial': (tutorial, ['PBR / PGPatcher：什么时候必须运行？', 'BodySlide → PGPatcher → TexGen → DynDOLOD', 'Community Shaders']),
        'adapter': (adapter, ['PGPatcher.exe', 'PGPatcher_Output', 'DynDOLOD', 'organizer.startApplication(exe, [], cwd)', 'set_active(names, False, verify=False)']),
        'changelog': (changelog, ['Clean First-Run + PGPatcher / TruePBR Support', 'LocalAppData', 'PBR / PGPatcher']),
    }
    for label, (text, tokens) in gates.items():
        missing = [token for token in tokens if token not in text]
        if missing:
            raise RuntimeError('{} feature gate missing: {!r}'.format(label, missing))
    report('PGPatcher integration feature gate: PASS')


def _load_test_modules():
    package_name = '_xingli_pg_test'
    pkg = types.ModuleType(package_name)
    pkg.__path__ = [str(FINAL_ROOT)]
    sys.modules[package_name] = pkg
    previous_mobase = sys.modules.get('mobase')
    sys.modules['mobase'] = types.ModuleType('mobase')

    def load(name: str):
        fq = package_name + '.' + name
        spec = importlib.util.spec_from_file_location(fq, str(FINAL_ROOT / (name + '.py')))
        module = importlib.util.module_from_spec(spec)
        module.__package__ = package_name
        sys.modules[fq] = module
        spec.loader.exec_module(module)
        return module

    load('mod_state_service')
    adapter = load('pgpatcher_adapter')
    return package_name, previous_mobase, adapter


def _cleanup_test_modules(package_name: str, previous_mobase) -> None:
    for key in list(sys.modules):
        if key == package_name or key.startswith(package_name + '.'):
            sys.modules.pop(key, None)
    if previous_mobase is None:
        sys.modules.pop('mobase', None)
    else:
        sys.modules['mobase'] = previous_mobase


def pgpatcher_acceptance() -> None:
    package_name, previous_mobase, adapter = _load_test_modules()

    class ModObject:
        def __init__(self, path):
            self._path = path
        def absolutePath(self):
            return self._path

    class ModList:
        def __init__(self, root):
            self.mods = {
                'PGPatcher': {'path': str(root / 'mods' / 'PGPatcher'), 'active': False},
                'PGPatcher_Output': {'path': str(root / 'mods' / 'PGPatcher_Output'), 'active': True},
                'DynDOLOD_Output': {'path': str(root / 'mods' / 'DynDOLOD_Output'), 'active': True},
                'TexGen Output': {'path': str(root / 'mods' / 'TexGen Output'), 'active': True},
            }
            self.set_calls = []
        def allModsByProfilePriority(self):
            return list(self.mods)
        def allMods(self):
            return list(self.mods)
        def state(self, name):
            return 0x2 if self.mods[name]['active'] else 0x1
        def displayName(self, name):
            return name
        def priority(self, name):
            return list(self.mods).index(name)
        def getMod(self, name):
            return ModObject(self.mods[name]['path'])
        def setActive(self, name, active):
            self.set_calls.append((name, bool(active)))
            self.mods[name]['active'] = bool(active)
            return True

    class Organizer:
        def __init__(self, root):
            self.root = root
            self.ml = ModList(root)
            self.launch_calls = []
        def modList(self):
            return self.ml
        def modsPath(self):
            return str(self.root / 'mods')
        def basePath(self):
            return str(self.root / 'MO2')
        def startApplication(self, *args):
            self.launch_calls.append(args)
            return 123

    sandbox = Path(tempfile.mkdtemp(prefix='xingli-pgpatcher-test-'))
    try:
        (sandbox / 'mods' / 'PGPatcher').mkdir(parents=True)
        (sandbox / 'mods' / 'PGPatcher' / 'PGPatcher.exe').write_bytes(b'MZ')
        for name in ['PGPatcher_Output', 'DynDOLOD_Output', 'TexGen Output']:
            (sandbox / 'mods' / name).mkdir()
        (sandbox / 'MO2').mkdir()
        (sandbox / 'MO2' / 'ModOrganizer.ini').write_text('[General]\n', encoding='utf-8')

        organizer = Organizer(sandbox)
        status = adapter.inspect(organizer)
        assert status['exe'].lower().endswith('pgpatcher.exe')
        assert status['owner_mod'] == 'PGPatcher'
        assert status['instance_ini_ok'] is True
        report('PG1 executable + MO2 instance discovery: PASS')

        assert status['active_outputs'] == ['PGPatcher_Output']
        assert status['active_dyndolod'] == ['DynDOLOD_Output']
        assert status['active_texgen'] == ['TexGen Output']
        report('PG2 output / DynDOLOD / TexGen state discovery: PASS')

        assert status['recommended_output'] == str((sandbox / 'mods' / 'PGPatcher_Output').resolve())
        report('PG3 recommended output path: PASS')

        ok, failures = adapter.disable_output_mods(organizer, status['active_outputs'])
        assert ok and not failures
        assert organizer.ml.set_calls == [('PGPatcher_Output', False)]
        assert organizer.ml.mods['PGPatcher_Output']['active'] is False
        report('PG4 safe scalar output disable: PASS')

        adapter.launch(organizer, status['exe'])
        assert len(organizer.launch_calls) == 1
        call = organizer.launch_calls[0]
        assert len(call) == 3, 'PGPatcher must not force a separate MO2 profile'
        assert call[1] == []
        assert os.path.normcase(call[2]) == os.path.normcase(str(sandbox / 'mods' / 'PGPatcher'))
        report('PG5 MO2 VFS launch without profile override: PASS')

        os.remove(str(sandbox / 'mods' / 'PGPatcher' / 'PGPatcher.exe'))
        missing = adapter.find_pgpatcher(organizer)
        assert missing == (None, None)
        report('PG6 missing executable fails closed to guidance: PASS')

        external = sandbox / 'Tools' / 'PGPatcher'
        external.mkdir(parents=True)
        (external / 'PGPatcher.exe').write_bytes(b'MZ')
        found, owner = adapter.find_pgpatcher(organizer)
        assert found and os.path.normcase(found) == os.path.normcase(str(external / 'PGPatcher.exe'))
        assert owner is None
        report('PG7 external/common placement discovery: PASS')
        report('PGPatcher adapter acceptance tests: 7/7 PASS')
    finally:
        shutil.rmtree(str(sandbox), ignore_errors=True)
        _cleanup_test_modules(package_name, previous_mobase)


def hygiene_gates() -> None:
    text_files = [
        p for p in FINAL_ROOT.rglob('*')
        if p.is_file() and p.suffix.lower() in {'.py', '.ps1', '.md', '.ini', '.json', '.txt', '.bat'}
    ]
    privacy_patterns = {
        'windows user path': re.compile(r'C:[\\/]Users[\\/]', re.I),
        'known author machine username': re.compile(r'\bm1660\b', re.I),
        'github token': re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})'),
        'openai style secret': re.compile(r'\bsk-[A-Za-z0-9_-]{20,}'),
    }
    hits = []
    for path in text_files:
        text = path.read_text(encoding='utf-8-sig', errors='ignore')
        for label, pattern in privacy_patterns.items():
            if pattern.search(text):
                hits.append('{}:{}'.format(label, path.relative_to(FINAL_ROOT)))
    if hits:
        raise RuntimeError('privacy gate failed: ' + ', '.join(hits))

    runtime_hits = []
    for path in FINAL_ROOT.rglob('*'):
        if not path.is_file():
            continue
        rel = path.relative_to(FINAL_ROOT)
        parts = [x.lower() for x in rel.parts]
        name = path.name.lower()
        if any(x in {'logs', '__pycache__', '_xingliupdates', '.xingli_local'} for x in parts):
            runtime_hits.append(str(rel))
        elif name.endswith(('.log', '.download', '.pyc')) or '.log.' in name or name in {
            'pending_update.json', 'pending_health_repair.json', 'onboarding.json'
        }:
            runtime_hits.append(str(rel))
    if runtime_hits:
        raise RuntimeError('runtime state leaked into final candidate: ' + ', '.join(runtime_hits[:30]))
    report('Final privacy/runtime-state hygiene gate: PASS')


def build_final_candidate() -> None:
    if PHASE_A_CANDIDATE.exists():
        PHASE_A_CANDIDATE.unlink()
    with zipfile.ZipFile(str(PHASE_A_CANDIDATE), 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(x for x in FINAL_ROOT.rglob('*') if x.is_file()):
            z.write(str(path), path.relative_to(FINAL_WORK).as_posix())
    with zipfile.ZipFile(str(PHASE_A_CANDIDATE)) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError('final candidate ZIP integrity failure: ' + bad)
        names = z.namelist()
        required = [
            'xingli_Little_assistant/instance_identity.py',
            'xingli_Little_assistant/onboarding_state.py',
            'xingli_Little_assistant/pgpatcher_adapter.py',
            'xingli_Little_assistant/CHANGELOG_v2.2.0.md',
            'xingli_Little_assistant/consolidation_controller.py',
            'xingli_Little_assistant/version.ini',
        ]
        missing = [x for x in required if x not in names]
        if missing:
            raise RuntimeError('final candidate package missing: {!r}'.format(missing))
        forbidden = [x for x in names if '/__pycache__/' in ('/' + x.lower()) or x.lower().endswith('.pyc')]
        if forbidden:
            raise RuntimeError('bytecode/runtime artifacts in final candidate: {!r}'.format(forbidden[:20]))

    data = PHASE_A_CANDIDATE.read_bytes()
    manifest = {
        'version': '2.2.0-candidate',
        'size': len(data),
        'sha256': hashlib.sha256(data).hexdigest(),
        'base': '2.1.1',
        'scope': 'Clean First-Run + PGPatcher/TruePBR integration',
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    report('Final candidate ZIP integrity gate: PASS')
    report('Final Candidate SHA256: ' + manifest['sha256'])
    report('Final Candidate size: {} bytes'.format(manifest['size']))


def main() -> int:
    run_phase_a()
    apply_pg_support()
    static_gates()
    pgpatcher_acceptance()
    hygiene_gates()
    build_final_candidate()
    report('Xingli Assistant 2.2 release candidate CI: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
