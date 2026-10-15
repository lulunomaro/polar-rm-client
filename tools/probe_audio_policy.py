"""Real Qt decoder audit; physical output unavailable, device guard controlled only.

The cold worker calls the production FFmpeg PCM cache, with its completion held
by a threading.Event to make focus and settings interleavings deterministic.
"""
from __future__ import annotations
import argparse, hashlib, json, os, sys, threading, time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--kill-cue',type=Path,required=True);p.add_argument('--cs2-pack',type=Path,required=True);p.add_argument('--expect-stale',action='store_true');args=p.parse_args()
ROOT=args.root.resolve();OUT=args.output.parent
OUT.mkdir(parents=True,exist_ok=True)
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['POLAR_USER_DATA_ROOT']=str(OUT/'policy-user-data')
os.environ['POLAR_DISABLE_MEDIA_PRELOAD']='1'
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from PySide6.QtCore import QCoreApplication,QEvent,QEventLoop,Qt,QTimer
from PySide6.QtWidgets import QApplication,QWidget
from PySide6.QtMultimedia import QAudioBufferOutput,QMediaDevices,QMediaPlayer as RealPlayer
from PySide6.QtTest import QTest
from rm_client import audio_preferences as prefs,ui_audio as ui,kill_audio as kill
from rm_client.ui.cs2_settings_center import build_settings_center
from test_cs2_settings_center import Host,ready_page
app=QApplication.instance() or QApplication([])
app.setQuitOnLastWindowClosed(False)
host=Host();host.show();host.activateWindow();app.processEvents()
try:
    from rm_client.ui.window_shortcuts import WindowShortcuts
    host._window_shortcuts=WindowShortcuts(host)
except ImportError:
    pass
players=[];current=None
class ObservedPlayer(RealPlayer):
    def __init__(self,parent=None):
        super().__init__(parent);players.append(self);self.record=None
        self.capture=QAudioBufferOutput(self);self.setAudioBufferOutput(self.capture)
        self.capture.audioBufferReceived.connect(self.receive)
    def play(self):
        self.record=current
        if self.record is not None:
            self.record['play_calls']+=1
            self.record['volumes'].append(round(self.audioOutput().volume(),5))
            self.record['sources'].append(Path(self.source().toLocalFile()).name)
        return super().play()
    def receive(self,buffer):
        if self.record is not None and buffer.isValid():
            self.record['pcm_bytes']+=buffer.byteCount()
            self.record['nonzero_pcm_bytes']+=sum(x!=0 for x in bytes(buffer.data()))
ui.QMediaPlayer=ObservedPlayer
real_outputs=[d.description() for d in QMediaDevices.audioOutputs()]
# The production guard prevents reaching decoder/queue logic on this host.
# Substitute only that guard. Qt itself still sees the genuine no-device state.
kill.QMediaDevices=NS(defaultAudioOutput=lambda:NS(isNull=lambda:False))
original=args.kill_cue.resolve()
assert original.is_file(), original
rows=[];dialogs=[];pools=[]
def wait(ms):
    loop=QEventLoop();QTimer.singleShot(ms,loop.quit);loop.exec()
def new_row(label):
    global current
    current={'name':label,'play_calls':0,'pcm_bytes':0,'nonzero_pcm_bytes':0,'volumes':[],'sources':[]}
    rows.append(current);return current

def audio_dialog():
    dialog=build_settings_center(host);dialogs.append(dialog)
    page=ready_page(dialog,'音频')
    return dialog,page

def apply_master(page,volume):
    page.audio_controls['master_volume'].spin.setValue(volume)
    page.apply_draft()

def gated_pool():
    pool=kill.KillSoundPool(original.parent,count=2);pools.append(pool)
    release=threading.Event();entered=threading.Event();decode=pool._cache.decode
    def gated(source):
        entered.set()
        if not release.wait(5):raise RuntimeError('test gate timed out')
        return decode(source)
    pool._cache.decode=gated
    return pool,release,entered

def close_pool(pool):
    pool.close()
    pool.apply_audio_preferences()

