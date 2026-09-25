"""Fetch the pinned Deno runtime for maintainers; users receive it bundled."""
import hashlib
import os
import json
from pathlib import Path
import zipfile
from urllib.request import urlopen

root = Path(__file__).resolve().parent
entry = json.loads((root / 'runtime.lock.json').read_text())['deno']
work = Path(os.environ.get('VIDEOCATCH_BUILD_DIR', root / '.build'))
binary = work / 'tools' / 'deno.exe'
if binary.is_file() and entry.get('binary_sha256') and hashlib.sha256(binary.read_bytes()).hexdigest() == entry['binary_sha256']:
    print('Deno ' + entry['version'] + ' existing binary SHA-256 verified')
    raise SystemExit(0)
cache = work / 'deno-download' / 'deno-runtime.zip'
cache.parent.mkdir(parents=True, exist_ok=True)
if not cache.exists() or hashlib.sha256(cache.read_bytes()).hexdigest() != entry['sha256']:
    with urlopen(entry['url'], timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != entry['sha256']:
        raise RuntimeError('Deno SHA-256 mismatch')
    cache.write_bytes(data)
with zipfile.ZipFile(cache) as archive:
    if archive.testzip() is not None:
        raise RuntimeError('Deno archive is corrupt')
    archive.extract('deno.exe', work / 'tools')
print('Deno ' + entry['version'] + ' verified and prepared')
