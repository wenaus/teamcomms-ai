"""Focused stage12 checks: atomic claims, expiry holds, fencing and guard records.
Private PostgreSQL and synthetic identities only; no full suite or native runtime.
"""
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4,UUID
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
import tempfile, subprocess, os


def check():
    import django;django.setup()
    from django.core.management import call_command
    from django.db import connections,close_old_connections,transaction,IntegrityError
    from django.utils import timezone
    from teamcomms.service.access import authenticate,AccessError
    from teamcomms.service.operations import bootstrap
    from teamcomms.service.models import Participant,Membership
    from teamcomms.comms.directory import register_resource,register_session
    from teamcomms.comms.schemas import NewResource,RegisterSession
    from teamcomms.inflight.operations import create_work,mutate_work,get_work
    from teamcomms.inflight.schemas import CreateWork,MutateWork,ReadWork
    from teamcomms.entries.schemas import State,Page
    from teamcomms.inflight.claim_schemas import ManageResource,OfferWork,ClaimWork,UpdateClaim,ValidateClaim,GuardRunRequest,ReadClaim
    from teamcomms.inflight.claims import manage_work_resource,offer_work,claim_work,update_claim,validate_claim,record_guard_run,list_work_resources,get_claim
    from teamcomms.inflight.models import Reservation,Claim,CoordinationReceipt,WorkOffer
    call_command('migrate',verbosity=0)
    call_command('makemigrations',check=True,dry_run=True,verbosity=0)
    _,token=bootstrap('Synthetic claims team','Custodian')
    admin=authenticate(token);owner=replace(admin,role='member')
    p=Participant.objects.create(name='Second worker',kind='human');m=Membership.objects.create(team_id=owner.team_id,participant=p,role='member')
    worker=replace(owner,participant_id=p.id,membership_id=m.id)
    def deny(fn,code):
        try:fn()
        except AccessError as e:assert e.status==code,str(e)
        else:raise AssertionError('Expected denial')
    def race(fn):
        barrier=Barrier(2)
        def run(i):
            close_old_connections()
            try:barrier.wait(5);return fn(i)
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(run,range(2)))
    resources=[]
    for n in range(3):
        raw=register_resource(admin,NewResource(key=f'guard-{n}',kind='service',host='synthetic',name=f'Guard {n}'))
        req=ManageResource(operation_id=uuid4(),action='provision',resource_id=raw['resource_id'],custodian_id=owner.participant_id,protection='local_flock')
        deny(lambda:manage_work_resource(owner,req),403)
        resources.append(manage_work_resource(admin,req)['resource_id'])
    def offer(title,selected,eligible=None):
        w=create_work(owner,CreateWork(operation_id=uuid4(),state=State(title=title)))
        return offer_work(owner,OfferWork(operation_id=uuid4(),entry_id=w['entry_id'],expected_revision=w['revision'],expected_generation=w['work']['generation'],
            resource_ids=selected,eligible_participant_ids=eligible or [owner.participant_id,worker.participant_id],expires_at=timezone.now()+timedelta(hours=1),policy='guarded',lease_seconds=30))
    a,b=offer('A',resources[:2]),offer('B',resources[1:])
    def take(actor,w):
        return ClaimWork(operation_id=uuid4(),offer_id=w['offer']['offer_id'],expected_revision=w['revision'],expected_generation=w['work']['generation'])
    requests=[take(owner,a),take(worker,b)]
    def compete(n):
        try:return claim_work([owner,worker][n],requests[n])
        except AccessError as e:return e.status
    result=race(compete);assert sum(isinstance(r,dict) for r in result)==1
    winner=next(i for i,r in enumerate(result) if isinstance(r,dict));actor=[owner,worker][winner];won=result[winner];lost=[a,b][1-winner]
    c=won['claim'];assert Reservation.objects.filter(active=True).count()==2
    assert not Claim.objects.filter(work_id=lost['entry_id']).exists()
    assert claim_work(actor,requests[winner])==won
    assert won['work']['owner_id']==str(owner.participant_id)
    identity={'claim_id':c['claim_id'],'expected_generation':c['generation']}
    assert validate_claim(actor,ValidateClaim(**identity,resource_ids=[r['resource_id'] for r in c['resources']]))['valid']
    deny(lambda:validate_claim(replace(actor,team_id=uuid4()),ValidateClaim(**identity)),404)
    deny(lambda:mutate_work(owner,MutateWork(operation_id=uuid4(),entry_id=won['entry_id'],expected_revision=won['revision'],expected_generation=c['generation'],change={'action':'assign_executor','participant_id':owner.participant_id})),409)
    deny(lambda:mutate_work(owner,MutateWork(operation_id=uuid4(),entry_id=won['entry_id'],expected_revision=won['revision'],expected_generation=c['generation'],change={'action':'transition','state':'completed','outcome':'bypass','evidence':['x']})),409)
    renew=UpdateClaim(operation_id=uuid4(),**identity,action='renew')
    renewed=race(lambda _:update_claim(actor,renew));assert renewed[0]==renewed[1]
    assert get_work(owner,ReadWork(entry_id=won['entry_id']))['revision']==won['revision']
    run=GuardRunRequest(operation_id=uuid4(),**identity,action='start',run_id=uuid4(),resource_ids=[r['resource_id'] for r in c['resources']],command_sha256='a'*64)
    record_guard_run(actor,run)
    deny(lambda:record_guard_run(actor,run.model_copy(update={'operation_id':uuid4(),'run_id':uuid4()})),409)
    deny(lambda:update_claim(actor,UpdateClaim(operation_id=uuid4(),**identity,expected_revision=won['revision'],action='release',reason='Not stopped yet')),409)
    from unittest.mock import patch
    future=timezone.now()+timedelta(minutes=2)
    with patch('teamcomms.inflight.claims.timezone.now',return_value=future):
        assert get_claim(actor,ReadClaim(claim_id=c['claim_id']))['state']=='expired_held'
        deny(lambda:validate_claim(actor,ValidateClaim(**identity)),409)
        deny(lambda:update_claim(actor,UpdateClaim(operation_id=uuid4(),**identity,action='renew')),409)
        deny(lambda:update_claim(actor,UpdateClaim(operation_id=uuid4(),**identity,expected_revision=won['revision'],action='complete',outcome='late',evidence=['late'])),409)
        deny(lambda:claim_work([worker,owner][winner],requests[1-winner]),409)
        assert Reservation.objects.filter(active=True).count()==2
        deny(lambda:update_claim(owner,UpdateClaim(operation_id=uuid4(),**identity,expected_revision=won['revision'],action='confirm_stopped',reason='Unsure')),400)
        released=update_claim(owner,UpdateClaim(operation_id=uuid4(),**identity,expected_revision=won['revision'],action='confirm_stopped',reason='Prior process group stopped',evidence=['Synthetic process exit receipt']))
    assert Reservation.objects.filter(active=True).count()==0
    assert released['work']['owner_id']==str(owner.participant_id)
    deny(lambda:update_claim(actor,renew.model_copy(update={'operation_id':uuid4()})),409)
    deny(lambda:record_guard_run(actor,GuardRunRequest(operation_id=uuid4(),**identity,action='finish',run_id=run.run_id,stopped=True,exit_code=0)),409)
    # A new offer/claim advances both work and resource generations.
    new_offer=offer_work(owner,OfferWork(operation_id=uuid4(),entry_id=released['entry_id'],expected_revision=released['revision'],expected_generation=released['work']['generation'],
        eligible_participant_ids=[worker.participant_id],resource_ids=[r['resource_id'] for r in c['resources']],expires_at=timezone.now()+timedelta(hours=1),policy='guarded'))
    next_work=claim_work(worker,take(worker,new_offer));new_claim=next_work['claim']
    assert new_claim['generation']>c['generation']
    assert all(r['reservation_generation']>old['reservation_generation'] for r,old in zip(new_claim['resources'],c['resources']))
    done=update_claim(worker,UpdateClaim(operation_id=uuid4(),claim_id=new_claim['claim_id'],expected_generation=new_claim['generation'],expected_revision=next_work['revision'],action='complete',outcome='Finished',evidence=['Synthetic completion receipt']))
    assert done['work']['state']=='completed' and not Reservation.objects.filter(active=True).exists()
    # Custody remains across idle and held periods, and only the named successor accepts.
    resource=list_work_resources(owner,Page())['resources'][0]
    offered=manage_work_resource(owner,ManageResource(operation_id=uuid4(),action='offer_transfer',resource_id=resource['resource_id'],expected_generation=resource['generation'],successor_id=worker.participant_id,reason='New coordinator'))
    transfer=ManageResource(operation_id=uuid4(),action='accept_transfer',resource_id=resource['resource_id'],expected_generation=resource['generation'],transfer_id=offered['transfer']['transfer_id'])
    deny(lambda:manage_work_resource(owner,transfer),403)
    accepted=manage_work_resource(worker,transfer)
    assert accepted['custodian_id']==str(worker.participant_id) and accepted['generation']==resource['generation']+1
    deny(lambda:manage_work_resource(worker,transfer.model_copy(update={'operation_id':uuid4()})),409)
    for fn in (lambda:CoordinationReceipt.objects.filter(pk=renew.operation_id).update(result={}),lambda:Reservation.objects.filter(claim_id=c['claim_id']).update(active=True)):
        try:
            with transaction.atomic():fn()
        except IntegrityError:pass
        else:raise AssertionError('Expected immutable coordination guard')
    from starlette.testclient import TestClient
    from teamcomms.service.asgi import create_app
    with TestClient(create_app(mount_path='/nested/tc')) as client:
        client.headers['Authorization']='Bearer '+token
        assert client.get('/nested/tc/api/inflight/resources').status_code==200
        response=client.post('/nested/tc/mcp/',headers={'Accept':'application/json, text/event-stream'},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'get_claim','arguments':{'request':{'claim_id':new_claim['claim_id']}}}})
        assert response.status_code==200 and not response.json()['result'].get('isError'),response.text
    connections.close_all()
    print('PASS: all-or-none resource races, claim idempotency/generations, custody acceptance, expiry holds, stopped-work reconciliation, guarded-run exclusion, completion evidence, immutable receipts and HTTP/MCP; no suite')


if __name__=='__main__':
    bindir=Path(subprocess.check_output(['pg_config','--bindir'],text=True).strip())
    with tempfile.TemporaryDirectory(prefix='tc-claims-check-') as directory:
        root=Path(directory);sock=root/'socket';sock.mkdir()
        os.environ.update(TEAMCOMMS_DATABASE_URL=f'postgresql:///postgres?host={sock}',TEAMCOMMS_SECRET_KEY='synthetic-claims',TEAMCOMMS_ALLOWED_HOSTS='testserver,localhost',DJANGO_SETTINGS_MODULE='teamcomms.service.settings')
        subprocess.run([str(bindir/'initdb'),'-D',str(root/'data'),'--auth=trust','--no-locale','--encoding=UTF8'],check=True,stdout=subprocess.DEVNULL)
        subprocess.run([str(bindir/'pg_ctl'),'-D',str(root/'data'),'-l',str(root/'pg.log'),'-o',f"-k {sock} -c listen_addresses=''",'-w','start'],check=True,stdout=subprocess.DEVNULL)
        try:check()
        finally:subprocess.run([str(bindir/'pg_ctl'),'-D',str(root/'data'),'-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL)
