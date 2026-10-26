"""Read-only per-process Windows GPU engine counters; unavailable is not zero."""
import ctypes as C
import os
import sys


class GpuEngineSampler:
    def __init__(self):
        self.error = ''
        self.query = C.c_void_p()
        self.counter = C.c_void_p()
        self.dll = None
        if sys.platform != 'win32':
            self.error = 'Windows PDH unavailable'
            return
        try:
            self.dll = C.WinDLL('pdh.dll')
            self.dll.PdhOpenQueryW.argtypes = [C.c_wchar_p, C.c_size_t, C.POINTER(C.c_void_p)]
            self.dll.PdhAddEnglishCounterW.argtypes = [C.c_void_p, C.c_wchar_p, C.c_size_t, C.POINTER(C.c_void_p)]
            self.dll.PdhCollectQueryData.argtypes = [C.c_void_p]
            self.dll.PdhCloseQuery.argtypes = [C.c_void_p]
            self.dll.PdhGetFormattedCounterArrayW.argtypes = [C.c_void_p, C.c_uint32,
                C.POINTER(C.c_uint32), C.POINTER(C.c_uint32), C.c_void_p]
            if self.dll.PdhOpenQueryW(None, 0, C.byref(self.query)):
                raise OSError('PdhOpenQuery failed')
            if self.dll.PdhAddEnglishCounterW(self.query, r'\GPU Engine(*)\Utilization Percentage', 0, C.byref(self.counter)):
                raise OSError('GPU Engine counter unavailable')
            self.dll.PdhCollectQueryData(self.query)
        except (OSError, AttributeError) as exc:
            self.error = str(exc)
            self.close()

    def sample(self):
        if not self.query:
            return None
        class Value(C.Structure):
            _fields_ = [('status', C.c_uint32), ('value', C.c_double)]
        class Item(C.Structure):
            _fields_ = [('name', C.c_wchar_p), ('value', Value)]
        self.dll.PdhCollectQueryData(self.query)
        size, count = C.c_uint32(), C.c_uint32()
        function = self.dll.PdhGetFormattedCounterArrayW
        result = function(self.counter, 0x200 | 0x8000, C.byref(size), C.byref(count), None)
        if result & 0xffffffff != 0x800007d2 or not 0 < size.value <= 1024*1024:
            self.error = f'GPU Engine read unavailable: 0x{result & 0xffffffff:08x}'
            return None
        for _ in range(3):
            if not 0 < size.value <= 1024*1024:
                self.error='GPU Engine array exceeds budget'
                return None
            # Engine instances can appear between the size query and read.
            # Reserve headroom; on MORE_DATA re-query from zero rather than
            # trusting the returned size of an undersized nonzero buffer.
            size.value=min(1024*1024,size.value*2+4096)
            buffer = C.create_string_buffer(size.value)
            result=function(self.counter, 0x200 | 0x8000, C.byref(size), C.byref(count), buffer)
            if result==0:break
            if result & 0xffffffff != 0x800007d2:
                self.error=f'GPU Engine read unavailable: 0x{result & 0xffffffff:08x}'
                return None
            size.value=0
            result=function(self.counter,0x200|0x8000,C.byref(size),C.byref(count),None)
            if result & 0xffffffff != 0x800007d2:
                self.error='GPU Engine size query failed during retry'
                return None
        else:
            self.error='GPU Engine counter changed during bounded retries'
            return None
        self.error=''
        prefix = f'pid_{os.getpid()}_'
        items = C.cast(buffer, C.POINTER(Item))
        return {item.name: item.value.value for item in items[:min(count.value,4096)]
                if item.value.status in (0,1) and item.name and item.name.startswith(prefix)
                and any(kind in item.name for kind in ('engtype_3D','engtype_VideoDecode'))}

    def close(self):
        if self.query and self.dll is not None:
            self.dll.PdhCloseQuery(self.query)
        self.query = C.c_void_p()
