"""Authorized controlled allocation, self-fails below20GiB if watchdog fails."""
import ctypes,time,json,os
from pathlib import Path
chunks=[];quarter=256*1024**2
for i in range(76):
 q=bytearray(quarter);ctypes.memset(ctypes.addressof(ctypes.c_char.from_buffer(q)),0x31,len(q));chunks.append(q);print(json.dumps({'allocated_gib':(i+1)/4,'pid':os.getpid(),'purpose':'ADJUST22 controlled runaway proof; must receive guard termination before20GiB'}),flush=True);time.sleep(.25)
print('FAIL_GUARD_DID_NOT_STOP_BEFORE19GIB',flush=True);raise SystemExit(42)