# Establish the actual production settings controls before holding a worker.
dialog,page=audio_dialog()
for mode in ('requested_while_master_muted','mute_then_unmute_during_decode'):
    prefs.save_audio(prefs.AudioPreferences())
    apply_master(page,0 if mode=='requested_while_master_muted' else 100)
    pool,release,entered=gated_pool();row=new_row(mode)
    row['accepted']=pool.play(original.name,55)
    row['decoder_started']=entered.wait(.1)
    row['waiting_before_settings']=len(pool._waiting[original.name])
    if mode=='mute_then_unmute_during_decode':apply_master(page,0)
    row['waiting_while_muted']=len(pool._waiting[original.name])
    apply_master(page,100)
    row['waiting_after_unmute']=len(pool._waiting[original.name])
    release.set();wait(1850)
    row['stale_cue_reproduced']=row['nonzero_pcm_bytes']>0
    row['policy_pass']=not row['stale_cue_reproduced']
    close_pool(pool)

dialog.reject();wait(180)
for mode in ('requested_while_unfocused','focus_lost_then_restored_during_decode'):
    prefs.save_audio(prefs.AudioPreferences(mute_unfocused=True))
    state={'value':Qt.ApplicationState.ApplicationInactive if mode=='requested_while_unfocused' else Qt.ApplicationState.ApplicationActive}
    with patch.object(prefs.QGuiApplication,'applicationState',side_effect=lambda:state['value']):
        pool,release,entered=gated_pool();row=new_row(mode)
        row['accepted']=pool.play(original.name,55);row['decoder_started']=entered.wait(.1)
        if mode=='focus_lost_then_restored_during_decode':
            state['value']=Qt.ApplicationState.ApplicationInactive
            app.applicationStateChanged.emit(state['value'])
        row['waiting_while_unfocused']=len(pool._waiting[original.name])
        state['value']=Qt.ApplicationState.ApplicationActive
        app.applicationStateChanged.emit(state['value'])
        release.set();wait(1850)
        row['stale_cue_reproduced']=row['nonzero_pcm_bytes']>0
        row['policy_pass']=not row['stale_cue_reproduced']
        close_pool(pool)

prefs.save_audio(prefs.AudioPreferences())
pool,release,entered=gated_pool();row=new_row('close_while_decode_pending')
row['accepted']=pool.play(original.name,55);assert entered.wait(2)
pool.close();release.set();wait(500)
row['waiting_after_close']=sum(map(len,pool._waiting.values()))
row['closed_no_playback']=not row['play_calls'] and not pool._waiting

# Native CS2 routes through ReliableAudioPool and should gate at request time.
from rm_client.ui.cs2_assets import Cs2Assets
cs2=Cs2Assets(args.cs2_pack.resolve());cs2.preload_audio();wait(100)
assert cs2._audio is not None, 'CS2 asset fixture did not load'
prefs.save_audio(prefs.AudioPreferences(master_volume=0))
row=new_row('native_cs2_request_while_master_muted')
row['accepted']=cs2.play('kill',.55);prefs.save_audio(prefs.AudioPreferences());wait(300)
row['muted_no_playback']=not row['play_calls']
row=new_row('native_cs2_unmuted_repeated')
for _ in range(4):cs2.play('kill',.55);wait(250)
wait(500);row['nonzero_pcm_observed']=row['nonzero_pcm_bytes']>0

