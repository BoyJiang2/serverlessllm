import datetime,hashlib,io,json,subprocess,tarfile,uuid
image='sha256:aa161e3800353473bf1c4bfc312cef77eb16fdf85e45d45d274246e6de582b6f'
run='sllm-glm51fp8-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
record=dict(run_id=run,image_id=image,purpose='Never-started readonly source extraction',started=False,files=[])
cid=None
paths=['__init__.py','model_executor/layers/attention/mla_attention.py','model_executor/layers/quantization/utils/quant_utils.py','model_executor/layers/quantization/fp8.py','model_executor/layers/fused_moe/oracle/fp8.py','model_executor/layers/quantization/utils/fp8_utils.py','utils/deep_gemm.py']
try:
 cid=subprocess.check_output(['docker','create','--name',run+'-source-only','--label','sllm.run_id='+run,'--label','sllm.purpose=never-started-source','--runtime=runc','--network=none','--read-only','--memory=128m','--cpus=1','--pids-limit=16','--entrypoint=/bin/true',image],text=True,timeout=30).strip()
 record['container_id']=cid
 c=json.loads(subprocess.check_output(['docker','inspect',cid]))[0]
 assert c['Image']==image and c['Config']['Labels']['sllm.run_id']==run and c['State']['Status']=='created' and not c['State']['Running'] and not c['Mounts']
 for relative in paths:
  target='/usr/local/lib/python3.12/dist-packages/vllm/'+relative
  r=subprocess.run(['docker','cp',cid+':'+target,'-'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
  assert r.returncode==0,(target,r.stderr.decode())
  assert len(r.stdout)<2097152
  with tarfile.open(fileobj=io.BytesIO(r.stdout)) as t:
   fs=[m for m in t.getmembers() if m.isfile()];assert len(fs)==1 and fs[0].size<1048576;b=t.extractfile(fs[0]).read()
  record['files'].append(dict(path=target,bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),raw_utf8=b.decode()))
finally:
 if cid:
  c=json.loads(subprocess.check_output(['docker','inspect',cid]))[0]
  assert c['Config']['Labels']['sllm.run_id']==run and c['Image']==image and not c['State']['Running']
  subprocess.run(['docker','rm',cid],check=True,stdout=subprocess.DEVNULL,timeout=30)
  record['own_metadata_container_removed']=True
print(json.dumps(record))
