"""Independent terminal audit: metadata/logs only, never rereads weight payloads."""
from pathlib import Path
import datetime,hashlib,json,sys

def validate_lines(rows, lines):
    wanted=[r['file']+': OK' for r in rows]
    if len(lines)!=len(wanted) or sorted(lines)!=sorted(wanted):
        raise ValueError('Checksum log must contain exactly one OK for every expected file')

def audit(job):
    def read(name):return json.loads((job/name).read_bytes())
    def digest(name):return hashlib.sha256((job/name).read_bytes()).hexdigest()
    handle=read('handle.json');result=read('result.json');expected=read('expected-weight-files.json')
    assert digest('expected-weight-files.json')==handle['expected_manifest_sha256']
    assert digest('official-model-info.json')==handle['official_metadata_sha256']
    assert digest('verify_v4_whole_weights.py')==handle['runner_sha256']
    assert Path('/proc/sys/kernel/random/boot_id').read_text().strip()==handle['boot_id']
    for kind in ('supervisor','checksum_process'):
        ref=handle[kind];p=Path('/proc')/str(ref['pid'])/'stat'
        if p.exists():
            fields=p.read_text().rsplit(')',1)[1].split()
            if int(fields[19])==ref['start_ticks']:
                assert fields[0]=='Z',kind+' is still live'
                assert int(fields[49])==0,kind+' has nonzero kernel exit status'
    rows=expected['files'];assert len(rows)==64 and len({r['file'] for r in rows})==64
    check=''.join(r['expected_file_sha256']+'  '+r['file']+'\n' for r in rows).encode()
    assert (job/'expected-whole-files.sha256').read_bytes().replace(b'\r\n',b'\n')==check
    assert result['files']==[dict(file=r['file'],expected_file_sha256=r['expected_file_sha256'],
                                 bytes=r['bytes'],matched=True) for r in rows]
    validate_lines(rows,(job/'sha256sum.stdout.log').read_text().splitlines())
    assert not (job/'sha256sum.stderr.log').read_bytes()
    assert result['exit_code']==0 and result['weight_file_content_verified'] is True
    assert result['verified_files']==64 and result['before_after_file_state_equal'] is True
    before=read('before-files.json');assert before==read('after-files.json')
    assert len(before)==64
    for state in before:
        p=Path(handle['model_path'])/state['file'];st=p.lstat()
        current=dict(file=p.name,realpath=str(p.resolve()),dev=st.st_dev,inode=st.st_ino,
                     size=st.st_size,mtime_ns=st.st_mtime_ns,ctime_ns=st.st_ctime_ns,mode=st.st_mode)
        assert current==state and not p.is_symlink(),state['file']
    wrapper=None
    if (job/'failure.json').exists():
        failure=read('failure.json')
        assert failure['error_type']=='SystemExit' and failure['error']=='0',failure
        wrapper='Successful SystemExit was caught by supervisor BaseException handler; raw artifact retained, independently verified not a checksum failure'
    artifacts={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in job.iterdir()
               if p.is_file() and p.name not in ('progress.json','observations.jsonl','terminal-audit.json')}
    receipt=dict(audited_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        job=str(job),state='WEIGHT_FILES_CONTENT_MATCHES_PINNED_REVISION',
        revision=expected['revision'],verified_weight_files=64,total_whole_file_bytes=sum(r['bytes'] for r in rows),
        exact_log_coverage=True,before_after_and_audit_stat_equal=True,
        full_repository_revision_verified=False,wrapper_bookkeeping_issue=wrapper,
        elapsed_seconds=result['elapsed_seconds'],artifacts_sha256=artifacts,
        scope='64 whole weight files only. Stat stability is not writer exclusion or future load provenance. Existing .gitattributes differs; no GPU, native Store or inference qualification.')
    (job/'terminal-audit.json').write_bytes((json.dumps(receipt,indent=2)+'\n').encode())
    print(json.dumps(receipt))

if __name__=='__main__':audit(Path(sys.argv[1]).resolve())
