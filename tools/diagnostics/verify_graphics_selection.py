"""Explicit owned D3D hardware-selection smoke; no robot/game/camera I/O."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from rm_client.ui.graphics_devices import graphics_inventory
from rm_client.ui.endfield.menu.native import EndfieldNativeMenu
from tools.endfield.pack import validate


def run(pack,out):
    if sys.platform!='win32':raise RuntimeError('Windows D3D required')
    checked=validate(pack)
    out.mkdir(parents=True,exist_ok=False)
    inventory,error=graphics_inventory()
    if error:raise RuntimeError(error)
    # Include each hardware model, rather than redundantly opening remote or
    # virtual DXGI representations of the same named physical GPU.
    names=set();choices=['auto']
    for adapter in inventory:
        if adapter.name not in names:
            choices.append(adapter.id);names.add(adapter.name)
    missing='luid:ffffffff:ffffffff';choices.append(missing)
    previous=os.environ.get('POLAR_RENDER_ADAPTER')
    cases=[]
    try:
        for requested in choices:
            os.environ['POLAR_RENDER_ADAPTER']=requested
            menu=EndfieldNativeMenu(pack)
            def until(predicate):
                deadline=time.monotonic()+30
                while not predicate():
                    if menu.error:raise RuntimeError(menu.error)
                    if menu.handle and menu.api.status(menu.handle)==-1:
                        raise RuntimeError(menu.api.error(menu.handle).decode('utf-8','replace'))
                    if time.monotonic()>deadline:raise TimeoutError('Owned D3D initialization/render timeout')
                    time.sleep(.01)
            try:
                menu.prepare(0,1280,720,offscreen=True)
                until(lambda:menu.ready)
                menu.request(True);menu.tick(width=1280,height=720,chinese=True,mode='RMUL3v3',paused=False)
                until(lambda:menu.stats()['frames']>=15)
                info=menu.device_info()
                if requested not in ('auto',missing) and info.get('luid')!=requested:
                    raise AssertionError(f'Selected hardware not active: {requested}: {info}')
                if requested==missing and 'unavailable' not in info.get('selection_note',''):
                    raise AssertionError('Missing-adapter fallback was not diagnosed')
                target=out/f'frame_{len(cases)}.bgra'
                menu.api.capture(menu.handle,os.fsencode(target))
                until(lambda:menu.api.captured(menu.handle)!=0)
                if menu.api.captured(menu.handle)!=1:raise RuntimeError('Owned framebuffer capture failed')
                raw=target.read_bytes()
                if len(raw)!=1280*720*4 or sum(v>0 for v in raw[3::4])<1280*720//100:
                    raise AssertionError('Blank or truncated owned frame')
                cases.append(dict(requested=requested,actual=info,nonblank=True))
            finally:
                menu.close();menu.wait_closed()
                if menu.handle:raise AssertionError('Native device owner did not retire')
    finally:
        if previous is None:os.environ.pop('POLAR_RENDER_ADAPTER',None)
        else:os.environ['POLAR_RENDER_ADAPTER']=previous
    return dict(status='PASS',scope=__doc__,cases=cases,pack=checked,
        limits='Hardware D3D selection only; Qt WGL remains OS-controlled. No throughput or real match qualification.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack',type=Path,default=ROOT/'rm_client/ui/endfield/menu/assets')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    report=run(args.pack.resolve(),args.out.resolve())
    (args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
