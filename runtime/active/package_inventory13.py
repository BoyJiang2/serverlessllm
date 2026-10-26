"""Exact source package inventory; bytecode stays local and never goes to SCP."""
def require_full_r1(verdict,code_commit,manifest_sha256):
    if verdict.get('not_a_full_r1') or verdict.get('approved_for_path_boundary') or any(verdict.get(key)!=value for key,value in dict(
            verdict='approved',gate='R1',scope='full-runtime',
            code_commit=code_commit,manifest_sha256=manifest_sha256).items()):
        raise RuntimeError('current bound full-runtime R1 approval required')

def reviewed_files(root,rows):
    allowed={row['path'] for row in rows}|{'control-input-manifest.json','REVIEW-PROMPT.md'}
    actual=set()
    for path in root.rglob('*'):
        relative=path.relative_to(root)
        if '__pycache__' in relative.parts: continue
        if path.is_symlink(): raise RuntimeError('source package symlink')
        if path.is_file(): actual.add(relative.as_posix())
    if actual!=allowed: raise RuntimeError('unexpected or missing package files')
    return allowed
