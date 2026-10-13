"""Read existing layout geometry; do not modify any original HUD/module setting."""
def reserved_regions(window, width, height):
    scale=float(window.get_hud_scale(width,height)) if hasattr(window,'get_hud_scale') else 1.
    hud_rect=lambda x,y,w,h:(x*scale,y*scale,w*scale,h*scale)
    regions=[(0.,0.,float(width),58.*scale),(width/2-145.*scale,0.,290.*scale,74.*scale),(width/2-30.,height/2-30.,60.,60.)]
    layout=getattr(getattr(window,'top_match_bar',None),'last_layout',None)
    if layout is not None:
        r=layout.bounds;regions.append(hud_rect(r.x,r.y,r.width,r.height))
    else:regions.append((width/2-230.,0.,460.,180.))
    progress=getattr(getattr(window,'top_match_bar',None),'last_progress_bounds',None)
    if progress is not None:regions.append(hud_rect(progress.x(),progress.y(),progress.width(),progress.height()))
    registry=getattr(window,'registry',None)
    if registry is None:return regions
    hud=registry.get('HUD')
    minecraft=window.minecraft_hud_active()
    if window.setting_bool(hud,'Module List',True):
        style=window.setting_mode(hud,'ModuleList Style','PolarTextList')
        if not minecraft and style=='PolarTextList':regions.append((0.,58.*scale,290.*scale,max(0.,height-76.*scale)))
        else:
            # Include animated shadow extent, not just the text anchor.
            cache=getattr(window,'_jello_active_mods_width_cache',{})
            widths=[v[0] for v in cache.values() if isinstance(v,tuple) and v and isinstance(v[0],(int,float))]
            margin=max(300.,1.5*max(widths,default=180.)+30.)*scale
            regions.append((width-margin,0.,margin,height-54.))
    tactical=registry.get('TacticalMap')
    if tactical is not None and tactical.enabled:
        # The original map already sampled this animation container during its
        # draw. Read it without restarting morph targets at viewport dimensions.
        r=window.tactical_map_state.container
        regions.append(hud_rect(r.x-4.,r.y-4.,r.width+8.,r.height+8.))
    mc=getattr(window,'_last_minecraft_hud_layout',None)
    if minecraft and mc is not None:
        for name in ('hotbar','scoreboard'):
            r=getattr(mc,name,None)
            if r is not None:regions.append((r.x()-8.,r.y()-8.,r.width()+16.,r.height()+16.))
        for name in ('hearts','armor','food','boss_bars'):
            for r in getattr(mc,name,()):regions.append((r.x()-4.,r.y()-4.,r.width()+8.,r.height()+8.))
    else:regions.append((width/2-250.,height-150.,500.,150.))
    return regions
