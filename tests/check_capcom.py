"""Bounded Capcom integrity, access and attention checks in private PostgreSQL."""
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
from unittest.mock import patch
import tempfile,subprocess,os,asyncio,json


def check():
    import django;django.setup()
    from django.core.management import call_command
    from django.db import connections,close_old_connections,transaction,IntegrityError
    from django.utils import timezone
    from teamcomms.service.access import authenticate,AccessError
    from teamcomms.service.operations import bootstrap
    from teamcomms.service.models import Participant,Membership
    from teamcomms.comms.directory import register_session
    from teamcomms.comms.schemas import RegisterSession,SendMessage
    from teamcomms.comms.operations import send_message,get_messages
    from teamcomms.comms.models import Delivery,Message
    from teamcomms.capcom import operations as ops,attention
    from teamcomms.capcom.schemas import CreateTopic,UpdateTopic,TopicRead,Topics,Notices,TopicPage,PublishNotice,FollowTopic,ResolveDecision,SetAttention,PlanAttention,SessionRead
    from teamcomms.capcom.models import Notice,Decision,MutationReceipt,Presentation
    from teamcomms.inflight.operations import create_work,mutate_work
    from teamcomms.inflight.schemas import CreateWork,MutateWork
    call_command('migrate',verbosity=0);call_command('makemigrations',check=True,dry_run=True,verbosity=0)
    _,token=bootstrap('Synthetic Capcom','Human owner');admin=authenticate(token)
    def person(name,kind='program'):
        p=Participant.objects.create(name=name,kind=kind);m=Membership.objects.create(team_id=admin.team_id,participant=p,role='member')
        return replace(admin,participant_id=p.id,membership_id=m.id,role='member')
    producer=person('Producer');other=person('Other');stranger=person('Unrelated')
    def session(actor,name):return register_session(actor,RegisterSession(native_id=name,client='pull',host='synthetic',name=name,capabilities=['attention-v1']))['session_id']
    s1=session(producer,'p1');s2=session(other,'p2')
    def deny(fn,code):
        try:fn()
        except AccessError as e:assert e.status==code,str(e)
        else:raise AssertionError('Expected denial')
    work=create_work(admin,CreateWork(operation_id=uuid4(),state={'title':'Current state source'}))
    work=mutate_work(admin,MutateWork(operation_id=uuid4(),entry_id=work['entry_id'],expected_revision=work['revision'],expected_generation=1,change={'action':'progress','state':'blocked','blockers':'Historical blocker'}))
    request=CreateTopic(operation_id=uuid4(),key='capcom-check',title='Synthetic topic',links={'entries':[{'entry_id':work['entry_id'],'revision':work['revision']}]})
    topic=ops.create_topic(admin,request);assert topic==ops.create_topic(admin,request)
    tid=topic['topic_id'];read=TopicRead(topic_id=tid)
    deny(lambda:ops.create_topic(other,request),409)
    deny(lambda:ops.update_topic(other,UpdateTopic(operation_id=uuid4(),topic_id=tid,expected_revision=1,title='Spoof',links={})),403)
    deny(lambda:ops.get_topic(replace(admin,scopes=frozenset()),read),403)
    deny(lambda:ops.get_topic(replace(admin,team_id=uuid4()),read),404)
    def notice(actor=producer,kind='event',urgency='normal',content='A real synthetic event',audience=None):
        return PublishNotice(operation_id=uuid4(),notice_id=uuid4(),topic_id=tid,kind=kind,urgency=urgency,content=content,source='fixture',observed_at=timezone.now(),audience=audience)
    req=notice(audience={'session_ids':[s1,s2]});barrier=Barrier(2)
    def race(_):
        close_old_connections()
        try:barrier.wait(5);return ops.publish_notice(producer,req)
        finally:connections.close_all()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(race,range(2)))
    assert results[0]==results[1] and Notice.objects.count()==1 and len(results[0]['publication']['deliveries'])==2
    deny(lambda:ops.publish_notice(producer,req.model_copy(update={'content':'changed'})),409)
    empty=notice(audience={'topics':['nobody']});deny(lambda:ops.publish_notice(producer,empty),409);assert not Notice.objects.filter(pk=empty.notice_id).exists()
    private=send_message(producer,SendMessage(message_id=uuid4(),topic='capcom-check',audience={'session_ids':[s1]},content='Private canonical conversation'))
    assert len(ops.get_topic_conversation(stranger,TopicPage(topic_id=tid))['messages'])==0
    assert any(m['message_id']==private['message_id'] for m in ops.get_topic_conversation(producer,TopicPage(topic_id=tid))['messages'])
    assert ops.get_topic(producer,read)['conversation_unread']
    ops.follow_topic(producer,FollowTopic(operation_id=uuid4(),topic_id=tid,message_ids=[private['message_id'],str(req.notice_id)]))
    assert not ops.get_topic(producer,read)['conversation_unread']
    deny(lambda:ops.follow_topic(stranger,FollowTopic(operation_id=uuid4(),topic_id=tid,message_ids=[private['message_id']])),404)
    decision=ops.publish_notice(admin,notice(kind='decision',content='Choose explicitly'))
    viewed=ops.get_topic(admin,read);assert viewed['decisions'] and Delivery.objects.filter(acknowledged_at__isnull=False).count()==0
    ops.follow_topic(admin,FollowTopic(operation_id=uuid4(),topic_id=tid,read_sequence=viewed['last_sequence']))
    assert ops.list_topics(admin,Topics())['topics'] # Unresolved decision/current blocker remains visible after reading.
    ops.resolve_decision(admin,ResolveDecision(operation_id=uuid4(),notice_id=decision['notice_id'],expected_revision=1,resolution='Resolved explicitly'))
    assert Decision.objects.get(pk=decision['notice_id']).resolved_at and Delivery.objects.filter(acknowledged_at__isnull=False).count()==0
    work=mutate_work(admin,MutateWork(operation_id=uuid4(),entry_id=work['entry_id'],expected_revision=work['revision'],expected_generation=1,change={'action':'transition','state':'completed','outcome':'Recovered','evidence':['Synthetic evidence']}))
    ops.publish_notice(producer,notice(content='Delayed historical blocker'))
    assert ops.get_topic(admin,read)['current_work'][0]['work']['state']=='completed'
    one=ops.get_topic_notices(admin,Notices(topic_id=tid,limit=1));two=ops.get_topic_notices(admin,Notices(topic_id=tid,before=one['next_before'],limit=100))
    assert all(n['sequence']<one['notices'][0]['sequence'] for n in two['notices'])
    def policy(mode,**extra):
        old=attention.get_attention_policy(producer,SessionRead(session_id=s1))
        return attention.set_attention_policy(producer,SetAttention(operation_id=uuid4(),session_id=s1,expected_revision=old['revision'],routine_mode=mode,**extra))
    def delivered(actor=producer,kind='routine',urgency='routine'):
        result=ops.publish_notice(actor,notice(kind=kind,urgency=urgency,audience={'session_ids':[s1,s2]}))
        return Delivery.objects.get(message_id=result['notice_id'],session_id=s1)
    def plan(rows,actor=producer):return attention.plan_attention(actor,PlanAttention(session_id=s1,delivery_ids=[r.id for r in rows]))['presentations']
    quiet=policy('batch',batch_seconds=30,quiet_until=timezone.now()+timedelta(minutes=5))
    routine1=delivered();routine2=delivered();alarm=delivered(urgency='alarm');human=delivered(admin)
    planned=plan([routine1,routine2,alarm,human]);states={r['delivery_id']:r['disposition'] for r in planned}
    assert states[str(routine1.id)]==states[str(routine2.id)]=='deferred' and states[str(alarm.id)]==states[str(human.id)]=='eligible'
    deny(lambda:plan([routine1],other),404)
    with patch('teamcomms.capcom.attention.timezone.now',return_value=timezone.now()+timedelta(minutes=10)):
        result=plan([routine1,routine2]);assert {r['disposition'] for r in result}=={'coalesced','eligible'}
    assert Delivery.objects.get(pk=routine1.pk).state=='pending' and Delivery.objects.filter(acknowledged_at__isnull=False).count()==0
    policy('record');recorded=delivered();assert plan([recorded])[0]['disposition']=='recorded'
    assert Delivery.objects.get(message_id=recorded.message_id,session_id=s2).state=='pending' # Independent destination retained.
    for model in (Notice,MutationReceipt):
        try:
            with transaction.atomic():model.objects.all().delete()
        except (IntegrityError,django.db.models.deletion.ProtectedError):pass
        else:raise AssertionError('History deletion must fail')
    from starlette.testclient import TestClient
    from teamcomms.service.asgi import create_app
    with TestClient(create_app(mount_path='/nested/tc')) as client:
        client.headers['Authorization']='Bearer '+token
        assert client.get('/nested/tc/api/capcom/topics').status_code==200
        r=client.post('/nested/tc/mcp/',headers={'Accept':'application/json, text/event-stream'},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'get_topic','arguments':{'request':{'topic_id':tid}}}})
        assert r.status_code==200 and not r.json()['result'].get('isError'),r.text
    connections.close_all()
    print('PASS: atomic topic publication/fanout/retries, private conversation isolation, authoritative work state, separate read/decision/receipt states, deferred/coalesced/recorded routine policy, alarm/human bypass, immutable history and HTTP/MCP; no suite')


if __name__=='__main__':
    bindir=Path(subprocess.check_output(['pg_config','--bindir'],text=True).strip())
    with tempfile.TemporaryDirectory(prefix='tc-capcom-check-') as directory:
        root=Path(directory);sock=root/'socket';sock.mkdir()
        os.environ.update(TEAMCOMMS_DATABASE_URL=f'postgresql:///postgres?host={sock}',TEAMCOMMS_SECRET_KEY='synthetic-capcom',TEAMCOMMS_ALLOWED_HOSTS='testserver,localhost',DJANGO_SETTINGS_MODULE='teamcomms.service.settings')
        subprocess.run([str(bindir/'initdb'),'-D',str(root/'data'),'--auth=trust','--no-locale','--encoding=UTF8'],check=True,stdout=subprocess.DEVNULL)
        subprocess.run([str(bindir/'pg_ctl'),'-D',str(root/'data'),'-l',str(root/'pg.log'),'-o',f"-k {sock} -c listen_addresses=''",'-w','start'],check=True,stdout=subprocess.DEVNULL)
        try:check()
        finally:subprocess.run([str(bindir/'pg_ctl'),'-D',str(root/'data'),'-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL)
