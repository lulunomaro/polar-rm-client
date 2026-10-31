"""Compare current MC raster/hit geometry with an explicit untouched checkout."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage,QPainter
from PySide6.QtWidgets import QApplication
from rm_client.model import RobotState,build_default_registry
from rm_client.ui.minecraft_hud import MinecraftCombatHudRenderer


def run(baseline):
    app=QApplication.instance() or QApplication([])
    spec=importlib.util.spec_from_file_location('rm_client.ui._mc_before_cache',baseline/'rm_client/ui/minecraft_hud.py')
    previous=importlib.util.module_from_spec(spec);sys.modules[spec.name]=previous;spec.loader.exec_module(previous)
    registry=build_default_registry();state=RobotState()
    renderers=(previous.MinecraftCombatHudRenderer(ROOT/'assets/minecraft_hud'),MinecraftCombatHudRenderer(ROOT/'assets/minecraft_hud'))
    cases=[]
    for language in ('English','简体中文'):
        registry.get('ClickGui').get_setting('Language').value=language
        for width,height,multiplier in ((1280,720,.5),(1600,900,1),(1920,1080,1.5),(2560,1440,2)):
            module=registry.get('MinecraftCombatHUD');module.get_setting('Scale Multiplier').value=multiplier
            for dpr in (1,1.5,2):
                images=[];geometry=[]
                for renderer in renderers:
                    image=QImage(round(width*dpr),round(height*dpr),QImage.Format_ARGB32_Premultiplied)
                    image.setDevicePixelRatio(dpr);image.fill(0)
                    painter=QPainter(image)
                    try:layout=renderer.draw(painter,width,height,state,registry,module)
                    finally:painter.end()
                    images.append(image)
                    geometry.append((layout.scale,layout.hotbar.getRect(),layout.scoreboard.getRect(),tuple(r.getRect() for r in layout.slots)))
                if images[0]!=images[1] or geometry[0]!=geometry[1]:raise AssertionError((language,width,height,multiplier,dpr))
                cases.append(dict(language=language,size=[width,height],multiplier=multiplier,dpr=dpr))
    return dict(status='PASS',scope='real QPainter MC layer only; explicit baseline Python source, identical native assets',pixel_and_hit_pairs=len(cases),cases=cases)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-root',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();result=run(args.baseline_root.resolve())
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='cases'}))
