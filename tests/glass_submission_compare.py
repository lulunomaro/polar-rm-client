"""Compare inert GL submissions with the committed pre-refactor renderer.

Run from the repository root. Requires the explicitly recorded baseline commit;
this is command-trace evidence, not a GPU pixel or performance qualification.
"""
import ast
import itertools
import random
import subprocess
import sys
from pathlib import Path
from types import MethodType
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_glass_command_lifetime import GlassCommandLifetimeTests
from rm_client.ui import gpu_frame_compositor as gpu
from PySide6.QtCore import QRectF
path='rm_client/ui/gpu_frame_compositor.py'
base=ast.parse(subprocess.check_output(['git','show','666c0408dee5b2d4025faab580ffa31557dd78c0:'+path],text=True))
current=ast.parse(Path(path).read_text())
def methods(tree):
    cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='GpuFrameCompositor')
    return {node.name:node for node in cls.body if isinstance(node,ast.FunctionDef)}
old,new=methods(base),methods(current)
for name in old:
    if name.startswith('_build_') or name=='_vertex_shader':
        assert ast.dump(old[name])==ast.dump(new[name]),name
assert [ast.dump(node) for node in old['add_glass'].body[:-2]]==[ast.dump(node) for node in new['add_glass'].body[:-1]]
namespace=dict(vars(gpu))
exec(compile(ast.Module(body=[old['render_glass']],type_ignores=[]),'<baseline render_glass>','exec'),namespace)
original=namespace['render_glass']
rng=random.Random(20261017)
specs=[]
for i in range(100):
    rect=(rng.uniform(-50,850),rng.uniform(-50,650),rng.uniform(.1,100),rng.uniform(.1,100))
    specs.append((rect,rng.uniform(-2,20),list(gpu.GlassOwner)[i%5],i%3==0))
count=0
for ratio,static,owners,destination in itertools.product((1.,1.25,2.),(False,True),(None,set(),{gpu.GlassOwner.HUD},{gpu.GlassOwner.CLICKGUI,gpu.GlassOwner.ECONOMY}),('hud','overlay')):
    traces=[]
    for baseline in (True,False):
        fixture=GlassCommandLifetimeTests();fixture.setUp()
        try:
            c=fixture.c;c._device_pixel_ratio=ratio
            for rect,radius,owner,is_static in specs:
                (c.begin_static_glass if is_static else c.end_static_glass)()
                c.add_glass(QRectF(*rect),radius,owner=owner)
            c.end_static_glass()
            render=MethodType(original,c) if baseline else c.render_glass
            accepted=render(fixture.gl,include_static=static,owners=owners,destination=destination)
            traces.append((accepted,fixture.gl.mock_calls,c._draw_quad.call_count))
        finally: fixture.doCleanups()
    assert traces[0]==traces[1],(ratio,static,owners,destination)
    count+=1
print(f'PASS {count} baseline/current complete GL submission trace pairs, 100 deterministic commands each')
print('PASS all five shader builders unchanged; all command normalization statements unchanged')
