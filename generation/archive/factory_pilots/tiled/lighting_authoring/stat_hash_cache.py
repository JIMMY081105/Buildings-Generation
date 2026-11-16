"""Reusable source hashes keyed by full path, byte count and nanosecond mtime."""
import hashlib
import json
from pathlib import Path
class StatHashCache:
    def __init__(self,inputs=()):
        self.rows={}
        for p in inputs:
            if Path(p).exists():
                for r in json.loads(Path(p).read_bytes()):self.rows[tuple(r['key'])]=r['value']
    def key(self,p):
        p=Path(p).absolute();s=p.stat();return str(p),s.st_size,s.st_mtime_ns
    def __contains__(self,p):return self.key(p)in self.rows
    def __getitem__(self,p):return dict(self.rows[self.key(p)])
    def __setitem__(self,p,v):
        k=self.key(p)
        if v['path']!=k[0] or v['bytes']!=k[1]:raise ValueError('Stat/reference identity differs')
        self.rows[k]=dict(v)
    def ref(self,p):
        k=self.key(p)
        if k not in self.rows:
            h=hashlib.sha256()
            with Path(p).open('rb')as f:
                for b in iter(lambda:f.read(1048576),b''):h.update(b)
            if self.key(p)!=k:raise ValueError('Source changed during hash')
            self.rows[k]=dict(path=k[0],bytes=k[1],sha256=h.hexdigest())
        return dict(self.rows[k])
    def save(self,p):
        Path(p).write_text(json.dumps([dict(key=k,value=v)for k,v in self.rows.items()],indent=2)+'\n')
