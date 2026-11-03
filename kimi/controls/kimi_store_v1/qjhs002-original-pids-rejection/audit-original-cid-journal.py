import datetime,json,subprocess
cid='c6ae802f821246c41cf8982688ee6e68e412cdba9182cc11a1ec85ee64c056a7'
command=['/usr/bin/journalctl','--since','2026-11-03 20:48:00','--until','2026-11-03 20:55:00','--no-pager','-o','json']
p=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20)
rows=[];kernel_counts={};total=0
for line in p.stdout.splitlines():
 try:r=json.loads(line)
 except json.JSONDecodeError:continue
 total+=1;message=r.get('MESSAGE','');message=message if isinstance(message,str) else repr(message)
 if cid in message:
  rows.append({'timestamp':r.get('__REALTIME_TIMESTAMP'),'identifier':r.get('SYSLOG_IDENTIFIER'),'pid':r.get('_PID'),'message_prefix':message[:1000],'message_characters':len(message)})
 if r.get('_TRANSPORT')=='kernel':
  for term in ('out of memory','oom-kill','fork','pids','pthread','nvrm','xid'):
   if term in message.lower():kernel_counts[term]=kernel_counts.get(term,0)+1
print(json.dumps({'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'query':command,'returncode':p.returncode,'stderr':p.stderr[:500],'total_records':total,'exact_cid_records':rows,'kernel_term_counts':kernel_counts,'limits':'No original OCI reconstruction; absence of logged event is not proof of absence of resource failure; unmatching messages not retained'},indent=2))
