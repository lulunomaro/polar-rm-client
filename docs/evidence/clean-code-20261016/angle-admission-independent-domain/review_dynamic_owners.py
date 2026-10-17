"""Independent dynamic-owner and failure-prefix differential against frozen baseline."""
from dataclasses import asdict
from types import SimpleNamespace
from test_angle_port_admission_characterization import frozen_method
from rm_client.backend.manager import BackendManager


def run(method, fail_at=None, *, mutate_prior=False, conflict=True, mock=False, handoff=False):
    trace=[]
    manager=None
    def record(*event):
        trace.append(event)
        if len(trace)==fail_at:
            raise RuntimeError('injected:'+str(event))
    class Observed:
        def __init__(self,label,values):
            object.__setattr__(self,'label',label)
            object.__setattr__(self,'values',values)
        def __getattr__(self,name):
            record('read',self.label,name)
            if mutate_prior and self.label=='old_cc' and name=='mock_output':
                manager._dt7_link=Observed('replacement_dt7',{'mock_output':False,'port':'RX'})
            if name not in self.values:raise AttributeError(name)
            return self.values[name]
    def module(label,vals):
        def setting(name):
            record('setting',label,name)
            if name not in vals:return None
            return Observed(label+'.'+name,{'value':vals[name]})
        return Observed(label,{'enabled':True,'get_setting':setting})
    angle=module('angle',{'Angle RX Port':' RX ','Angle TX Port':'TX',
                         'Forward Angles':True,'Angle Mock Writes':False})
    robot=module('robot',{'Link Mode':'tcp','Allow Drive':True,
                         'Custom Controller Egress':False,'Direct DT7 Egress':False})
    def get(name):
        record('registry',name)
        return {'ControllerAngles':angle,'RobotLink':robot}[name]
    class Manager(BackendManager):
        def __getattribute__(self,name):
            if name in ('_cc_link','_dt7_link','serial','safety','controller_angles','state'):
                record('manager',name)
            return object.__getattribute__(self,name)
    manager=object.__new__(Manager)
    manager._backend_transition_blocks_work=lambda:False
    manager._cc_link=Observed('old_cc',{'mock_output':mock,'port_handoff_ready':handoff,
                                      'resolved_port':'','port':'OTHER'})
    manager._dt7_link=Observed('old_dt7',{'mock_output':False,'port_handoff_ready':False,
                                       'resolved_port':'RX' if conflict else 'OLD_OTHER','port':'Auto'})
    manager.serial=Observed('serial',{'running':True,'port':'SERIAL'})
    manager.safety=Observed('safety',{'estop_active':False})
    def configure(config,authorized):record('configure',asdict(config),authorized)
    def disarm(reason):record('disarm',reason)
    status={'fixture':'status'}
    def stats():record('stats');return status
    manager.controller_angles=Observed('relay',{'configure':configure,'disarm':disarm,'stats':stats})
    class Stats(dict):
        def __setitem__(self,key,value):
            record('publish',key,value)
            super().__setitem__(key,value)
    published=Stats()
    manager.state=Observed('state',{'backend_stats':published})
    try:result=('ok',method(manager,SimpleNamespace(get=get)))
    except Exception as exc:result=('error',type(exc).__name__,str(exc))
    return trace,result,dict(published)

baseline=frozen_method()
checks=0
for mutate in (False,True):
 for conflict in (False,True):
  for mock in (False,True):
   for handoff in (False,True,1):
    kwargs=dict(mutate_prior=mutate,conflict=conflict,mock=mock,handoff=handoff)
    expected=run(baseline,**kwargs)
    actual=run(BackendManager._sync_controller_angles,**kwargs)
    assert actual==expected,(kwargs,actual,expected)
    checks+=1
    for failure in range(1,len(expected[0])+1):
        expected_fail=run(baseline,failure,**kwargs)
        actual_fail=run(BackendManager._sync_controller_angles,failure,**kwargs)
        assert actual_fail==expected_fail,(kwargs,failure,actual_fail,expected_fail)
        checks+=1
print(f'PASS: {checks} independent dynamic-owner/read-order/failure-prefix differential cases')
