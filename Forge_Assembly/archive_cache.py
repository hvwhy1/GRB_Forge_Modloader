"""Bounded ZIP reader reuse during composition, with final SHA verification."""
import collections,contextlib,contextvars,functools,hashlib,os,time,zipfile
from pathlib import Path
import performance_trace as perf

_active=contextvars.ContextVar('zip_readers',default=None)


def signature(path):
    s=os.stat(path);return s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns


class Readers:
    def __init__(self,limit=8):self.limit=limit;self.entries=collections.OrderedDict()
    def release(self,key,verify=True):
        entry=self.entries.pop(key);entry['reader'].close()
        if verify:
            started=time.perf_counter()
            h=hashlib.sha256()
            with open(key[0],'rb') as stream:
                for block in iter(lambda:stream.read(4*1024**2),b''):h.update(block)
            if signature(key[0])!=entry['signature'] or h.hexdigest()!=key[1]:
                raise ValueError('ZIP archive changed during composition: '+key[0])
            perf.record('zip.source_reverified',time.perf_counter()-started,entry['signature'][2])
    def get(self,path,digest):
        key=(str(Path(path).resolve()),digest.lower())
        if key in self.entries:
            entry=self.entries[key]
            if signature(path)!=entry['signature']:raise ValueError('ZIP archive changed during composition: '+str(path))
            self.entries.move_to_end(key);perf.record('zip.reader_reuse');return entry['reader']
        while len(self.entries)>=self.limit:self.release(next(iter(self.entries)))
        before=signature(path);reader=zipfile.ZipFile(path)
        if signature(path)!=before:reader.close();raise ValueError('ZIP archive changed while opening: '+str(path))
        self.entries[key]={'reader':reader,'signature':before};perf.record('zip.reader_open')
        return reader
    def close(self,verify=True):
        error=None
        for key in list(self.entries):
            try:self.release(key,verify)
            except Exception as exc:error=error or exc
        if error:raise error


@contextlib.contextmanager
def session():
    if _active.get() is not None:yield _active.get();return
    readers=Readers();token=_active.set(readers);success=False
    try:yield readers;success=True
    finally:
        _active.reset(token);readers.close(verify=success)


def cached(fn):
    @functools.wraps(fn)
    def wrapped(*args,**kwargs):
        with session():return fn(*args,**kwargs)
    return wrapped


@contextlib.contextmanager
def open_zip(path,digest):
    readers=_active.get()
    if readers is not None:yield readers.get(path,digest)
    else:
        with zipfile.ZipFile(path) as reader:yield reader
