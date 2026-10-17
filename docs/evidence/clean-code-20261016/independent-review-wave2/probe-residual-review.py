import gc,json,os,tempfile,threading,weakref
from unittest.mock import patch
from rm_client.backend.dt7_worker import AsyncDt7KeyMouseLink
from rm_client.backend.cc_link import AsyncCustomControllerLink
from rm_client.backend.manager import BackendManager
from rm_client.model import RobotState,build_default_registry
from rm_client import serial_ports
results=[]
for factory in (AsyncDt7KeyMouseLink,AsyncCustomControllerLink):
 original=threading.Thread.start; references=[]; workers=[]
 failure=RuntimeError('injected after actual admission')
 def admit_then_raise(worker):
  references.append(weakref.ref(worker._target.__self__))
  workers.append(worker)
  original(worker)
  raise failure
 try:
  with patch.object(threading.Thread,'start',admit_then_raise):
   try: factory(mock_output=True)
   except RuntimeError as caught: assert caught is failure
   else: raise AssertionError('error vanished')
  failure.__traceback__=None;gc.collect()
  owner=references[0]()
  results.append({'factory':factory.__name__,'alive_after_error_and_gc':workers[0].is_alive(),'owner_survives':owner is not None,'closing':owner._closing,'closed':owner.closed})
 finally:
  owner=references[0](); owner.close(); assert owner.wait_closed(2)
with tempfile.TemporaryDirectory() as directory:
 with patch.dict(os.environ,{'POLAR_USER_DATA_ROOT':directory}):
  manager=BackendManager(workspace=directory,state=RobotState())
 with patch.object(serial_ports,'port_snapshot',return_value=[]): registry=build_default_registry()
 try:
  port=registry.get('MQTTLink').get_setting('Port')
  for raw in ('not-a-port','3333.0',3333.5,float('inf')):
   port.value=raw
   try: note=manager.sync_mqtt_link(registry); error=None
   except Exception as exc: error=type(exc).__name__;note=None
   results.append({'raw_port':str(raw),'error':error,'note':note,'displayed_port':str(port.value),'actual_port':manager.mqtt.port})
 finally:
  manager.shutdown();manager.field_log.close(1)
print(json.dumps(results,indent=2))
