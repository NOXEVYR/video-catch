from pathlib import Path
import functools
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
import sys
import threading
import time
import zipfile

root=Path(__file__).resolve().parent.parent
source=root
import argparse
parser=argparse.ArgumentParser()
parser.add_argument('archive', type=Path)
parser.add_argument('--report', type=Path, required=True)
args=parser.parse_args()
work=Path(os.environ.get('RUNNER_TEMP', str(root/'.build')))
run_id=str(time.time_ns())
archive=args.archive.resolve()
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    assert len(z.namelist()) == len(set(z.namelist()))
    assert all(p.startswith('VideoCatch/') and '..' not in Path(p).parts and not Path(p).is_absolute() for p in z.namelist())
    z.extractall(work/('package-check-'+run_id))
package=work/('package-check-'+run_id)/'VideoCatch'
manifest=json.loads((package/'runtime-manifest.json').read_text(encoding='utf-8'))
for name,meta in manifest['files'].items():
    p=package/name
    assert p.stat().st_size==meta['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==meta['sha256'],name
fixture=work/('package-fixture-'+run_id)
fixture.mkdir(exist_ok=True)
ffmpeg=package/'_internal/tools/ffmpeg.exe'
media=fixture/'source.mp4'
subprocess.run([str(ffmpeg),'-v','error','-y','-f','lavfi','-i','testsrc=size=160x90:rate=20','-f','lavfi','-i','sine=frequency=440','-t','6','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(media)],check=True,capture_output=True)
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*_):pass
server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Quiet,directory=str(fixture)))
threading.Thread(target=server.serve_forever,daemon=True).start()
exe=package/'VideoCatch.exe'
report=fixture/'download.json'
try:
    subprocess.run([str(exe),'--verify-download',f'http://127.0.0.1:{server.server_port}/source.mp4',str(fixture/'download'),str(report)],check=True,timeout=90)
    downloaded=json.loads(report.read_text(encoding='utf-8'))
    assert downloaded['status']=='已保存',downloaded
    assert Path(downloaded['path']).read_bytes()==media.read_bytes()
finally:
    server.shutdown();server.server_close()
request=fixture/'clip.json'
request.write_text(json.dumps({'source':str(media),'start':1,'end':3.25}),encoding='utf-8')
clip_report=fixture/'clip-report.json'
subprocess.run([str(exe),'--verify-clip-json',str(request),str(fixture/'clipped'),str(clip_report)],check=True,timeout=90)
clipped=json.loads(clip_report.read_text(encoding='utf-8'))
assert clipped['status']=='已保存',clipped
decoded=subprocess.run([str(ffmpeg),'-v','error','-i',clipped['path'],'-map','0:v:0','-map','0:a:0','-progress','pipe:1','-f','null','-'],capture_output=True,check=True)
times=[int(x.split('=')[1])/1e6 for x in decoded.stdout.decode().splitlines() if x.startswith('out_time_us=')]
assert abs(times[-1]-2.25)<.12,times
# Packaged client -> actual authenticated HTTP -> application's main-thread dispatcher.
sys.path.insert(0,str(source))
import tkinter as tk
from app import App
window=tk.Tk();window.withdraw()
app=App(window)
app.ai.enabled.set()
env=dict(os.environ,VIDEOCATCH_TOKEN=app.bridge.token)
results=[]
def client():
    p=subprocess.run([str(package/'VideoCatchAI.exe'),'capabilities'],env=env,capture_output=True,timeout=20)
    results.append(p)
thread=threading.Thread(target=client);thread.start()
deadline=time.monotonic()+25
try:
    while thread.is_alive() and time.monotonic()<deadline:
        window.update();time.sleep(.01)
    thread.join(timeout=1)
    assert results and results[0].returncode==0
    data=json.loads(results[0].stdout.decode('utf-8'))
    assert data['ok'] and 'clip' in data['actions']
finally:
    app.close()
result={'archive_crc':'passed','manifest_files':len(manifest['files']),'frozen_download_sha256':'passed','frozen_clip_audio_video_decode':'passed','clip_duration_seconds':times[-1],'packaged_client_http':'passed','third_party_sites':'not_tested','user_visual_acceptance':'not_tested'}
args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result))
