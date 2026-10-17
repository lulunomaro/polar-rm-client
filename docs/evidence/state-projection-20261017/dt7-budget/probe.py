import importlib.util, json, time, unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('dt7_test','tests/test_dt7_worker_dispatch.py')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
rows=[]
def wrap(name, original):
    def traced(self,*args,**kwargs):
        wall=time.perf_counter_ns(); cpu=time.thread_time_ns()
        try: return original(self,*args,**kwargs)
        finally: rows.append({'phase':name,'wall_ms':(time.perf_counter_ns()-wall)/1e6,'thread_cpu_ms':(time.thread_time_ns()-cpu)/1e6})
    return traced
names=('poll','send','release')
with patch.object(m.AsyncDt7KeyMouseLink,'poll',wrap('poll',m.AsyncDt7KeyMouseLink.poll)), patch.object(m.AsyncDt7KeyMouseLink,'send',wrap('send',m.AsyncDt7KeyMouseLink.send)), patch.object(m.AsyncDt7KeyMouseLink,'release',wrap('release',m.AsyncDt7KeyMouseLink.release)):
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([m.DT7WorkerTests('test_no_device_real_time_100hz_tick_retry_budget')]))
print(json.dumps({'phase_summary':{name:{'count':len(r:=[x for x in rows if x['phase']==name]),'slowest':sorted(r,key=lambda x:x['wall_ms'],reverse=True)[:5]} for name in names}},indent=2))
raise SystemExit(not result.wasSuccessful())
