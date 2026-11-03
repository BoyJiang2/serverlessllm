import concurrent.futures,datetime,hashlib,json,os,pathlib,subprocess,time
import httpx
root=pathlib.Path('/data/brick1/boyjiang/ServerlessLLM/models')
cache=root/'.baseline-tools/oci-layer-cache';cache.mkdir(exist_ok=True)
receipt=root/'qwen3-image-cache-import-receipt.json'
image='serverlessllm/sllm@sha256:470039cd917ca13febc1a5d04578e60850fbc9b1604dc5cdf4ad53561fd9035e'
expected='sha256:6a7fb919dcd7bf67b14c9a533e1ff53c2384ebbe9c2562f42aa23674fd25350a'
proxy='http://127.0.0.1:17897'
hard_end=datetime.datetime.fromisoformat('2026-11-04T03:10:00+08:00').timestamp()
state={'status':'starting','pid':os.getpid(),'image':image,'cache':str(cache),'verified_layers':[],'deadline':'2026-11-04T03:10:00+08:00','previous_attempt':'SSH connection closed; previous child PIDs absent and expected image absent'}
def emit(event):
 state.update(event);receipt.write_text(json.dumps(state,indent=2));print(json.dumps(event),flush=True)
def remaining():
 value=hard_end-time.time()
 if value<=0:raise TimeoutError('task transfer deadline reached')
 return value
def client():return httpx.Client(proxy=proxy,follow_redirects=True,timeout=httpx.Timeout(30,connect=10))
def token(c):
 r=c.get('https://auth.docker.io/token',params={'service':'registry.docker.io','scope':'repository:serverlessllm/sllm:pull'});r.raise_for_status();return r.json()['token']
def fetch(layer):
 digest=layer['digest'];alg,hexpart=digest.split(':');assert alg=='sha256' and len(hexpart)==64 and all(x in '0123456789abcdef' for x in hexpart)
 final=cache/digest;partial=cache/(digest+'.partial');size=layer['size']
 if final.exists():
  h=hashlib.file_digest(final.open('rb'),'sha256').hexdigest() if hasattr(hashlib,'file_digest') else sha(final)
  assert h==hexpart and final.stat().st_size==size;return {'digest':digest,'bytes':size,'cache_hit':True}
 for attempt in range(1,11):
  remaining()
  try:
   offset=partial.stat().st_size if partial.exists() else 0;assert offset<=size
   if offset<size:
    with client() as c:
     headers={'Authorization':'Bearer '+token(c)}
     if offset:headers['Range']='bytes='+str(offset)+'-'
     with c.stream('GET','https://registry-1.docker.io/v2/serverlessllm/sllm/blobs/'+digest,headers=headers) as r:
      r.raise_for_status()
      if offset:assert r.status_code==206 and r.headers.get('Content-Range','').startswith('bytes '+str(offset)+'-'),'resume range refused'
      with partial.open('ab') as f:
       for block in r.iter_bytes(1024*1024):
        remaining();f.write(block)
        assert f.tell()<=size,'oversized blob'
   assert partial.stat().st_size==size,'short blob'
   assert sha(partial)==hexpart,'blob hash mismatch'
   partial.rename(final)
   return {'digest':digest,'bytes':size,'attempts':attempt}
  except Exception as e:
   print(json.dumps({'layer':digest,'attempt':attempt,'error_type':type(e).__name__}),flush=True)
   if isinstance(e,(AssertionError,TimeoutError)) or attempt==10:raise
   time.sleep(min(3,remaining()))
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
 return h.hexdigest()
try:
 emit({'status':'fetching_manifest'})
 with client() as c:
  r=c.get('https://registry-1.docker.io/v2/serverlessllm/sllm/manifests/'+image.split('@')[1],headers={'Authorization':'Bearer '+token(c),'Accept':'application/vnd.oci.image.manifest.v1+json'});r.raise_for_status()
  assert hashlib.sha256(r.content).hexdigest()==image.split(':')[-1]
  manifest=r.json();(cache/'manifest.json').write_bytes(r.content)
 emit({'status':'parallel_layers','layer_count':len(manifest['layers']),'total_bytes':sum(x['size'] for x in manifest['layers'])})
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  for result in pool.map(fetch,manifest['layers']):
   state['verified_layers'].append(result);emit({'verified_count':len(state['verified_layers'])})
 emit({'status':'loading_verified_layer_cache'})
 env=dict(os.environ,HTTPS_PROXY=proxy,HTTP_PROXY=proxy)
 loader=subprocess.Popen(['docker','load'],stdin=subprocess.PIPE)
 puller=subprocess.Popen([str(root/'.baseline-tools/crane-v0.22.1'),'pull','--cache_path',str(cache),'--platform','linux/amd64',image,'/dev/stdout'],env=env,stdout=loader.stdin)
 loader.stdin.close();emit({'puller_pid':puller.pid,'loader_pid':loader.pid})
 try:
  a=puller.wait(timeout=remaining());b=loader.wait(timeout=remaining());assert a==0 and b==0,'archive/load failed'
 finally:
  for p in (puller,loader):
   if p.poll() is None:
    p.terminate()
    try:p.wait(timeout=10)
    except subprocess.TimeoutExpired:p.kill();p.wait(timeout=10)
 r=subprocess.run(['docker','image','inspect',expected,'--format','{{.Id}}'],capture_output=True,text=True,timeout=20)
 assert r.returncode==0 and r.stdout.strip()==expected,'image config mismatch'
 emit({'status':'image_config_verified','actual_image_id':r.stdout.strip(),'finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
except Exception as e:
 emit({'status':'failed','error_type':type(e).__name__,'failed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()});raise SystemExit(1)
