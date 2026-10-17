"""Independent actual-source constructor lookup/failure-prefix probe."""
import ast, pathlib, runpy, subprocess, sys
ROOT=pathlib.Path(sys.argv[1]).resolve()
def segment(source):
 t=ast.parse(source)
 cls=next(n for n in t.body if isinstance(n,ast.ClassDef) and n.name=='PolarRMWindow')
 init=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='__init__')
 a=next(i for i,n in enumerate(init.body) if isinstance(n,ast.Assign) and any(isinstance(x,ast.Attribute) and x.attr=='_gpu_warm_pending' for x in n.targets))+1
 b=next(i for i,n in enumerate(init.body[a:],a) if isinstance(n,ast.ImportFrom) and n.module=='window_shortcuts')
 return compile(ast.Module(body=init.body[a:b],type_ignores=[]),'<actual-constructor-segment>','exec')
base=subprocess.check_output(['git','show','fa2c2b8:rm_client/ui/polar_app.py'],cwd=ROOT,text=True)
new=(ROOT/'rm_client/ui/polar_app.py').read_text()
ports=runpy.run_path(str(ROOT/'rm_client/ui/polar/startup_activation.py'))
def run(code, visible=False, pending=False, failure_event=None):
 trace=[]
 def event(value):
  trace.append(value)
  if value==failure_event: raise RuntimeError('injected')
 class Value:
  def __init__(self,path): object.__setattr__(self,'path',path)
  def __getattr__(self,name):
   path=self.path+'.'+name;event(('get',path));return Value(path)
  def __call__(self,*args,**kwargs):
   def norm(v):return v.path if isinstance(v,Value) else v
   event(('call',self.path,tuple(map(norm,args)),tuple((k,norm(v)) for k,v in kwargs.items())))
   return Value(self.path+'()')
  def __bool__(self):
   event(('bool',self.path))
   return {'self.home_visible':visible,'self.configuration_change_pending':pending}.get(self.path,False)
 scope=dict(ports,self=Value('self'),os=Value('os'),ui_text=Value('ui_text'),chinese_enabled=Value('chinese_enabled'))
 try: exec(code,scope)
 except RuntimeError: trace.append(('failed',))
 return trace
old_code,new_code=segment(base),segment(new)
checks=0;failures=[]
for visible,pending in ((False,False),(False,True),(True,False),(True,True)):
 expected=run(old_code,visible,pending)
 for failure in [None,*dict.fromkeys(expected)]:
  old=run(old_code,visible,pending,failure);current=run(new_code,visible,pending,failure)
  checks+=1
  if old!=current:
   failures.append((visible,pending,failure,next(((a,b) for a,b in zip(old,current) if a!=b),('length',len(old),len(current)))))
print('candidate',subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip())
print('checks',checks,'failures',len(failures))
for failure in failures[:8]:print(failure)
raise SystemExit(bool(failures))
