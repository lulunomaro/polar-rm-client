"""No-GL real bridge/client parser compatibility; synthetic control, no match replay."""
import argparse
import json
import sys
from pathlib import Path

def main():
    if not __debug__:raise SystemExit('Run without -O: assertions are required')
    p=argparse.ArgumentParser();p.add_argument('--bridge-root',required=True);p.add_argument('--client-root',required=True);p.add_argument('--legacy',action='store_true');a=p.parse_args()
    sys.path[:0]=[str(Path(a.client_root).absolute()),str(Path(a.bridge_root).absolute())]
    from rm_match.operator.protocol import ControlGate
    from rm_match.operator.server import BridgeState
    from rm_match.operator.runtime import OperatorRuntime
    from rm_match.operator.test_protocol import Clock
    from rm_client.sim_operator_transport import SimOperatorTransport
    clock=Clock();state=BridgeState(ControlGate(wall=clock,monotonic=clock,session_id='synthetic-parser-check'))
    runtime=OperatorRuntime(state,render=False);client=SimOperatorTransport(clock=clock);count=0
    try:
        runtime.step_once();clock.advance(runtime.sim.model.opt.timestep);runtime._publish();session=state.session();client.install_session(session)
        assert client.install_telemetry(state.snapshot(),session);count+=1
        seq=0
        for mode in (['free'] if a.legacy else ['free','gimbal','field']):
            for focus in (False,True,False):
                seq+=1
                body=dict(session_id=session['session_id'],epoch=session['epoch'],robot_id=session['robot_id'],seq=seq,sent_at_ms=clock()*1000,focus=focus,deadman=focus,vx_mps=0.,vy_mps=0.,yaw_rate_rps=0.,pitch_rate_rps=0.,fire=False,mode_request=dict(spin_enabled=False,spin_direction='auto',spin_speed_dps=420.,chassis_follow=mode))
                if not a.legacy:body['drive_axes']=dict(forward_mps=.2 if focus else 0.,left_mps=0.)
                assert state.gate.accept(body)['accepted']
                runtime.step_once();clock.advance(runtime.sim.model.opt.timestep);runtime._publish()
                assert client.install_telemetry(state.snapshot(),session);count+=1
        print(json.dumps(dict(qualified='parser_roundtrip_only',legacy=a.legacy,checks=count,render=False)))
    finally:runtime.close()

if __name__=='__main__':main()
