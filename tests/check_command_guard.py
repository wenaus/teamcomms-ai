"""Focused local guard checks with synthetic service responses and child processes.
No native AI, deployed service, credentials or full suite.
"""
import asyncio
from contextlib import suppress
from datetime import datetime,timedelta,timezone
import json,os,signal,subprocess,sys,tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from teamcomms.connectors.guard import run_guard,resource_locks


class Service:
    def __init__(self,resource,fail_renew=False):
        self.http=SimpleNamespace();self.resource=resource;self.fail_renew=fail_renew;self.renewals=0;self.runs=[]
    async def close(self):pass
    async def request(self,method,path,body):
        claim={'policy':'guarded','resources':[{'resource_id':self.resource,'host':'synthetic','protection':'local_flock'}],
               'lease_seconds':30,'server_time':datetime.now(timezone.utc).isoformat(),'deadline':(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat()}
        if path.endswith('/update'):
            self.renewals+=1
            if self.fail_renew and self.renewals>1:raise RuntimeError('Synthetic renewal unavailable')
        if path.endswith('/guard'):self.runs.append(body)
        return {'claim':claim,'valid':True}


async def check(root):
    resource=str(uuid4());configfile=root/'guard.json';lockdir=root/'locks'
    configfile.write_text(json.dumps({'host':'synthetic','lock_dir':str(lockdir),'resource_ids':[resource]}));configfile.chmod(0o600)
    config=SimpleNamespace(host='synthetic')
    def args(command):return SimpleNamespace(guard_config=configfile,claim_id=str(uuid4()),generation=2,resource=[resource],arguments=command)
    service=Service(resource)
    assert await run_guard(args([sys.executable,'-c','pass']),config,service=service)==0
    assert [r['action'] for r in service.runs]==['start','finish'] and service.runs[-1]['stopped']
    # A second process cannot acquire locks held by the first, even if an API
    # incorrectly admitted it. No partial lock set leaks on failure.
    with resource_locks(lockdir,[resource]):
        child=subprocess.run([sys.executable,'-c',
            'from pathlib import Path;from teamcomms.connectors.guard import resource_locks;import sys\nwith resource_locks(Path(sys.argv[1]),[sys.argv[2]]):pass',str(lockdir),resource],capture_output=True)
        assert child.returncode!=0 and b'BlockingIOError' in child.stderr
    with resource_locks(lockdir,[resource]):pass
    # Renewal failure stops this check's own child and records its stopped group.
    marker=root/'pid'
    failing=Service(resource,fail_renew=True)
    try:
        await run_guard(args([sys.executable,'-c','import os,sys,time;open(sys.argv[1],"w").write(str(os.getpid()));time.sleep(60)',str(marker)]),config,service=failing)
    except RuntimeError as error:assert 'renewal unavailable' in str(error)
    else:raise AssertionError('Expected renewal failure')
    assert failing.runs[-1]['action']=='finish' and failing.runs[-1]['stopped']
    pid=int(marker.read_text())
    try:os.kill(pid,0)
    except ProcessLookupError:pass
    else:raise AssertionError('Guard child survived failed renewal')
    # The child inherits the descriptor: killing only its wrapper leaves the
    # resource locked until that child is terminated.
    inherited=root/'inherited-pid'
    code='''import asyncio,json,os,sys,time
from pathlib import Path
from teamcomms.connectors.guard import resource_locks
import subprocess
with resource_locks(Path(sys.argv[1]),[sys.argv[2]]) as fds:
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],pass_fds=tuple(fds),start_new_session=True)
    Path(sys.argv[3]).write_text(str(child.pid))
    time.sleep(60)
'''
    wrapper=subprocess.Popen([sys.executable,'-c',code,str(lockdir),resource,str(inherited)])
    child_pid=None
    try:
        for _ in range(100):
            if inherited.exists():break
            await asyncio.sleep(.02)
        child_pid=int(inherited.read_text());wrapper.kill();wrapper.wait()
        try:
            with resource_locks(lockdir,[resource]):pass
        except BlockingIOError:pass
        else:raise AssertionError('Wrapper death released inherited lock')
    finally:
        if wrapper.poll() is None:wrapper.kill();wrapper.wait()
        if child_pid:
            with suppress(ProcessLookupError):os.killpg(child_pid,signal.SIGKILL)
    print('PASS: guarded foreground execution, local exclusion, renewal-failure process stop, durable finish report and inherited lock after wrapper death; no native or suite')


with tempfile.TemporaryDirectory(prefix='tc-command-guard-') as directory:asyncio.run(check(Path(directory)))
