import pathlib,json,hashlib,subprocess,sys,time
p=pathlib.Path(__file__).resolve().parent; reg=json.loads((p/'registration.json').read_text()); tool=pathlib.Path(reg['code_directory'])
for n,sha in reg['source_sha256'].items():
 if hashlib.sha256((tool/n).read_bytes()).hexdigest()!=sha:raise RuntimeError('executed tool hash mismatch')
spec=p/'spec.json'; config=json.loads(spec.read_text()); prefix=['python3',str(tool/'docker_debug.py')]
record=dict(run_id=config['run_id'],code_commit=reg['code_commit'],source_sha256=reg['source_sha256'],status='failed',model_or_store_started=False)
identity=None
try:
 begin=time.monotonic()
 up=subprocess.run(prefix+['up',str(spec),'--seconds','3'],capture_output=True,text=True,timeout=90,check=True)
 identity=json.loads(up.stdout);record['up']=identity
 state=subprocess.run(prefix+['status',str(spec)],capture_output=True,text=True,timeout=30,check=True);record['observed_state']=json.loads(state.stdout)
 cid=identity['container_id']
 code=subprocess.check_output(['docker','wait',cid],text=True,timeout=20).strip();record['timeout_exit_code']=int(code)
 if code!='124':raise RuntimeError('timeout exit not124')
 record['elapsed_seconds']=time.monotonic()-begin
 logs=subprocess.check_output(['docker','logs',cid],text=True,timeout=15);record['probe']=json.loads(logs)
 if record['probe']['nvidia_devices']:raise RuntimeError('GPU device exposed')
 if record['probe']['versions']['torch']!='2.10.0+cu130':raise RuntimeError('torch version mismatch')
 record['status']='passed'
finally:
 receipt=pathlib.Path('/data/brick1/boyjiang/glm5.1-fp8/debug')/config['run_id']/(config['role']+'-identity.json')
 if receipt.exists():
  record['identity_receipt_before_cleanup']=json.loads(receipt.read_text())
  if record['identity_receipt_before_cleanup'].get('container_id'):
   down=subprocess.run(prefix+['down',str(spec)],capture_output=True,text=True,timeout=60)
   if down.returncode:record['status']='failed';record['cleanup_error']=down.stderr
   else:record['down']=json.loads(down.stdout)
 with (p/'result.json').open('x') as f:json.dump(record,f,indent=2)
if record['status']!='passed' or record.get('down',{}).get('status')!='released':raise RuntimeError('probe or release failed')
print(json.dumps(record))
