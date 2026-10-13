"""Recorded endpoint RTT costs with synthetic sampling; no original wake replay."""
import sys
if not __debug__:raise SystemExit('Normal Python required')
import argparse,hashlib,json,math
from pathlib import Path


def main():
    p=argparse.ArgumentParser(allow_abbrev=False)
    for name in ('client-root','server-root','duration-json','out'):p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--expect',required=True,choices=('continued','stopped'))
    a=p.parse_args()
    if a.out.exists():raise SystemExit('Fresh result required')
    source=json.loads(a.duration_json.read_text())
    durations=source['max_recorded_duration_by_endpoint_s']
    allowed=('/v2/control_window','/v2/adapter_input','/v2/control','/v2/renew','/v2/keepalive')
    if set(durations)!=set(allowed):raise SystemExit('Unexpected duration paths')
    for v in durations.values():
        if type(v) not in (float,int) or not math.isfinite(v) or not 0<=v<=2:raise SystemExit('Invalid duration')
    c=a.client_root.absolute();s=a.server_root.absolute()
    sys.path[:0]=[str(c),str(c/'tests'),str(s)]
    from test_platform_handoff import Harness
    files={'client/'+q:c/q for q in ('rm_client/sim_platform_handoff.py','rm_client/sim_platform_transport.py','tests/test_platform_handoff.py')}
    files['server/control.py']=s/'rm_match/platform_v2/control.py'
    def hashes():return {k:hashlib.sha256(v.read_bytes()).hexdigest() for k,v in files.items()}
    before=hashes();results=[]
    for split in ('all_before_admission','all_after_admission'):
        h=Harness();start=h.now;next_sample=100.020
        (h.delay if split=='all_before_admission' else h.ack_delay).update(durations)
        iterations=0
        while h.now-start<1.0 and h.t._focus:
            iterations+=1
            if iterations>1000:raise RuntimeError('Synthetic scheduler failed to progress')
            h.now+=.005
            if h.now>=next_sample:
                h.sample()
                while next_sample<=h.now:next_sample+=.020
            h.tick()
        continued=h.t._focus and not h.client.uncertain
        events=h.t.diagnostics()['transport']
        results.append(dict(split=split,continued=continued,iterations=iterations,
            elapsed_fake_s=h.now-start,note=h.t.note,first_failure=events['first_failure'],
            requests=h.requests,requests_count=len(h.requests)))
    after=hashes();expected=a.expect=='continued'
    passed=before==after and all(r['continued']==expected for r in results)
    result=dict(passed=passed,expect=a.expect,results=results,hashes_before=before,hashes_after=after,
        input_duration_file_sha256=hashlib.sha256(a.duration_json.read_bytes()).hexdigest(),
        model=dict(qt_complete_sample_period_s=.020,worker_wake_s=.005,fake_elapsed_limit_s=1.0,
            duration_reuse='max observed RTT per endpoint repeated, including the sole recorded window request',
            transit_split='two modeled extremes, not observed one-way network delays'),
        scope='fake HTTP + actual ControlPlane only; not exact original event replay, network, physics, image, native or goal qualification')
    a.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(passed=passed,continued=[r['continued'] for r in results])))
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