# Full production preview -> accept -> renderer -> asynchronous pool, with
# real timers and repeated fullscreen transitions while a settings page closes.
from rm_client.kill_fx import KillFxRenderer
from rm_client.ui.polar.mixins.config_actions_mixin import ConfigActionsMixin
from rm_client.ui.polar.mixins.video_backend_mixin import VideoBackendMixin
host.kill_fx=KillFxRenderer(original.parent.parent);pools.append(host.kill_fx.sounds)
host.setting_mode=lambda module,key,default: module.get_setting(key).value if module and module.get_setting(key) else default
host.setting_bool=lambda module,key,default: bool(host.setting_mode(module,key,default))
host.get_setting_number=lambda module,key,default: float(host.setting_mode(module,key,default))
host.elimination_element_toggles=lambda module: VideoBackendMixin.elimination_element_toggles(host,module)
host.accept_combat_event=lambda event: VideoBackendMixin.accept_combat_event(host,event)
host.registry.get('MatchChat').enabled=False
fx=host.registry.get('EliminationFX');fx.enabled=True
fx.get_setting('Preset').value='Default Scrolling'
fx.get_setting('Preview Sequence').value='Normal 1-6'
fx.get_setting('Lock Target Bonus').value=False  # Only the primary asset fixture is provided.
fx.get_setting('Sound Enabled').value=True
fx.get_setting('Sound Volume').value=55
prefs.save_audio(prefs.AudioPreferences())
dialog,page=audio_dialog();row=new_row('repeated_preview_settings_close_fullscreen')
fullscreen=[]
def toggle(target):
    QTest.keyClick(target,Qt.Key.Key_F11);fullscreen.append(host.isFullScreen())
QTimer.singleShot(0,lambda:ConfigActionsMixin.preview_elimination_fx(host))
QTimer.singleShot(200,lambda:ConfigActionsMixin.preview_elimination_fx(host))
QTimer.singleShot(100,lambda:toggle(dialog.search))
QTimer.singleShot(250,lambda:toggle(dialog.search))
QTimer.singleShot(400,dialog.reject)
QTimer.singleShot(650,lambda:toggle(host))
QTimer.singleShot(800,lambda:toggle(host))
wait(5100)
row['fullscreen_transitions']=fullscreen
row['dialog_closed']=not dialog.isVisible()
row['preview_kill_count']=host.state.referee_kill_count
row['preview_pass']=row['preview_kill_count']==12 and row['play_calls']==12 and row['nonzero_pcm_bytes']>0 and fullscreen==[True,False,True,False] and row['dialog_closed']

# A closed warm pool must stop an in-flight real player and reject later play.
pool=kill.KillSoundPool(original.parent,count=1);pools.append(pool);pool._cache=None
row=new_row('close_warm_pool_rejects_later_play')
row['accepted_before_close']=pool.play(original.name,55);wait(150)
pool.close();row['states_after_close']=[str(player.playbackState()) for player,_ in pool._fallback]
before=row['play_calls'];row['accepted_after_close']=pool.play(original.name,55);wait(150)
row['close_pass']=not row['accepted_after_close'] and row['play_calls']==before and all(state=='PlaybackState.StoppedState' for state in row['states_after_close'])

result={'root':str(ROOT),'qt_version':__import__('PySide6').__version__,'audio_outputs':real_outputs,'limits':['Real Qt offscreen and FFmpeg decoded PCM; no speaker/device assertion.','KillSoundPool device-availability guard substituted because this host has no output device.','Focus cases explicitly control applicationState and emit real Qt signal; no operating-system focus claim.','Production decode completion held in worker with Event, then released before MAX_PENDING_AGE expiry.'],'fixture_sha256':{'kill_cue':hashlib.sha256(original.read_bytes()).hexdigest(),'cs2_manifest':hashlib.sha256((args.cs2_pack/'manifest.json').read_bytes()).hexdigest()},'source_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['rm_client/kill_audio.py','rm_client/ui_audio.py','rm_client/audio_preferences.py','rm_client/ui/cs2_settings_center.py']},'checks':rows}
result['pass']=all(row['stale_cue_reproduced']==args.expect_stale for row in rows[:4]) and rows[4]['closed_no_playback'] and rows[5]['accepted'] and rows[5]['muted_no_playback'] and rows[6]['nonzero_pcm_observed'] and rows[7]['preview_pass'] and (rows[8]['close_pass'] or args.expect_stale)
args.output.write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2),flush=True)
for pool in pools:close_pool(pool)
for player in players:player.stop();player.setAudioBufferOutput(None);player.setSource(ui.QUrl())
for dialog in dialogs:dialog.hide();dialog.deleteLater()
host.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);wait(50)

sys.exit(0 if result['pass'] else 1)
