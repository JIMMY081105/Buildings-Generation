"""Use installed libzstd with bounded streaming buffers; no environment installation."""
import ctypes,ctypes.util,pathlib,shutil,tarfile,tempfile

class Input(ctypes.Structure):
 _fields_=[('src',ctypes.c_void_p),('size',ctypes.c_size_t),('pos',ctypes.c_size_t)]
class Output(ctypes.Structure):
 _fields_=[('dst',ctypes.c_void_p),('size',ctypes.c_size_t),('pos',ctypes.c_size_t)]

def decompress(source,target):
 lib=ctypes.CDLL(ctypes.util.find_library('zstd') or 'libzstd.so.1')
 lib.ZSTD_createDStream.restype=ctypes.c_void_p
 lib.ZSTD_freeDStream.argtypes=[ctypes.c_void_p];lib.ZSTD_freeDStream.restype=ctypes.c_size_t
 lib.ZSTD_initDStream.argtypes=[ctypes.c_void_p];lib.ZSTD_initDStream.restype=ctypes.c_size_t
 lib.ZSTD_decompressStream.argtypes=[ctypes.c_void_p,ctypes.POINTER(Output),ctypes.POINTER(Input)];lib.ZSTD_decompressStream.restype=ctypes.c_size_t
 lib.ZSTD_isError.argtypes=[ctypes.c_size_t];lib.ZSTD_isError.restype=ctypes.c_uint
 lib.ZSTD_getErrorName.argtypes=[ctypes.c_size_t];lib.ZSTD_getErrorName.restype=ctypes.c_char_p
 state=lib.ZSTD_createDStream()
 if not state:raise RuntimeError('ZSTD_createDStream failed')
 def check(result):
  if lib.ZSTD_isError(result):raise RuntimeError('libzstd: '+lib.ZSTD_getErrorName(result).decode())
  return result
 try:
  check(lib.ZSTD_initDStream(state));last=1;saw_input=False
  with pathlib.Path(source).open('rb') as src,pathlib.Path(target).open('wb') as dst:
   while True:
    compressed=src.read(1024*1024)
    if not compressed:break
    saw_input=True;buffer=ctypes.create_string_buffer(compressed);inp=Input(ctypes.cast(buffer,ctypes.c_void_p),len(compressed),0)
    while True:
     output=ctypes.create_string_buffer(1024*1024);out=Output(ctypes.cast(output,ctypes.c_void_p),len(output),0)
     last=check(lib.ZSTD_decompressStream(state,ctypes.byref(out),ctypes.byref(inp)))
     if out.pos:dst.write(output.raw[:out.pos])
     if inp.pos==inp.size and (last==0 or out.pos<out.size):break
   if not saw_input or last!=0:raise RuntimeError('empty/truncated zstd stream')
 finally:lib.ZSTD_freeDStream(state)

def extract(source,destination):
 root=pathlib.Path(destination).resolve();root.mkdir(parents=True,exist_ok=True);tops=set()
 with tempfile.TemporaryDirectory(prefix='zstd_',dir=root.parent) as temp:
  raw=pathlib.Path(temp)/'decoded.tar';decompress(source,raw)
  with tarfile.open(raw,'r:') as tar:
   for member in tar:
    name=pathlib.PurePosixPath(member.name)
    if name.is_absolute() or '..' in name.parts or not name.parts:raise RuntimeError('unsafe tar member')
    target=root.joinpath(*name.parts)
    if not target.resolve().is_relative_to(root):raise RuntimeError('tar destination outside task temp root')
    tops.add(name.parts[0])
    if member.isdir():target.mkdir(parents=True,exist_ok=True)
    elif member.isfile():
     target.parent.mkdir(parents=True,exist_ok=True)
     with tar.extractfile(member) as incoming,target.open('wb') as outgoing:shutil.copyfileobj(incoming,outgoing)
     if target.stat().st_size!=member.size:raise RuntimeError('tar member extracted size mismatch')
    else:raise RuntimeError('unsupported link/device member; no source accepted or overwritten')
 return sorted(tops)
