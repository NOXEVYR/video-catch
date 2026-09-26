"""Publish explicitly selected, locally verified platform assets without rebuilding them."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check_zip(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if archive.testzip() or len(names) != len(set(names)):
            raise ValueError('ZIP integrity failed: ' + path.name)
        if any(name.startswith('/') or '..' in Path(name).parts or '\\' in name for name in names):
            raise ValueError('ZIP has unsafe paths: ' + path.name)
        if len({name.split('/')[0] for name in names}) != 1:
            raise ValueError('ZIP requires one application root: ' + path.name)
        manifest_name = 'VideoCatch/runtime-manifest.json'
        if manifest_name in names:
            manifest = json.loads(archive.read(manifest_name))
            for name, meta in manifest['files'].items():
                contents = archive.read('VideoCatch/' + name)
                if meta.get('type') == 'symlink':
                    if contents.decode() != meta['target']:
                        raise ValueError('Symlink mismatch: ' + name)
                elif len(contents) != meta['bytes'] or hashlib.sha256(contents).hexdigest() != meta['sha256']:
                    raise ValueError('Manifest mismatch: ' + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--version', default='0.5.0')
    parser.add_argument('--gh', default='gh')
    parser.add_argument('--notes', type=Path, required=True)
    args = parser.parse_args()
    version = args.version
    release = ROOT / 'releases'
    def gh(*command, check=True):
        return subprocess.run([args.gh, *map(str, command), '--repo', args.repo], check=check, capture_output=True, text=True, encoding='utf-8')
    files = []
    for checksum in [release/f'checksums-v{version}.json', release/f'extension-checksums-v{version}.json',
                     release/f'checksums-v{version}-macOS-arm64.json', release/f'checksums-v{version}-macOS-x86_64.json']:
        meta = json.loads(checksum.read_text(encoding='utf-8'))
        asset = release/meta['file']
        if asset.stat().st_size != meta['bytes'] or digest(asset) != meta['sha256']:
            raise ValueError('Local asset mismatch: ' + asset.name)
        check_zip(asset)
        files.extend([asset, checksum])
    files.extend([release/f'verification-v{version}.json', release/f'verification-v{version}-macOS-arm64.json',
                  release/f'verification-v{version}-macOS-x86_64.json', release/f'VideoCatch-v{version}-Source.zip'])
    for asset in files:
        if not asset.is_file():
            raise ValueError('Missing verified delivery: ' + asset.name)
    source_meta = {p.name: {'bytes': p.stat().st_size, 'sha256': digest(p)} for p in files}
    provenance = release/f'build-provenance-v{version}.json'
    provenance.write_text(json.dumps({'repository': args.repo, 'source_commit': args.commit, 'version': version, 'assets': source_meta}, indent=2), encoding='utf-8')
    files.append(provenance)
    tag = 'v' + version
    existing = gh('release', 'view', tag, '--json', 'isDraft', check=False)
    if existing.returncode == 0:
        raise RuntimeError('Release already exists; preserve it and resume by asset identity')
    gh('release', 'create', tag, *files, '--target', args.commit, '--title', '拾影 ' + version + ' · 录屏与 AI 协作', '--notes-file', args.notes, '--draft')
    with tempfile.TemporaryDirectory(prefix='videocatch-remote-check-', dir=ROOT/'.build') as temporary:
        folder = Path(temporary)
        gh('release', 'download', tag, '--dir', folder)
        for asset in files:
            remote = folder/asset.name
            if remote.stat().st_size != asset.stat().st_size or digest(remote) != digest(asset):
                raise ValueError('Remote asset mismatch: ' + asset.name)
            if remote.suffix == '.zip':
                check_zip(remote)
    gh('release', 'edit', tag, '--draft=false', '--latest')
    result = json.loads(gh('release', 'view', tag, '--json', 'isDraft,url,assets').stdout)
    if result['isDraft']:
        raise RuntimeError('Release is still a draft')
    (release/f'published-v{version}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'url': result['url'], 'assets_verified': len(files), 'source_commit': args.commit},ensure_ascii=False))


if __name__ == '__main__':
    main()
