"""Opt-in foreground command fencing for cooperating local entrypoints."""
import asyncio
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field
from .client import ServiceClient
from .state import private_directory


class GuardConfiguration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    host: str = Field(min_length=1,max_length=160)
    lock_dir: Path
    resource_ids: list[UUID] = Field(min_length=1,max_length=100)


def load_guard(path):
    path=Path(path).expanduser()
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077:
        raise ValueError('Guard configuration must be a private file owned by this user')
    value=GuardConfiguration.model_validate_json(path.read_text())
    if not value.lock_dir.is_absolute():raise ValueError('Guard lock directory must be absolute and shared by all cooperating invocations')
    return value


@contextmanager
def resource_locks(root, resource_ids):
    root=private_directory(root)
    directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    descriptors=[]
    try:
        for resource in sorted(resource_ids):
            fd=os.open(str(UUID(resource))+'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600,dir_fd=directory)
            descriptors.append(fd)
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077:
                raise ValueError('Resource lock must be private and owned by this user')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield descriptors
    finally:
        for fd in reversed(descriptors):os.close(fd)
        os.close(directory)


async def stop_group(process):
    try:os.killpg(process.pid,signal.SIGTERM)
    except ProcessLookupError:return
    try:await asyncio.wait_for(process.wait(),5)
    except asyncio.TimeoutError:pass
    # A foreground leader can exit before its helpers. Kill its remaining group
    # before recording the stop; daemonized/escaped processes are outside this guard.
    try:os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError:pass
    await process.wait()


async def run_guard(args, config, *, service=None):
    guard=load_guard(args.guard_config)
    resources=sorted(str(UUID(x)) for x in args.resource)
    if not resources or len(resources)!=len(set(resources)) or not set(resources)<=set(map(str,guard.resource_ids)):
        raise ValueError('Explicit distinct resources must be in the local guard allowlist')
    if guard.host!=config.host:raise ValueError('Connector and guard host differ')
    command=args.arguments[1:] if args.arguments[:1]==['--'] else args.arguments
    if not command:raise ValueError('Guard requires one foreground command')
    identity={'claim_id':str(UUID(args.claim_id)),'expected_generation':args.generation}
    service=service or ServiceClient(config)
    service.http.timeout=httpx.Timeout(3.0)
    stop=asyncio.Event();loop=asyncio.get_running_loop()
    for sig in (signal.SIGINT,signal.SIGTERM):loop.add_signal_handler(sig,stop.set)
    run_id=str(uuid4());process=None;started=False;waiter=None;stop_wait=None
    async def post(path,payload):return await service.request('POST','/api/inflight'+path,payload)
    async def mutate(path,payload):return await post(path,{'operation_id':str(uuid4()),**payload})
    async def validate():
        result=await post('/claims/validate',{**identity,'resource_ids':resources})
        claim=result['claim']
        records={r['resource_id']:r for r in claim['resources']}
        if claim['policy']!='guarded' or any(records[key]['host']!=guard.host or records[key]['protection']!='local_flock' for key in resources):
            raise ValueError('Claim does not cover guarded local resources on this host')
        return claim
    try:
        await validate()
        with resource_locks(guard.lock_dir,resources) as descriptors:
            await validate()
            claim=(await mutate('/claims/update',{**identity,'action':'renew'}))['claim']
            lease_received=loop.time()
            await mutate('/claims/guard',{**identity,'action':'start','run_id':run_id,'resource_ids':resources,
                'command_sha256':hashlib.sha256(json.dumps(command,ensure_ascii=False).encode()).hexdigest()})
            started=True
            try:
                if stop.is_set():raise RuntimeError('Stopped before command launch')
                # Start only with time to detect authority loss and stop before
                # expiry. Compare two server timestamps, avoiding host clock skew.
                remaining=(datetime.fromisoformat(claim['deadline'])-datetime.fromisoformat(claim['server_time'])).total_seconds()-(loop.time()-lease_received)
                if remaining<20:raise RuntimeError('Insufficient lease remaining to start guarded command')
                process=await asyncio.create_subprocess_exec(*command,start_new_session=True,pass_fds=tuple(descriptors))
                waiter=asyncio.create_task(process.wait());stop_wait=asyncio.create_task(stop.wait())
                interval=min(10,claim['lease_seconds']/3)
                while True:
                    done,_=await asyncio.wait({waiter,stop_wait},timeout=interval,return_when=asyncio.FIRST_COMPLETED)
                    if stop_wait in done:raise RuntimeError('Guard stopped; terminating command process group')
                    if waiter in done:break
                    await mutate('/claims/update',{**identity,'action':'renew'})
                code=process.returncode
                return code if code>=0 else 128-code
            finally:
                if process is not None:await stop_group(process)
                for task in (waiter,stop_wait):
                    if task and not task.done():task.cancel()
                if started:
                    await mutate('/claims/guard',{**identity,'action':'finish','run_id':run_id,'stopped':True,
                        'exit_code':process.returncode if process else None})
    finally:
        for sig in (signal.SIGINT,signal.SIGTERM):loop.remove_signal_handler(sig)
        await service.close()
