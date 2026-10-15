"""CPU/raster-only atlas and sequential Jello module benchmark.

Production Qt shaping and QImage rasterization use the source tree's fonts.
Only OpenGL texture allocation/upload is replaced by an inert object. No
backend, transport, audio playback or device is initialized. This is NOT a
GPU, Windows, display-driver or input-latency acceptance test.

Example (fresh process for each revision/DPR):
  QT_QPA_PLATFORM=offscreen python tools/profile_jello_atlas.py --module-pages --dpr 2
"""
import argparse, json, os, sys, time
from pathlib import Path
from types import SimpleNamespace as NS, MethodType
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--dpr',type=float,default=1);p.add_argument('--module-pages',action='store_true'); a=p.parse_args()
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');os.environ.setdefault('POLAR_DISABLE_MEDIA_PRELOAD','1');sys.path.insert(0,str(a.source.resolve()))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QOpenGLContext,QSurfaceFormat
from rm_client.ui.polar_app import PolarRMWindow
from rm_client.ui.fonts import FontBook
from rm_client.ui.jello_clickgui import JelloClickGuiRenderer
from rm_client.ui import clickgui_gpu as gpu
from rm_client.ui.economy_workspace import EconomyWorkspaceLayer
from rm_client.model import build_default_registry,RobotState
from rm_client.ui.cs2_assets import local_cs2_assets
from rm_client.runtime_paths import RuntimePaths
app=QApplication([])
real_texture=gpu.QOpenGLTexture
class CpuTexture:
 MipMapGeneration=real_texture.MipMapGeneration;Filter=real_texture.Filter;WrapMode=real_texture.WrapMode
 def __init__(self,image,*args):self.size=(image.width(),image.height())
 def setMinificationFilter(self,*a):pass
 def setMagnificationFilter(self,*a):pass
 def setWrapMode(self,*a):pass
 def destroy(self):pass
gpu.QOpenGLTexture=CpuTexture
fontbook=FontBook();fontbook.load_from_project_assets(a.source);fontbook.font('jello_light',18)
r=gpu.ClickGuiGpuRenderer();r.available=True
paths=RuntimePaths.discover()
h=NS(registry=build_default_registry(),state=RobotState(),fonts=fontbook,clickgui_gpu=r,
 jello_clickgui=JelloClickGuiRenderer(a.source/'assets/jello'),width=lambda:1280,height=lambda:720,
 devicePixelRatioF=lambda:a.dpr,current_profile=lambda:'Default',font=fontbook.font,
 paths=paths,kill_fx=NS(cs2=NS(assets=local_cs2_assets())),minecraft_hud=NS(asset_root=a.source/'assets/minecraft'),
 _client_asset_path=lambda *args:a.source.joinpath(*args),economy_ui_scale=lambda *args:1.0,
 match_mode_manager=NS(current=NS(title='RMUC 7v7')),economy_workspace_layer=EconomyWorkspaceLayer())
for name in ('_jello_gpu_fonts','_jello_module_text_pages','_gpu_prewarm_jobs','economy_offers'):
 setattr(h,name,MethodType(getattr(PolarRMWindow,name),h))
jobs=h._gpu_prewarm_jobs();result=[]
while True:
 t=time.perf_counter()
 try:kind,args,kwargs=next(jobs)
 except StopIteration:break
 prepared=time.perf_counter();row={'job':len(result),'kind':kind,'generator_ms':(prepared-t)*1000}
 if kind=='text':
  row.update(font=args[0][0].family(),size=args[0][0].pointSizeF(),labels=len(args[1]),shaped=kwargs.get('text_profile')=='jello')
  r.prewarm(*args,**kwargs);row['cpu_raster_ms']=(time.perf_counter()-prepared)*1000
 elif kind=='image':row['path']=str(args)
 result.append(row)
module_results=[]
if a.module_pages:
 h.context=lambda:object();h.makeCurrent=lambda:None;h.doneCurrent=lambda:None
 for module in h.registry.modules:
  t=time.perf_counter()
  try:
   PolarRMWindow.prewarm_jello_module_resources(h,module)
   module_results.append({'module':module.name,'cpu_raster_ms':(time.perf_counter()-t)*1000,'error':None})
  except Exception as e:
   module_results.append({'module':module.name,'cpu_raster_ms':(time.perf_counter()-t)*1000,'error':str(e)})
ctx=QOpenGLContext(); fmt=QSurfaceFormat();fmt.setVersion(3,3);ctx.setFormat(fmt);created=ctx.create()
print(json.dumps({'source':str(a.source.resolve()),'dpr':a.dpr,'hardware':False,'gl_context_created':created,'bundled_fonts':fontbook.loaded,'jobs':result,'modules':module_results},ensure_ascii=False,indent=2))
