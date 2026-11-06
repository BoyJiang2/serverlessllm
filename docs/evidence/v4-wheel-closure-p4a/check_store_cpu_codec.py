from pathlib import Path
import os,json,hashlib,glob,datetime,subprocess,torch
from sllm_store import _C
os.umask(0o002);assert not glob.glob('/dev/nvidia*') and not torch.cuda.is_available()
base=Path('/logs');out=Path('/data/brick1/boyjiang/sllm-v4-pro-build/cpu-codec-v2');out.mkdir(exist_ok=False)
# Every possible 8-bit payload, including special encodings, is preserved without numeric conversion.
raw=torch.arange(256,dtype=torch.int16).to(torch.uint8)
tensors={
 'mxfp4_packed_expert':raw.view(torch.int8).clone(),
 'fp8_nonexpert':raw.view(torch.float8_e4m3fn).clone(),
 'ue8m0_scale':raw.view(torch.float8_e8m0fnu).clone(),
 'bf16_weight':raw.view(torch.bfloat16).clone(),
 'mhc_fp32':raw.view(torch.float32).clone(),
 'int64_buffer':raw.view(torch.int64).clone(),
}
tensors['exact_storage_alias']=tensors['mxfp4_packed_expert']
rows={n:(t.untyped_storage().data_ptr(),t.untyped_storage().nbytes()) for n,t in tensors.items()}
offsets=_C.save_tensors(list(tensors),rows,str(out))
files=list(out.glob('tensor.data_*'));assert len(files)==1
payload=files[0].read_bytes();assert len(payload)==4096
evidence=[]
for name,t in tensors.items():
 expected=bytes(t.view(torch.uint8).tolist());start=offsets[name];actual=payload[start:start+len(expected)]
 assert actual==expected and start%8==0
 restored=torch.frombuffer(bytearray(actual),dtype=t.dtype).clone()
 assert bytes(restored.view(torch.uint8).tolist())==expected
 evidence.append(dict(name=name,dtype=str(t.dtype),bytes=len(expected),offset=start,sha256=hashlib.sha256(actual).hexdigest()))
assert offsets['exact_storage_alias']==offsets['mxfp4_packed_expert']
unique=[offsets[n] for n in tensors if n!='exact_storage_alias'];assert len(set(unique))==6
assert len(payload.rstrip(b'\x00'))<=6*256
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),torch=torch.__version__,extension=_C.__file__,records=evidence,partition=dict(path=str(files[0]),bytes=len(payload),sha256=hashlib.sha256(payload).hexdigest()),writer_source_open_flags='O_DIRECT (actual native extension; no substitution)',alias_deduplicated=True,scope='Real installed native CPU writer and byte reconstruction only; tiny synthetic tensors, no native GPU restore, no full Store/model execution')
(base/'store-native-cpu-codec.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
