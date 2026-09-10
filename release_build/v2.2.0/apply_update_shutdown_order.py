# coding=utf-8
"""Enforce Xingli UI -> MO2 shutdown order for the validated 2.2 candidate."""

import ast
import hashlib
import json
import shutil
import zipfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
CANDIDATE = REPO / ".work" / "xingli_Little_assistant_v2.2.0_candidate.zip"
MANIFEST = REPO / ".work" / "candidate-manifest.json"
REPORT = REPO / ".work" / "phase-a-test-report.txt"
WORK = REPO / ".work_shutdown_order"
ROOT = WORK / "xingli_Little_assistant"


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
            raise RuntimeError("candidate ZIP corrupt before shutdown-order transform: " + bad)
        z.extractall(str(WORK))

    updater_path = ROOT / "auto_updater.py"
    updater = updater_path.read_text(encoding="utf-8-sig")

    updater = replace_once(
        updater,
        '            "继续后，星黎会请求 MO2 正常关闭；退出完成后自动安装更新并重新打开 MO2。\\n"\n',
        '            "继续后，星黎会先关闭星黎小助手窗口，再请求 MO2 正常关闭；MO2 退出完成后自动安装更新并重新打开 MO2。\\n"\n',
        "update confirmation shutdown order copy",
    )

    old_install = '''            os.startfile(installer)  # type: ignore[attr-defined]\n            self.close()\n            QTimer.singleShot(250, _request_graceful_mo2_shutdown)\n'''
    new_install = '''            # Strict shutdown order: close Xingli UI first, then start the external\n            # installer, then request MO2 to exit.  The plugin itself lives inside\n            # MO2, so closing Xingli here means all Xingli-owned windows are gone\n            # before MO2 receives its close request.\n            if not bool(self.close()):\n                raise RuntimeError("无法先关闭星黎更新窗口，已取消更新。")\n            main_window = getattr(self.controller, "window", None)\n            if main_window is not None and main_window is not self:\n                if not bool(main_window.close()):\n                    raise RuntimeError("无法先关闭星黎小助手窗口，已取消更新。")\n            try:\n                QtWidgets.QApplication.processEvents()\n            except Exception:\n                pass\n            os.startfile(installer)  # type: ignore[attr-defined]\n            QTimer.singleShot(350, _request_graceful_mo2_shutdown)\n'''
    updater = replace_once(updater, old_install, new_install, "installer launch shutdown order")
    updater_path.write_text(updater, encoding="utf-8")

    changelog_path = ROOT / "CHANGELOG_v2.2.0.md"
    changelog = changelog_path.read_text(encoding="utf-8-sig")
    note = "- 优化助手自更新退出顺序：先关闭星黎小助手界面，再请求 MO2 正常关闭，随后执行更新替换。"
    if note not in changelog:
        changelog = changelog.rstrip() + "\n" + note + "\n"
    changelog_path.write_text(changelog, encoding="utf-8")

    for path in sorted(ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        try:
            ast.parse(source, filename=str(path), feature_version=(3, 8))
        except TypeError:
            ast.parse(source, filename=str(path), feature_version=8)

    install_start = updater.index("            # Strict shutdown order:")
    install_end = updater.index("        except Exception as exc:", install_start)
    block = updater[install_start:install_end]
    ordered_tokens = [
        "self.close()",
        "main_window.close()",
        "QtWidgets.QApplication.processEvents()",
        "os.startfile(installer)",
        "QTimer.singleShot(350, _request_graceful_mo2_shutdown)",
    ]
    positions = [block.index(token) for token in ordered_tokens]
    if positions != sorted(positions):
        raise RuntimeError("Xingli/MO2 shutdown order gate failed")
    if "先关闭星黎小助手窗口，再请求 MO2 正常关闭" not in updater:
        raise RuntimeError("shutdown-order user guidance missing")

    CANDIDATE.unlink()
    with zipfile.ZipFile(str(CANDIDATE), "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(p for p in ROOT.rglob("*") if p.is_file()):
            z.write(str(path), path.relative_to(WORK).as_posix())
    with zipfile.ZipFile(str(CANDIDATE)) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError("candidate ZIP corrupt after shutdown-order transform: " + bad)
        forbidden = [
            name for name in z.namelist()
            if "/__pycache__/" in ("/" + name.replace("\\", "/").lower())
            or name.lower().endswith(".pyc")
            or name.lower().endswith("/onboarding.json")
        ]
        if forbidden:
            raise RuntimeError("runtime state leaked after shutdown-order transform: {!r}".format(forbidden[:20]))

    data = CANDIDATE.read_bytes()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    manifest["size"] = len(data)
    manifest["sha256"] = hashlib.sha256(data).hexdigest()
    manifest["scope"] = "Clean First-Run + PGPatcher/TruePBR + BEES guidance + ordered updater shutdown"
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    append_report("UP1 Xingli UI closes before installer/MO2 shutdown: PASS")
    append_report("UP2 post-order Python 3.8 AST + ZIP hygiene: PASS")
    append_report("Final Candidate SHA256 (ordered shutdown): " + manifest["sha256"])
    append_report("Final Candidate size (ordered shutdown): {} bytes".format(manifest["size"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
