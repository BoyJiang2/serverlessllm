"""Attribute ready sockets to the newly launched child inside its private namespace."""
import pathlib

def listener(pid,port):
    inodes=set()
    for name in ('tcp','tcp6'):
        for line in (pathlib.Path('/proc/net')/name).read_text().splitlines()[1:]:
            fields=line.split()
            if fields[3]=='0A' and int(fields[1].rsplit(':',1)[1],16)==port:
                inodes.add(fields[9])
    owned=[]
    for fd in (pathlib.Path('/proc')/str(pid)/'fd').iterdir():
        try: target=fd.readlink().as_posix()
        except FileNotFoundError: continue
        if target.startswith('socket:[') and target[8:-1] in inodes:
            owned.append(dict(fd=int(fd.name),inode=target[8:-1]))
    if not owned: raise RuntimeError(f'port {port} not owned by child {pid}')
    return dict(pid=pid,port=port,listening_sockets=owned,scope='private container network/PID namespace; current child ownership required')
