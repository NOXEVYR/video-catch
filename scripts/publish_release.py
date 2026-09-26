"""Publish explicitly selected, locally verified platform assets without rebuilding them."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check_zip(path, expected_root=None, require_manifest=False):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        normalized = [name.rstrip('/') for name in names]
        if archive.testzip() or len(normalized) != len(set(normalized)):
            raise ValueError('ZIP integrity failed: ' + path.name)
        if any(not name or name.startswith('/') or '..' in PurePosixPath(name).parts or '\\' in name or ':' in name for name in names):
            raise ValueError('ZIP has unsafe paths: ' + path.name)
        roots = {name.split('/')[0] for name in names}
        if len(roots) != 1 or (expected_root and roots != {expected_root}):
            raise ValueError('ZIP requires one application root: ' + path.name)
        root = next(iter(roots))
        links = {}
        for item in archive.infolist():
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode):
                target = archive.read(item).decode('utf-8')
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(item.filename), target))
                if not target or target.startswith('/') or '\\' in target or ':' in target or not resolved.startswith(root + '/'):
                    raise ValueError('Unsafe ZIP symlink: ' + item.filename)
                links[item.filename] = target
            elif stat.S_IFMT(mode) not in (0, stat.S_IFDIR, stat.S_IFREG):
                raise ValueError('Special ZIP entry: ' + item.filename)
        if any(str(parent) in links for name in names for parent in PurePosixPath(name).parents):
            raise ValueError('ZIP contains entries below a symlink')
        # Resolve chained links without extracting or executing the archive.
        for name in links:
            remaining, resolved, hops = name.split('/'), [], 0
            while remaining:
                part = remaining.pop(0)
                if part in ('', '.'):
                    continue
                if part == '..':
                    if len(resolved) <= 1:
                        raise ValueError('ZIP symlink chain escapes root')
                    resolved.pop()
                    continue
                resolved.append(part)
                joined = '/'.join(resolved)
                if joined in links:
                    hops += 1
                    if hops > 40:
                        raise ValueError('ZIP symlink cycle')
                    remaining = links[joined].split('/') + remaining
                    resolved.pop()
            final = '/'.join(resolved)
            if final not in normalized and not any(item.startswith(final + '/') for item in normalized):
                raise ValueError('Dangling ZIP symlink: ' + name)
        manifest_name = 'VideoCatch/runtime-manifest.json'
        if require_manifest and manifest_name not in names:
            raise ValueError('Application runtime manifest missing: ' + path.name)
        manifest = None
        if manifest_name in names:
            manifest = json.loads(archive.read(manifest_name))
            actual = {item.filename for item in archive.infolist() if not item.is_dir() and item.filename != manifest_name}
            if actual != {'VideoCatch/' + name for name in manifest['files']}:
                raise ValueError('ZIP and manifest file sets differ: ' + path.name)
            for name, meta in manifest['files'].items():
                item = archive.getinfo('VideoCatch/' + name)
                contents = archive.read(item)
                is_link = stat.S_ISLNK(item.external_attr >> 16)
                if meta.get('type') == 'symlink':
                    if not is_link or contents.decode() != meta['target']:
                        raise ValueError('Symlink mismatch: ' + name)
                elif is_link or len(contents) != meta['bytes'] or hashlib.sha256(contents).hexdigest() != meta['sha256']:
                    raise ValueError('Manifest mismatch: ' + name)
                if 'mode' in meta and stat.S_IMODE(item.external_attr >> 16) != meta['mode']:
                    raise ValueError('Manifest mode mismatch: ' + name)
        return manifest


def git(*args, check=True):
    return subprocess.run(['git', *args], cwd=ROOT, check=check, capture_output=True)


def resolve_commit(value):
    if not re.fullmatch(r'[0-9a-fA-F]{7,40}', value):
        raise ValueError('Use an explicit Git commit SHA')
    return git('rev-parse', '--verify', value + '^{commit}').stdout.decode().strip()


NON_PRODUCT_FILES = {
    'README.md', 'AI接口使用说明.md', '使用指南.html', 'RELEASE-NOTES.md',
    'TEST-REPORT.md', 'AGENTS.md', 'MIGRATION.md', 'SOURCE-PROVENANCE.json', '.gitignore',
    'scripts/publish_release.py', 'scripts/verify_release.py', 'scripts/verify_macos.py',
}


def source_relationship(source, target):
    source = resolve_commit(source)
    if git('merge-base', '--is-ancestor', source, target, check=False).returncode != 0:
        raise ValueError(f'Package source {source} is not an ancestor of release source {target}')
    changed = git('diff', '--name-only', '-z', source, target, '--').stdout.decode('utf-8').split('\0')
    changed = [name for name in changed if name]
    unexpected = [name for name in changed if name not in NON_PRODUCT_FILES and not (
        name.startswith('.github/workflows/') and name.endswith(('.yml', '.yaml')))]
    if unexpected:
        raise ValueError('Package/release product sources differ: ' + ', '.join(unexpected))
    return {'source_commit': source, 'release_commit': target, 'non_product_differences': changed}


def check_report(path, archive, manifest, version, arch=None):
    report = json.loads(path.read_text(encoding='utf-8'))
    for key, value in {'archive': archive.name, 'bytes': archive.stat().st_size,
                       'sha256': digest(archive), 'version': version, 'status': 'passed'}.items():
        if report.get(key) != value:
            raise ValueError(f'Verification report {path.name} does not bind {key} to this package')
    if report.get('source_commit', manifest['source_commit']) != manifest['source_commit']:
        raise ValueError('Verification report source commit mismatch')
    required = ['archive_crc', 'frozen_download_sha256', 'frozen_clip_audio_video_decode']
    if arch:
        if report.get('architecture') != arch:
            raise ValueError('Verification architecture mismatch')
        required += ['archive_paths_links', 'executable_components', 'ffmpeg_synthetic_audio_video', 'frozen_gui_smoke']
        if not str(report.get('ad_hoc_signature', '')).startswith('passed;'):
            raise ValueError('Missing macOS signature verification')
    else:
        required += ['packaged_client_http', 'one_click_local_pairing', 'credential_free_handoff',
                     'frozen_native_window_recording', 'duration_auto_stop', 'output_decode']
    if any(report.get(key) != 'passed' for key in required):
        raise ValueError('Required verification did not pass: ' + ', '.join(key for key in required if report.get(key) != 'passed'))
    if report.get('manifest_files') != len(manifest['files']):
        raise ValueError('Verification manifest count mismatch')
    return report


def check_source_archive(path, commit):
    check_zip(path, 'VideoCatch-source')
    expected_bytes = git('archive', '--format=zip', '--prefix=VideoCatch-source/', commit).stdout
    with zipfile.ZipFile(path) as actual, zipfile.ZipFile(io.BytesIO(expected_bytes)) as expected:
        if actual.comment.decode('ascii') != commit:
            raise ValueError('Source ZIP Git commit comment mismatch')
        def tree(archive):
            return {item.filename: (hashlib.sha256(archive.read(item)).hexdigest(),
                                    stat.S_IFMT(item.external_attr >> 16), bool(item.external_attr >> 16 & 0o111))
                    for item in archive.infolist() if not item.is_dir()}
        if tree(actual) != tree(expected):
            raise ValueError('Source ZIP does not match git archive of the release commit')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--version', default='0.5.0')
    parser.add_argument('--gh', default='gh')
    parser.add_argument('--notes', type=Path, required=True)
    args = parser.parse_args()
    version = args.version
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Expected stable semantic release version')
    commit = resolve_commit(args.commit)
    release = ROOT / 'releases'
    def gh(*command, check=True):
        return subprocess.run([args.gh, *map(str, command), '--repo', args.repo], check=check, capture_output=True, text=True, encoding='utf-8')
    files, platforms = [], {}
    specifications = [('', 'Windows-x64', None), ('-macOS-arm64', 'macOS-arm64', 'arm64'),
                      ('-macOS-x86_64', 'macOS-x86_64', 'x86_64')]
    for suffix, platform, arch in specifications:
        checksum = release/f'checksums-v{version}{suffix}.json'
        meta = json.loads(checksum.read_text(encoding='utf-8'))
        if meta['file'] != f'VideoCatch-v{version}-{platform}.zip' or meta.get('root') != 'VideoCatch':
            raise ValueError('Unexpected platform asset identity: ' + checksum.name)
        asset = release/meta['file']
        if asset.stat().st_size != meta['bytes'] or digest(asset) != meta['sha256']:
            raise ValueError('Local asset mismatch: ' + asset.name)
        manifest = check_zip(asset, 'VideoCatch', require_manifest=True)
        if manifest.get('version') != version:
            raise ValueError('Package manifest version mismatch: ' + asset.name)
        if manifest.get('source_dirty') is not False:
            raise ValueError('Package must be built from clean committed source: ' + asset.name)
        if arch and (manifest.get('architecture') != arch or manifest.get('platform') != 'macOS' or manifest.get('source_dirty') is not False):
            raise ValueError('Wrong or dirty macOS build source: ' + asset.name)
        required_files = ({'VideoCatch.app/Contents/MacOS/VideoCatch', 'VideoCatchAI'} if arch else
                          {'VideoCatch.exe', 'VideoCatchAI.exe', '_internal/tools/ffmpeg.exe', '_internal/tools/deno.exe'})
        if not required_files.issubset(manifest['files']):
            raise ValueError('Platform executable components are missing: ' + asset.name)
        relationship = source_relationship(manifest.get('source_commit', ''), commit)
        if meta.get('source_commit', relationship['source_commit']) != relationship['source_commit']:
            raise ValueError('Checksum source commit mismatch: ' + asset.name)
        report = release/f'verification-v{version}{suffix}.json'
        check_report(report, asset, manifest, version, arch)
        platforms[platform] = dict(relationship, archive=asset.name, archive_sha256=meta['sha256'], verification=report.name,
                                   verification_sha256=digest(report))
        files.extend([asset, checksum, report])
    extension_checksum = release/f'extension-checksums-v{version}.json'
    extension_meta = json.loads(extension_checksum.read_text(encoding='utf-8'))
    if extension_meta['file'] != f'VideoCatch-Extension-v{version}.zip' or extension_meta.get('root') != 'VideoCatch-extension':
        raise ValueError('Unexpected extension asset identity')
    extension = release/extension_meta['file']
    if extension.stat().st_size != extension_meta['bytes'] or digest(extension) != extension_meta['sha256']:
        raise ValueError('Extension checksum mismatch')
    check_zip(extension, 'VideoCatch-extension')
    source = release/f'VideoCatch-v{version}-Source.zip'
    check_source_archive(source, commit)
    with zipfile.ZipFile(source) as source_zip, zipfile.ZipFile(extension) as extension_zip:
        prefix = 'VideoCatch-source/extension/'
        expected_extension = {name[len(prefix):]: hashlib.sha256(source_zip.read(name)).hexdigest()
                              for name in source_zip.namelist() if name.startswith(prefix) and not name.endswith('/')}
        actual_extension = {name.split('/', 1)[1]: hashlib.sha256(extension_zip.read(name)).hexdigest()
                            for name in extension_zip.namelist() if not name.endswith('/')}
        if not expected_extension or actual_extension != expected_extension:
            raise ValueError('Extension ZIP does not match release source')
    files.extend([extension, extension_checksum, source])
    for asset in files:
        if not asset.is_file():
            raise ValueError('Missing verified delivery: ' + asset.name)
    source_meta = {p.name: {'bytes': p.stat().st_size, 'sha256': digest(p)} for p in files}
    provenance = release/f'build-provenance-v{version}.json'
    provenance.write_text(json.dumps({'repository': args.repo, 'release_source_commit': commit, 'version': version,
                                     'platforms': platforms, 'source_archive': source.name, 'assets': source_meta}, indent=2), encoding='utf-8')
    files.append(provenance)
    expected_remote = dict(source_meta)
    expected_remote[provenance.name] = {'bytes': provenance.stat().st_size, 'sha256': digest(provenance)}
    tag = 'v' + version
    existing = gh('release', 'view', tag, '--json', 'isDraft', check=False)
    if existing.returncode == 0:
        raise RuntimeError('Release already exists; preserve it and resume by asset identity')
    tag_ref = subprocess.run([args.gh, 'api', f'repos/{args.repo}/git/ref/tags/{tag}'], capture_output=True, text=True, encoding='utf-8')
    if tag_ref.returncode == 0:
        tag_commit = subprocess.run([args.gh, 'api', f'repos/{args.repo}/commits/{tag}'], check=True, capture_output=True, text=True, encoding='utf-8')
        if json.loads(tag_commit.stdout)['sha'] != commit:
            raise ValueError('Existing release tag points to a different source commit')
    elif '(HTTP 404)' not in tag_ref.stderr:
        raise RuntimeError('Unable to verify whether the release tag already exists: ' + tag_ref.stderr)
    gh('release', 'create', tag, *files, '--target', commit, '--title', '拾影 ' + version + ' · 录屏与 AI 协作', '--notes-file', args.notes, '--draft')
    with tempfile.TemporaryDirectory(prefix='videocatch-remote-check-', dir=ROOT/'.build') as temporary:
        folder = Path(temporary)
        gh('release', 'download', tag, '--dir', folder)
        for asset in files:
            remote = folder/asset.name
            expected = expected_remote[asset.name]
            if remote.stat().st_size != expected['bytes'] or digest(remote) != expected['sha256']:
                raise ValueError('Remote asset mismatch: ' + asset.name)
            if remote.suffix == '.zip':
                check_zip(remote)
    gh('release', 'edit', tag, '--draft=false', '--latest')
    result = json.loads(gh('release', 'view', tag, '--json', 'isDraft,url,assets').stdout)
    if result['isDraft']:
        raise RuntimeError('Release is still a draft')
    (release/f'published-v{version}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'url': result['url'], 'assets_verified': len(files), 'release_source_commit': commit, 'platforms': platforms},ensure_ascii=False))


if __name__ == '__main__':
    main()
