# coding=utf-8
"""Add the PGPatcher BEES requirement notice to the validated 2.2 candidate.

PGPatcher's current requirements list Backported Extended ESL Support (BEES)
for Skyrim 1.5.97 through 1.6.659. Xingli's target is 1.5.97, so this notice is
part of the release UX rather than an optional documentation footnote.
"""

import ast
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
CANDIDATE = REPO / ".work" / "xingli_Little_assistant_v2.2.0_candidate.zip"
MANIFEST = REPO / ".work" / "candidate-manifest.json"
REPORT = REPO / ".work" / "phase-a-test-report.txt"
WORK = REPO / ".work_bees"
ROOT = WORK / "xingli_Little_assistant"
NOTICE = "Skyrim 1.5.97–1.6.659 还需要 Backported Extended ESL Support (BEES)。"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError("{}: expected exactly one match, got {}".format(label, count))
    return text.replace(old, new, 1)


def append_report(line: str) -> None:
    print(line)
    with REPORT.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def main() -> int:
    if not CANDIDATE.is_file():
        raise RuntimeError("validated 2.2 candidate missing")
    if WORK.exists():
        shutil.rmtree(str(WORK))
    WORK.mkdir(parents=True)

    with zipfile.ZipFile(str(CANDIDATE)) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError("candidate ZIP corrupt before BEES notice: " + bad)
        z.extractall(str(WORK))

    controller_path = ROOT / "consolidation_controller.py"
    controller = controller_path.read_text(encoding="utf-8-sig")
    controller = replace_once(
        controller,
        '            "TruePBR 需要 Community Shaders 的 PBR 功能。",\n',
        '            "TruePBR 需要 Community Shaders 的 PBR 功能。",\n'
        '            "Skyrim 1.5.97–1.6.659 还需要 Backported Extended ESL Support (BEES)。",\n',
        "PGPatcher launch BEES notice",
    )
    controller_path.write_text(controller, encoding="utf-8")

    tutorial_path = ROOT / "tutorial_data.py"
    tutorial = tutorial_path.read_text(encoding="utf-8-sig")
    tutorial = replace_once(
        tutorial,
        '                "TruePBR 材质需要 Community Shaders，并需要 PGPatcher 根据当前 MO2 的纹理、模型和插件栈生成适配结果。\\n\\n"\n',
        '                "TruePBR 材质需要 Community Shaders，并需要 PGPatcher 根据当前 MO2 的纹理、模型和插件栈生成适配结果。\\n"\n'
        '                "Skyrim 1.5.97–1.6.659 按 PGPatcher 官方要求还需要安装 Backported Extended ESL Support (BEES)。\\n\\n"\n',
        "PGPatcher tutorial BEES notice",
    )
    tutorial_path.write_text(tutorial, encoding="utf-8")

    changelog_path = ROOT / "CHANGELOG_v2.2.0.md"
    changelog = changelog_path.read_text(encoding="utf-8-sig")
    if "Backported Extended ESL Support" not in changelog:
        changelog = changelog.rstrip() + "\n- 对 Skyrim 1.5.97–1.6.659 明确提示 PGPatcher 官方要求 Backported Extended ESL Support (BEES)。\n"
    changelog_path.write_text(changelog, encoding="utf-8")

    # Revalidate the exact post-notice player source before replacing the ZIP.
    for path in sorted(ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        try:
            ast.parse(source, filename=str(path), feature_version=(3, 8))
        except TypeError:
            ast.parse(source, filename=str(path), feature_version=8)
    if NOTICE not in controller:
        raise RuntimeError("controller BEES notice missing")
    if "Backported Extended ESL Support (BEES)" not in tutorial:
        raise RuntimeError("tutorial BEES notice missing")

    CANDIDATE.unlink()
    with zipfile.ZipFile(str(CANDIDATE), "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(p for p in ROOT.rglob("*") if p.is_file()):
            z.write(str(path), path.relative_to(WORK).as_posix())
    with zipfile.ZipFile(str(CANDIDATE)) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError("candidate ZIP corrupt after BEES notice: " + bad)
        names = z.namelist()
        forbidden = [
            name for name in names
            if "/__pycache__/" in ("/" + name.replace("\\", "/").lower())
            or name.lower().endswith(".pyc")
            or name.lower().endswith("/onboarding.json")
        ]
        if forbidden:
            raise RuntimeError("runtime state leaked after BEES notice: {!r}".format(forbidden[:20]))

    data = CANDIDATE.read_bytes()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    manifest["size"] = len(data)
    manifest["sha256"] = hashlib.sha256(data).hexdigest()
    manifest["scope"] = "Clean First-Run + PGPatcher/TruePBR + BEES requirement guidance"
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    append_report("PG8 Skyrim 1.5.97–1.6.659 BEES requirement guidance: PASS")
    append_report("Post-BEES Python 3.8 AST + ZIP hygiene: PASS")
    append_report("Final Candidate SHA256 (BEES guidance): " + manifest["sha256"])
    append_report("Final Candidate size (BEES guidance): {} bytes".format(manifest["size"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
