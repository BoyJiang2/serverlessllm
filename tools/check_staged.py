"""Screen explicitly staged blobs before experiment commits; never print secret values."""
import pathlib,re,subprocess,sys

def contains_secret(data):
    patterns=[rb'-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----',
              rb'gh[pousr]_[A-Za-z0-9]{20,}',rb'github_pat_[A-Za-z0-9_]{30,}',
              rb'(?i)(?:password|passwd|api[_-]?key|access[_-]?token)\s*[:=]\s*[\x22\x27][^\x22\x27\s]{8,}[\x22\x27]']
    return any(re.search(pattern,data) for pattern in patterns)

def checked_files(root):
    names=subprocess.check_output(['git','diff','--cached','--name-only','--diff-filter=ACMR','-z'],cwd=root).decode().split('\0')
    checked=[]
    for name in filter(None,names):
        path=pathlib.PurePosixPath(name)
        if path.name in ['HANDOFF.md','GLM51-FP8-status-20261025.md','NEXT-WINDOW-RUNBOOK.md'] or path.name.startswith('.env'):
            raise RuntimeError('forbidden staged filename: '+name)
        if path.suffix.lower() in ('.safetensors','.pt','.pth','.bin','.so','.dll','.exe','.pem','.key','.tar','.gz','.zip'):
            raise RuntimeError('forbidden staged data/binary: '+name)
        data=subprocess.check_output(['git','show',':'+name],cwd=root)
        if len(data)>1024*1024: raise RuntimeError('staged blob exceeds1MiB: '+name)
        if contains_secret(data): raise RuntimeError('possible secret in staged blob: '+name)
        if b'\0' in data: raise RuntimeError('binary staged blob: '+name)
        data.decode('utf-8')
        checked.append((name,len(data)))
    return checked

if __name__=='__main__':
    assert contains_secret(b'ghp_'+b'X'*36)
    assert contains_secret(b'-----BEGIN '+b'OPENSSH PRIVATE KEY-----')
    assert not contains_secret(b'{"token_ids":[12,34],"sha256":"0123456789abcdef"}')
    rows=checked_files(pathlib.Path(__file__).resolve().parent.parent)
    print('staged UTF-8 blobs checked:',len(rows),'total bytes:',sum(size for _,size in rows))
