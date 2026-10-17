"""Independent exception precedence and four-adapter stop-prefix checks."""
import subprocess
import sys
from unittest.mock import patch
from rm_client.configuration.quiescence import quiesce_configuration_inputs
from test_configuration_quiescence import Host

names=('lock','release','clear')
for mask in range(8):
    events=[]
    failures={name:RuntimeError(name) for i,name in enumerate(names) if mask & (1<<i)}
    def action(name):
        def run():
            events.append(name)
            if name in failures: raise failures[name]
        return run
    caught=None
    with patch('rm_client.configuration.quiescence._LOG.error') as log:
        try:
            quiesce_configuration_inputs(revoke=action('lock'), release=action('release'),
                                         clear_pending=action('clear'))
        except RuntimeError as error:
            caught=error
        expected=['lock','clear'] if 'lock' in failures else ['lock','release','clear']
        assert events==expected,(mask,events)
        primary=next((failures[n] for n in expected if n in failures),None)
        assert caught is primary,(mask,caught,primary)
        secondary='clear' in failures and ('lock' in failures or 'release' in failures)
        assert log.call_count==int(secondary)
        if secondary: assert any('clear' in note for note in caught.__notes__)
for caller in ('match','robot','binds','reload'):
    for mask in range(1,8):
        failed={name for i,name in enumerate(names) if mask & (1<<i)}
        host=Host(failed)
        with patch('rm_client.configuration.quiescence._LOG.error'):
            try: host.invoke(caller)
            except RuntimeError as error:
                first='lock' if 'lock' in failed else ('release' if 'release' in failed else 'clear')
                assert error is host.errors[first]
            else: raise AssertionError((caller,mask,'mutation admitted'))
        assert host.trace==(['lock','clear'] if 'lock' in failed else ['lock','release','clear'])
        host.match_mode_manager.set_mode.assert_not_called()
        host.backend.set_client_id.assert_not_called()
        host.control.set_binds.assert_not_called()
program='''
import importlib.abc,sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.startswith(('PySide6','tkinter','rm_client.backend','rm_client.model','rm_client.ui')):
   raise AssertionError(fullname)
sys.meta_path.insert(0,Block())
from rm_client.configuration.quiescence import quiesce_configuration_inputs
quiesce_configuration_inputs(revoke=lambda:None,release=lambda:None,clear_pending=lambda:None)
'''
result=subprocess.run([sys.executable,'-c',program],capture_output=True,text=True)
assert result.returncode==0,result.stderr
print('PASS: all 8 helper outcome combinations, all 28 failing adapter combinations, exact primary exception identity and secondary diagnostic accounting; runtime import blocking')
