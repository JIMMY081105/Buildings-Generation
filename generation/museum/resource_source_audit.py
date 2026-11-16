"""ADJUST65: inspect every Python source in the immutable run directory before CPU admission."""
from pathlib import Path
import hashlib,json,re,datetime
PATTERNS={
 'TORCH_IMPORT':re.compile(r'\b(?:import\s+torch\b|from\s+torch(?:\.|\s))'),
 'CUDA_ATTRIBUTE':re.compile(r'\.cuda\w*\b',re.I),
 'CUDA_DEVICE_ARGUMENT':re.compile(r'\bdevice\s*=\s*[rubf]*[\'\"]cuda[^\'\"]*[\'\"]',re.I),
 'ISAAC_OR_OMNI_IMPORT':re.compile(r'\b(?:from|import)\s+(?:isaac\w*|omni)(?:\.[\w.]+)?\b'),
 'DYNAMIC_GPU_IMPORT':re.compile(r'\b__import__\s*\(\s*[\'\"](?:torch|isaac\w*|omni)(?:\.[\w.]+)?[\'\"]')
}
def scan(directory,command,root):
 directory=Path(directory).resolve();root=Path(root).resolve();assert directory.is_relative_to(root/'runs')and directory.name=='source_code','SOURCE65_DIRECTORY_OUTSIDE_BOUND_RUN';assert directory.is_dir(),'SOURCE65_DIRECTORY_MISSING';files=sorted(directory.rglob('*.py'));assert files,'SOURCE65_NO_PYTHON_PAYLOAD';entry=[Path(x).resolve()for x in command if str(x).endswith('.py')and Path(x).is_file()];assert entry and entry[0].is_relative_to(directory),'SOURCE65_EXECUTABLE_OUTSIDE_DECLARED_SOURCE';binding=directory.parent/'SOURCE_BINDING.json';b=json.loads(binding.read_text());expected=b['script_sha256'];actual={};hits=[]
 for p in files:
  assert not p.is_symlink()and p.resolve().is_relative_to(directory),'SOURCE65_SYMLINK_OR_ESCAPE';name=p.relative_to(directory).as_posix();data=p.read_bytes();h=hashlib.sha256(data).hexdigest();actual[name]=h;assert expected.get(name)==h,('SOURCE65_BINDING_MISMATCH',name)
  text=data.decode('utf-8-sig')
  for label,pattern in PATTERNS.items():
   for match in pattern.finditer(text):hits.append({'file':name,'line':text.count('\n',0,match.start())+1,'pattern':label,'file_sha256':h})
 assert set(actual)==set(expected),'SOURCE65_BINDING_FILESET_MISMATCH'
 return {'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'GPU_SOURCE_PRESENT'if hits else'NO_GPU_SOURCE_PATTERN_DETECTED','directory':str(directory),'entry_script':str(entry[0]),'source_binding_sha256':hashlib.sha256(binding.read_bytes()).hexdigest(),'all_python_files_sha256':actual,'python_file_count':len(files),'gpu_hits':hits,'gpu_hit_count':len(hits),'scanner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'scope':'Every.py inactualrun/source_code recursivelyscanned, includingGPUimportsinhelpers/embeddedstrings/comments. Regexconservative; CPUclassrejectedifanyhit. Wholeboundfileset/hashchecked, entrymustinside. Thisisresourceadmissionclassification, notproofarbitraryPythonsafeandnotascientificbuildingtest.'}
