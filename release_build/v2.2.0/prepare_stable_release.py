# coding=utf-8

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CANDIDATE = REPO / '.work' / 'xingli_Little_assistant_v2.2.0_candidate.zip'
RELEASE = REPO / 'update' / 'releases' / 'xingli_Little_assistant_v2.2.0_update.zip'
LATEST = REPO / 'update' / 'latest.json'
PUBLISHED_AT = '2026-09-10T15:57:00+08:00'


def main() -> int:
    if not CANDIDATE.is_file():
        raise RuntimeError('validated 2.2.0 candidate missing')
    with zipfile.ZipFile(str(CANDIDATE)) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError('candidate ZIP corrupt: ' + bad)
        names = z.namelist()
        for required in [
            'xingli_Little_assistant/pgpatcher_adapter.py',
            'xingli_Little_assistant/CHANGELOG_v2.2.0.md',
            'xingli_Little_assistant/instance_identity.py',
        ]:
            if required not in names:
                raise RuntimeError('release content missing: ' + required)
    RELEASE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(CANDIDATE), str(RELEASE))
    data = RELEASE.read_bytes()
    package_sha = hashlib.sha256(data).hexdigest()
    package_size = len(data)
    manifest = {
        'schema': 2,
        'product': 'xingli_Little_assistant',
        'channel': 'stable',
        'version': '2.2.0',
        'title': '星黎 MO2 小助手 2.2.0',
        'published_at': PUBLISHED_AT,
        'changelog': '2.2.0：修复整合包重新解压后的真实首次启动状态；新增 PGPatcher / TruePBR 适配，可自动发现 PGPatcher、通过当前 MO2 VFS 启动、检查并安全禁用旧 PGPatcher 输出、阻止 DynDOLOD 输出状态冲突，并补充正确的 PBR 生成顺序与内置教程；Skyrim 1.5.97–1.6.659 会明确提示 PGPatcher 官方要求 Backported Extended ESL Support (BEES)。',
        'package': {
            'filename': RELEASE.name,
            'size': package_size,
            'sha256': package_sha,
            'mirrors': [
                {'name': 'Gitee 国内主源', 'url': 'https://gitee.com/HR-world/xingli/raw/main/update/releases/' + RELEASE.name},
                {'name': 'GitHub 备用源', 'url': 'https://raw.githubusercontent.com/haoranzheng/xingli/main/update/releases/' + RELEASE.name},
            ],
        },
    }
    LATEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
