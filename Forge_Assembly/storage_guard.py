"""Budget checks around private writes and owned child processes only."""
import contextlib,contextvars,functools,shutil,subprocess,threading,time
from pathlib import Path
import workspace_storage as ws
import performance_trace as perf

_active=contextvars.ContextVar('storage_budget',default=None)

class Budget:
    def __init__(self,work,used,limit,free=None):
        self.work=Path(work);self.used=used;self.limit=limit
        self.free=free or (lambda:shutil.disk_usage(work).free)
        self.start_free=self.free();self.failure=None
    def check(self):
        free=self.free()
        if self.failure:raise ws.StorageError(self.failure)
        if free<20*ws.GIB:self.failure='Storage limit: fewer than 20 GiB remain free. Staging stopped; free space before retrying.'
        elif self.used+max(0,self.start_free-free)>self.limit:self.failure=f'Storage limit: the workspace reached its {self.limit/ws.GIB:g} GiB budget. Staging stopped and disposable work will be cleaned. Adjust the storage budget in Diagnostics only if this build requires more space.'
        if self.failure:raise ws.StorageError(self.failure)

def check():
    budget=_active.get()
    if budget:budget.check()

def compact(folder,keep=()):
    budget=_active.get()
    if not budget:return
    # Proof JSON remains; the newly produced Forge is already linked into the
    # current view before this runs. No per-step full-Forge cache is retained.
    ws.prune(budget.work,[folder],{str(Path(p).resolve()).lower() for p in keep})
    check()

class Popen(subprocess.Popen):
    def __init__(self,*args,**kwargs):
        self._budget=_active.get();self._storage_error=None;self._monitor_done=threading.Event()
        check();super().__init__(*args,**kwargs)
        if self._budget:
            def watch():
                while not self._monitor_done.wait(.25):
                    if self.poll() is not None:return
                    try:self._budget.check()
                    except ws.StorageError as exc:
                        self._storage_error=exc
                        if self.poll() is None:self.kill()
                        return
            threading.Thread(target=watch,daemon=True).start()
    def wait(self,*args,**kwargs):
        value=super().wait(*args,**kwargs);self._monitor_done.set()
        if self._storage_error:raise self._storage_error
        if self._budget:self._budget.check()
        return value

@perf.process
def run(*args,input=None,capture_output=False,timeout=None,check=False,**kwargs):
    if _active.get() is None:return subprocess.run(*args,input=input,capture_output=capture_output,timeout=timeout,check=check,**kwargs)
    if input is not None:kwargs['stdin']=subprocess.PIPE
    if capture_output:kwargs.update(stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    with Popen(*args,**kwargs) as process:
        try:stdout,stderr=process.communicate(input,timeout=timeout)
        except BaseException:
            if process.poll() is None:process.kill()
            raise
        code=process.poll()
        if check and code:raise subprocess.CalledProcessError(code,process.args,stdout,stderr)
        return subprocess.CompletedProcess(process.args,code,stdout,stderr)

@contextlib.contextmanager
def session(work,cfg,log=lambda _:None):
    with ws.lease(work):
        result=ws.cleanup(work,cfg,log=log)
        limit=ws.limit_bytes(cfg)
        budget=Budget(work,result['uniqueBytes'],limit);budget.check()
        token=_active.set(budget)
        try:yield budget
        finally:_active.reset(token)

def suspended(fn):
    """Never interrupt an atomic live install or its rollback with a scratch guard."""
    @functools.wraps(fn)
    def wrapped(*args,**kwargs):
        token=_active.set(None)
        try:return fn(*args,**kwargs)
        finally:_active.reset(token)
    return wrapped
