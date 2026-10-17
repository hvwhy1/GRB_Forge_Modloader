"""Operation-local SHA reuse, invalidated by file identity and change metadata.

No digests survive a staging call. Installation never enables this cache.
Unsupported file metadata falls back to reading every byte normally.
"""
import collections,contextlib,contextvars,functools,os,stat

_active=contextvars.ContextVar('staging_verified_hashes',default=None)
LIMIT=16384

if os.name=='nt':
    import ctypes,msvcrt
    class BasicInfo(ctypes.Structure):
        _fields_=[('created',ctypes.c_longlong),('accessed',ctypes.c_longlong),
                  ('written',ctypes.c_longlong),('changed',ctypes.c_longlong),('attributes',ctypes.c_uint32)]
    _query=ctypes.WinDLL('kernel32',use_last_error=True).GetFileInformationByHandleEx
    _query.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32]
    _query.restype=ctypes.c_int


def version(stream):
    info=os.fstat(stream.fileno())
    if not stat.S_ISREG(info.st_mode) or not info.st_ino:return None
    changed=info.st_ctime_ns
    if os.name=='nt':
        basic=BasicInfo()
        if not _query(msvcrt.get_osfhandle(stream.fileno()),0,ctypes.byref(basic),ctypes.sizeof(basic)) or not basic.changed:return None
        changed=basic.changed
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,changed)


def current():return _active.get()


@contextlib.contextmanager
def session(enabled=True):
    if enabled and _active.get() is not None:yield;return
    token=_active.set(collections.OrderedDict() if enabled else None)
    try:yield
    finally:_active.reset(token)


def for_staging(fn):
    @functools.wraps(fn)
    def wrapped(*args,**kwargs):
        with session(enabled=not kwargs.get('install',False)):
            return fn(*args,**kwargs)
    return wrapped
