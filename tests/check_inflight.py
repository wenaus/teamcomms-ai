"""Focused Inflight transaction/authority/transport checks in private PostgreSQL.
No full suite, native runtime, deployed credentials or production data.
"""
from dataclasses import replace
from uuid import uuid4, UUID
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import os
from pathlib import Path
import subprocess
import tempfile


def check():
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import connections, close_old_connections, transaction, IntegrityError
    from starlette.testclient import TestClient
    from teamcomms.service.access import AccessError, authenticate
    from teamcomms.service.asgi import create_app
    from teamcomms.service.operations import bootstrap
    from teamcomms.service.models import Participant, Membership
    from teamcomms.entries.models import Entry, Revision
    from teamcomms.entries.operations import create_entry, read_entry, update_entry
    from teamcomms.entries.schemas import State, CreateEntry, ReadEntry, UpdateEntry
    from teamcomms.inflight.models import Work, WorkReceipt
    from teamcomms.inflight.operations import create_work, mutate_work, get_work, list_work, get_work_changes
    from teamcomms.inflight.schemas import CreateWork, MutateWork, ReadWork, ListWork, Changes
    from teamcomms.comms.directory import register_session
    from teamcomms.comms.schemas import RegisterSession, SendMessage, MessageQuery
    from teamcomms.comms.operations import send_message

    call_command('migrate', verbosity=0)
    call_command('makemigrations', check=True, dry_run=True, verbosity=0)
    _, token = bootstrap('Synthetic work team', 'Owner')
    admin = authenticate(token)
    owner = replace(admin, role='member')
    def person(name):
        p = Participant.objects.create(name=name, kind='human')
        m = Membership.objects.create(team_id=owner.team_id, participant=p, role='member')
        return replace(owner, participant_id=p.id, membership_id=m.id)
    successor, outsider = person('Successor'), person('Unassigned')
    def deny(fn, code):
        try: fn()
        except AccessError as e: assert e.status == code, str(e)
        else: raise AssertionError('Expected denial')
    def race(fn):
        barrier=Barrier(2)
        def run(n):
            close_old_connections()
            try: barrier.wait(5); return fn(n)
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool: return list(pool.map(run, range(2)))
    def change(actor, work, action, **values):
        return mutate_work(actor, MutateWork(operation_id=uuid4(),entry_id=work['entry_id'],
            expected_revision=work['revision'],expected_generation=work['work']['generation'],change={'action':action,**values}))
    def new(title, **values):
        return create_work(owner, CreateWork(operation_id=uuid4(),state=State(title=title),**values))

    document = create_entry(owner, CreateEntry(kind='document',state=State(title='Plan')))
    request=CreateWork(operation_id=uuid4(),state=State(title='Commission work',relations=[{'entry_id':document['entry_id'],'revision':1,'relation':'source'}]),criteria='Recorded evidence')
    values=race(lambda n:create_work(owner,request))
    assert values[0]==values[1] and Work.objects.count()==1
    first=w=values[0]
    assert w['work']['owner_id']==str(owner.participant_id) and w['revision']==1
    deny(lambda:change(outsider,w,'progress',state='active'),403)
    deny(lambda:change(owner,w,'progress',state='blocked'),400)
    deny(lambda:update_entry(owner,UpdateEntry(entry_id=w['entry_id'],expected_revision=1,changes={'title':'bypass'})),400)
    deny(lambda:read_entry(owner,ReadEntry(entry_id=w['entry_id'])),400)
    deny(lambda:get_work(replace(owner,team_id=uuid4()),ReadWork(entry_id=w['entry_id'])),404)
    deny(lambda:get_work(replace(owner,scopes=frozenset()),ReadWork(entry_id=w['entry_id'])),403)
    deny(lambda:change(owner,w,'edit',changes={'metadata':{'inflight':{'owner_id':str(outsider.participant_id)}}}),400)
    w=change(owner,w,'progress',state='active')
    w=change(owner,w,'offer_handoff',successor_id=successor.participant_id,reason='Continue on another host')
    assert w['work']['owner_id']==str(owner.participant_id)
    offer=w['work']['handoff']['handoff_id']
    stale=w
    accept=MutateWork(operation_id=uuid4(),entry_id=w['entry_id'],expected_revision=w['revision'],expected_generation=1,
        change={'action':'accept_handoff','handoff_id':offer})
    deny(lambda:mutate_work(outsider,accept),403)
    accepted=race(lambda n:mutate_work(successor,accept))
    assert accepted[0]==accepted[1]
    w=accepted[0]
    assert w['work']['owner_id']==str(successor.participant_id) and w['work']['generation']==2
    deny(lambda:change(owner,stale,'progress',state='active'),409)
    deny(lambda:change(owner,w,'progress',state='active'),403)
    # Retrying the old create or acceptance must return its exact saved result.
    assert create_work(owner,request)==first
    assert mutate_work(successor,accept)==w
    deny(lambda:create_work(successor,request),409)
    session=register_session(successor,RegisterSession(native_id='synthetic-thread',client='synthetic',host='synthetic',name='Executor'))
    w=change(successor,w,'assign_executor',participant_id=successor.participant_id,session_id=session['session_id'])
    assert w['work']['generation']==3
    from teamcomms.comms.models import Session
    Session.objects.filter(pk=session['session_id']).update(state='offline')
    current=get_work(successor,ReadWork(entry_id=w['entry_id']))
    assert current['work']['owner_id']==str(successor.participant_id) and not current['current_presence']['executor_session_online']
    deny(lambda:change(successor,w,'transition',state='completed',outcome='Done'),400)
    w=change(successor,w,'transition',state='completed',outcome='Verified',evidence=['Synthetic host receipt'])
    deny(lambda:change(successor,w,'progress',state='active'),409)
    deny(lambda:change(successor,w,'edit',changes={'content':'late write'}),409)
    assert list_work(successor,ListWork(view='done'))['entries'][0]['entry_id']==w['entry_id']
    closed=w
    w=change(successor,w,'reopen',reason='New follow-up requirement')
    assert w['work']['state']=='planned' and w['work']['owner_id']==closed['work']['owner_id']
    assert get_work(owner,ReadWork(entry_id=w['entry_id'],revision=closed['revision']))['work']['state']=='completed'
    assert get_work_changes(owner,Changes(entry_id=w['entry_id'],limit=2))['next_offset']==2

    parent=new('Parent'); child=new('Child',parent_id=parent['entry_id'])
    deny(lambda:change(owner,parent,'transition',state='completed',outcome='Done',evidence=['receipt']),409)
    deny(lambda:change(owner,child,'graph',parent_id=parent['entry_id'],dependencies=[parent['entry_id']]),409)
    child=change(owner,child,'transition',state='completed',outcome='Done',evidence=['receipt'])
    parent=change(owner,parent,'transition',state='completed',outcome='Aggregate',evidence=['child receipt'])
    deny(lambda:change(owner,child,'reopen',reason='Would invalidate parent'),409)
    a,b=new('A'),new('B')
    def cycle(n):
        try: return change(owner,[a,b][n],'graph',parent_id=None,dependencies=[[b,a][n]['entry_id']])['revision']
        except AccessError as e:return e.status
    assert sorted(race(cycle))==[2,409]
    # Superseded and expired handoffs cannot transfer ownership.
    h=new('Handoff');h=change(owner,h,'offer_handoff',successor_id=successor.participant_id,reason='Offer')
    old_offer=h['work']['handoff']['handoff_id']
    h=change(owner,h,'offer_handoff',successor_id=outsider.participant_id,reason='Replace offer')
    deny(lambda:change(successor,h,'accept_handoff',handoff_id=old_offer),409)
    h=change(outsider,h,'reject_handoff',handoff_id=h['work']['handoff']['handoff_id'])
    assert h['work']['owner_id']==str(owner.participant_id)
    from datetime import timedelta
    from django.utils import timezone
    h=change(owner,h,'offer_handoff',successor_id=successor.participant_id,reason='Expiring',expires_at=timezone.now()+timedelta(seconds=10))
    from unittest.mock import patch
    with patch('teamcomms.inflight.operations.timezone.now',return_value=timezone.now()+timedelta(seconds=20)):
        deny(lambda:change(successor,h,'accept_handoff',handoff_id=h['work']['handoff']['handoff_id']),409)
    # Private canonical peer provenance cannot become an asserted Dialog link.
    from teamcomms.dialog.operations import record_dialog
    from teamcomms.dialog.schemas import RecordDialog
    target=register_session(outsider,RegisterSession(native_id='recipient',client='synthetic',host='synthetic',name='Recipient'))
    msg_id=uuid4()
    send_message(successor,SendMessage(message_id=msg_id,audience={'session_ids':[target['session_id']]},content='Private peer source'))
    event=record_dialog(outsider,RecordDialog(session_id=target['session_id'],source_id='private-source',source_sequence=1,
        occurred_at=timezone.now(),role='peer',content='source',message_id=msg_id))
    deny(lambda:change(owner,h,'sources',dialog_event_ids=[event['event_id']]),404)
    deny(lambda:change(owner,h,'sources',message_ids=[msg_id]),404)
    h=change(owner,h,'sources',resource_ids=[],message_ids=[],dialog_event_ids=[])
    assert h['work']['message_ids']==[]
    # Component references require their component's read permission.
    deny(lambda:create_entry(replace(owner,scopes=owner.scopes-{'inflight:read'}),CreateEntry(state=State(relations=[{'entry_id':h['entry_id']}]))),403)
    link=create_entry(owner,CreateEntry(state=State(relations=[{'entry_id':h['entry_id'],'revision':h['revision']}])) )
    for fn in (lambda:WorkReceipt.objects.filter(pk=request.operation_id).update(result={}),
               lambda:Work.objects.filter(pk=h['entry_id']).delete(),
               lambda:Entry.objects.filter(pk=h['entry_id']).update(kind='note')):
        try:
            with transaction.atomic():fn()
        except (IntegrityError, __import__('django.db.models.deletion',fromlist=['ProtectedError']).ProtectedError):pass
        else:raise AssertionError('Expected integrity guard')
    with TestClient(create_app(mount_path='/nested/tc')) as client:
        client.headers['Authorization']='Bearer '+token
        assert client.get('/nested/tc/inflight').status_code==200
        assert client.get('/nested/tc/api/inflight/read',params={'entry_id':w['entry_id']}).json()['work']['state']=='planned'
        response=client.post('/nested/tc/mcp/',headers={'Accept':'application/json, text/event-stream'},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'get_work','arguments':{'request':{'entry_id':w['entry_id']}}}})
        assert response.status_code==200 and not response.json()['result'].get('isError'),response.text
    connections.close_all()
    print('PASS: Inflight ownership, accepted/superseded/expired handoffs, generation fencing, concurrent retries and graph races, completion evidence/terminal guards, references/scopes/history and HTTP/MCP; no suite')


if __name__=='__main__':
    bindir=Path(subprocess.check_output(['pg_config','--bindir'],text=True).strip())
    with tempfile.TemporaryDirectory(prefix='tc-inflight-check-') as directory:
        root=Path(directory);socket_dir=root/'socket';socket_dir.mkdir()
        os.environ.update(TEAMCOMMS_DATABASE_URL=f'postgresql:///postgres?host={socket_dir}',TEAMCOMMS_SECRET_KEY='synthetic-work-only',TEAMCOMMS_ALLOWED_HOSTS='testserver,localhost',DJANGO_SETTINGS_MODULE='teamcomms.service.settings')
        subprocess.run([str(bindir/'initdb'),'-D',str(root/'data'),'--auth=trust','--no-locale','--encoding=UTF8'],check=True,stdout=subprocess.DEVNULL)
        subprocess.run([str(bindir/'pg_ctl'),'-D',str(root/'data'),'-l',str(root/'postgres.log'),'-o',f"-k {socket_dir} -c listen_addresses=''",'-w','start'],check=True,stdout=subprocess.DEVNULL)
        try:check()
        finally:subprocess.run([str(bindir/'pg_ctl'),'-D',str(root/'data'),'-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL)
