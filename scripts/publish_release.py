"""Publish only after the uploaded archive is downloaded and verified again."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parent.parent
repo=os.environ['GITHUB_REPOSITORY']
commit=os.environ['GITHUB_SHA']
tag='v0.4.2'

def gh(*args, check=True):
    return subprocess.run(['gh', *args, '--repo', repo], check=check, capture_output=True, text=True, encoding='utf-8')

existing=gh('release','view',tag,'--json','isDraft',check=False)
if existing.returncode == 0:
    raise RuntimeError('Release already exists; preserve it and resume by asset identity instead of overwriting')

assets=[root/'releases'/name for name in ['VideoCatch-v0.4.2-Windows-x64.zip', 'VideoCatch-Extension-v0.4.2.zip', 'checksums-v0.4.2.json', 'extension-checksums-v0.4.2.json', 'verification-v0.4.2.json']]
provenance=root/'releases/build-provenance-v0.4.2.json'
provenance.write_text(json.dumps({'repository':repo,'source_commit':commit,'version':'0.4.2','workflow_run':os.environ['GITHUB_RUN_ID']},indent=2)+'\n',encoding='utf-8')
assets.append(provenance)
gh('release','create',tag,*map(str,assets),'--target',commit,'--title','拾影 0.4.2 · 落日光轨与 AI 控制','--notes-file',str(root/'RELEASE-NOTES.md'),'--draft')
with tempfile.TemporaryDirectory(prefix='videocatch-release-check-') as temp:
    folder=Path(temp)
    gh('release','download',tag,'--dir',str(folder))
    for asset in assets:
        downloaded=folder/asset.name
        assert downloaded.stat().st_size == asset.stat().st_size, asset.name
        assert hashlib.sha256(downloaded.read_bytes()).digest() == hashlib.sha256(asset.read_bytes()).digest(), asset.name
    subprocess.run([sys.executable,str(root/'scripts/verify_release.py'),str(folder/'VideoCatch-v0.4.2-Windows-x64.zip'),'--report',str(folder/'download-verification.json')],check=True)
    gh('release','upload',tag,str(folder/'download-verification.json'))
gh('release','edit',tag,'--draft=false','--latest')
actual=json.loads(gh('release','view',tag,'--json','isDraft,body,url').stdout)
assert actual['isDraft'] is False
assert actual['body'].replace('\r\n','\n') == (root/'RELEASE-NOTES.md').read_text(encoding='utf-8').replace('\r\n','\n')
print(json.dumps({'published':actual['url'],'source_commit':commit,'uploaded_assets_downloaded_sha256_verified':True},ensure_ascii=False))
