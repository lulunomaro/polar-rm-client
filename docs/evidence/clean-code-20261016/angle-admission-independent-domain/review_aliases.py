"""Independent realpath aliases and normalization short-circuit trace checks."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from test_angle_port_admission_characterization import run, frozen_method
from rm_client.backend.manager import BackendManager
from rm_client.backend.controller_angles import port_key

checks=0
with TemporaryDirectory() as folder:
    actual=Path(folder)/'serial-target'
    alias=Path(folder)/'serial-alias'
    actual.touch()
    alias.symlink_to(actual)
    for enabled in (False,True):
        for forward in (False,True):
            for mock in (False,True):
                for port in (str(actual),str(alias),' Auto ','Stub','None','','COM3',' com3 '):
                    kwargs=dict(enabled=enabled,angle={'Angle RX Port':str(alias),'Angle TX Port':'COM3',
                                'Forward Angles':forward,'Angle Mock Writes':mock},
                                robot={'Link Mode':'serial','Port':port},
                                prior={'cc':{'mock_output':False,'port':'Auto'}})
                    baseline=frozen_method()
                    traces=[]
                    outputs=[]
                    for implementation in (baseline,BackendManager._sync_controller_angles):
                        observed=[]
                        def key(value):
                            observed.append(value)
                            return port_key(value)
                        if implementation is baseline:
                            baseline.__globals__['port_key']=key
                            result=run(implementation,**kwargs)
                        else:
                            with patch('rm_client.backend.manager.port_key',side_effect=key):
                                result=run(implementation,**kwargs)
                        traces.append(observed)
                        outputs.append(result)
                    assert traces[0]==traces[1],(kwargs,traces)
                    assert outputs[0]==outputs[1],(kwargs,outputs)
                    checks+=1
print(f'PASS: {checks} independent POSIX-alias/Auto/Stub and normalization-order cases')
