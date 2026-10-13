"""Run the fixed pure-CPU handoff matrix with real ControlPlane, no socket/GL."""
import sys
if not __debug__:
    raise SystemExit('Normal Python required for qualification')
import argparse
import hashlib
import io
import json
from pathlib import Path
import unittest


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--client-root',required=True,type=Path)
    p.add_argument('--server-root',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=False)
    c=a.client_root.absolute();s=a.server_root.absolute()
    paths={f'client/{f}':c/f for f in ('rm_client/sim_platform_handoff.py',
        'rm_client/sim_platform_transport.py','rm_client/sim_platform_client.py',
        'rm_client/sim_platform_diagnostics.py','tests/test_platform_handoff.py','tests/test_platform_handoff_lookahead.py')}
    paths['server/rm_match/platform_v2/control.py']=s/'rm_match/platform_v2/control.py'
    def hashes():return {name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in paths.items()}
    before=hashes()
    sys.path[:0]=[str(c),str(c/'tests'),str(s)]
    import test_platform_handoff,test_platform_handoff_lookahead
    stream=io.StringIO()
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(
        unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromModule(m) for m in (test_platform_handoff,test_platform_handoff_lookahead)]))
    after=hashes()
    passed=result.wasSuccessful() and before==after
    (a.out/'tests.log').write_text(stream.getvalue(),encoding='utf-8')
    report=dict(passed=passed,tests=result.testsRun,hashes_before=before,hashes_after=after,
        scope='pure fake-clock/fake HTTP and actual ControlPlane; no socket, Qt application, image, physics or native algorithm',
        historical_navigation_failure_cause='not established by this matrix')
    (a.out/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(passed=passed,tests=result.testsRun)))
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
