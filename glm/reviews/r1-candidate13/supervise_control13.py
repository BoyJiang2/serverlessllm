import json,pathlib,subprocess,sys,time
root=pathlib.Path('/experiment/controls/sllm-glm51fp8-20261025T145409Z-0b3a25a1')
mode,label=sys.argv[1:]
assert mode in ('ray','service') and label.isalnum()
script='ray_control13.py' if mode=='ray' else 'bootstrap_control13.py'
budget=330 if mode=='ray' else 2500
record=dict(begin_ns=time.monotonic_ns(),status='failed',timeout_seconds=budget)
try:
    subprocess.run(['/experiment/.venv/bin/python','-u',str(root/script),label],check=True,timeout=budget)
    record['status']='passed including cleanup'
except BaseException as error:
    record['error']=repr(error)
    raise
finally:
    record['end_ns']=time.monotonic_ns()
    with (root/(label+'-supervisor-result.json')).open('x') as stream:
        json.dump(record,stream,indent=2)
