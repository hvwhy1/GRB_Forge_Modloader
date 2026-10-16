"""Interactive choices pause a private build; only current resource choices may change."""
import copy,json,threading
from pathlib import Path
import mod_manager as mm
import conflict_policy as cp

class StagingCancelled(RuntimeError):pass

class ChoiceRequest:
 def __init__(self,report,message):
  self.report=str(report);self.message=str(message);self.ready=threading.Event();self.accepted=False
 def answer(self,accepted):self.accepted=bool(accepted);self.ready.set()
 def wait(self):self.ready.wait();return self.accepted

RECOVERABLE=('SAME_ARCHIVE_CONTAINER_VARIANTS_NEED_EXACT_CHOICE','CONTAINER_OBJECT_CONFLICT_NEEDS_PRIORITY',
 'CONTAINER_OBJECT_PRIORITY_TIE','COMPETING_INNER_PAYLOADS_REQUIRE_FIELD_MERGE_OR_PRIORITY',
 'CONFLICTING INNER SCHEMAS','CONFLICTING NEW/AMBIGUOUS INNER RESOURCE','REMOVAL CONFLICTS WITH A REPLACEMENT',
 'REMOVAL/REPLACEMENT PRIORITY TIE','COMPETING EXACT CONTAINER CHOICES','COMPETING EXACT RESOURCE CHOICES')
def recoverable(message):return any(token in str(message).upper() for token in RECOVERABLE)

def resolve(report,message,callback):
 if callback is None:return False
 report=Path(report)
 if not report.is_file():return False
 groups=json.loads(report.read_text())['groups']
 allowed={g['target'] for g in groups if g['state']!='VERIFIED_COMPOSED'}
 if not allowed:return False
 before=mm.load(mm.CONFIG,{})
 if not callback(report,message):raise StagingCancelled('Staging cancelled. Completed private work is preserved; the game was not modified.')
 after=mm.load(mm.CONFIG,{})
 a=cp.build_settings(before);b=cp.build_settings(after)
 a['conflict_priorities']=b['conflict_priorities']=None
 old=copy.deepcopy(cp.settings(before));new=copy.deepcopy(cp.settings(after))
 old_choices=old.pop('resource_winners',{});new_choices=new.pop('resource_winners',{})
 if a!=b or old!=new:raise ValueError('Only resource choices may change while resuming. Other settings changed; start a fresh scan to apply them consistently.')
 changed={k for k in set(old_choices)|set(new_choices) if old_choices.get(k)!=new_choices.get(k)}
 # Replacing a legacy name-based choice with the verified binary group is safe
 # when its old winner is one of that same group's candidate members.
 members={(c['mod'],c['member']) for g in groups if g['target'] in allowed for c in g['candidates']}
 removed_legacy={k for k in changed if k not in new_choices and (old_choices[k].get('mod'),old_choices[k].get('member')) in members}
 if not changed or not changed.issubset(allowed|removed_legacy):raise ValueError('No valid choice for the waiting conflict was saved.')
 return True
