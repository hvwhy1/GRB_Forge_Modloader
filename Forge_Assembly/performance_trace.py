"""Per-run timings and I/O counters; no cached verification decisions."""
import contextvars,functools,json,os,time
from pathlib import Path

_active=contextvars.ContextVar('pipeline_performance',default=None)


def record(name,seconds=0,bytes_read=0):
    state=_active.get()
    if state is None:return
    row=state['metrics'].setdefault(name,{'calls':0,'seconds':0,'bytes':0})
    row['calls']+=1;row['seconds']+=seconds;row['bytes']+=bytes_read


def bind_run(run):
    state=_active.get()
    if state is not None:state['run']=str(run)


def timed(name):
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(*args,**kwargs):
            if _active.get() is None:return fn(*args,**kwargs)
            start=time.perf_counter()
            try:return fn(*args,**kwargs)
            finally:record(name,time.perf_counter()-start)
        return wrapped
    return decorate


def process(fn):
    @functools.wraps(fn)
    def wrapped(*args,**kwargs):
        if _active.get() is None:return fn(*args,**kwargs)
        command=args[0] if args else kwargs.get('args',[])
        name=Path(str(command[0])).stem if isinstance(command,(list,tuple)) and command else 'child'
        if name=='dotnet' and len(command)>1:
            name+='.'+(str(command[2]) if str(command[1]).lower().endswith('.dll') and len(command)>2 else str(command[1]))
        start=time.perf_counter()
        try:return fn(*args,**kwargs)
        finally:record('process.'+name,time.perf_counter()-start)
    return wrapped


def profiled(fn=None,*,filename='PERFORMANCE.json'):
    if fn is None:return lambda target:profiled(target,filename=filename)
    @functools.wraps(fn)
    def wrapped(*args,**kwargs):
        if _active.get() is not None:return fn(*args,**kwargs)
        state={'schema':'FORGE_PERFORMANCE_1','metrics':{},'completed':False}
        token=_active.set(state);start=time.perf_counter()
        try:
            result=fn(*args,**kwargs);state['completed']=True;return result
        finally:
            state['totalSeconds']=time.perf_counter()-start;_active.reset(token)
            if state.get('run'):
                try:
                    run=Path(state['run'])
                    if not run.is_dir() and run.parent.name=='auto_runs':
                        archived=run.parent.parent/'diagnostics'/'run_history'/run.name
                        if archived.is_dir():run=archived
                    target=run/filename;pending=target.with_suffix('.json.pending')
                    pending.write_text(json.dumps(state,indent=2),encoding='utf-8');os.replace(pending,target)
                except OSError:pass  # Never hide a build error with a metrics error.
    return wrapped
