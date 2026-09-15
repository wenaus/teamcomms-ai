"""Focused Notify LLM admission and retry check; private PostgreSQL, no live sends."""
from dataclasses import replace
from pathlib import Path
from uuid import uuid4
import asyncio, os, subprocess, tempfile


def check():
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import connections
    from django.utils import timezone
    from teamcomms.service.operations import bootstrap
    from teamcomms.service.access import authenticate, AccessError
    from teamcomms.service.models import Participant, Membership
    from teamcomms.comms.directory import register_session
    from teamcomms.comms.schemas import RegisterSession, SendMessage, NotifyLLM
    from teamcomms.comms.operations import send_message, notify_llm
    from teamcomms.comms.models import Message, Delivery
    from teamcomms.capcom import operations as capcom, attention
    from teamcomms.capcom.schemas import CreateTopic, PublishNotice, Notices
    from pydantic import ValidationError
    call_command('migrate', verbosity=0)
    _, token = bootstrap('Notify LLM fixture', 'Human')
    admin = authenticate(token)
    def person(kind):
        p=Participant.objects.create(name=kind,kind=kind)
        m=Membership.objects.create(team_id=admin.team_id,participant=p)
        return replace(admin,participant_id=p.id,membership_id=m.id,role='member')
    producer, ai, bridge = [person(k) for k in ('program','ai','connector')]
    def register(actor):
        return register_session(actor,RegisterSession(native_id=str(uuid4()),client='pull',host='fixture',name='fixture'))['session_id']
    sa,sb=register(ai),register(bridge)
    def denial(fn,status=409):
        count=Message.objects.count()
        try:fn()
        except AccessError as error:assert error.status==status,str(error)
        else:raise AssertionError('Expected denied publication')
        assert Message.objects.count()==count
    def request(**extra):
        return dict(message_id=uuid4(),audience={'session_ids':[sa,sb]},content='Synthetic finding',observed_at=timezone.now(),**extra)
    ordinary=SendMessage(**request(kind='notification'))
    result=send_message(producer,ordinary)
    assert [d['session_id'] for d in result['deliveries']]==[sb]
    assert send_message(producer,ordinary)==result
    assert 'notify_llm' not in Message.objects.get(pk=ordinary.message_id).envelope
    denial(lambda:send_message(producer,SendMessage(**{**request(kind='notification'),'audience':{'session_ids':[sa]}})))
    assert len(send_message(producer,SendMessage(**request()))['deliveries'])==2
    fields=request(notify_llm_reason='Review requested',notify_llm_source='fixture',notify_llm_event_id='one')
    selected=NotifyLLM(**fields)
    result=notify_llm(producer,selected)
    assert len(result['deliveries'])==2 and notify_llm(producer,selected)==result
    denial(lambda:notify_llm(producer,selected.model_copy(update={'notify_llm_reason':'changed'})))
    denial(lambda:notify_llm(replace(producer,scopes=frozenset()),selected),403)
    denial(lambda:notify_llm(producer,NotifyLLM(**{**fields,'message_id':uuid4(),'audience':{'session_ids':[sb]}})))
    for field in ('notify_llm_reason','notify_llm_source','notify_llm_event_id','observed_at'):
        try:NotifyLLM(**{**fields,field:None if field=='observed_at' else ''})
        except ValidationError:pass
        else:raise AssertionError(field)
    external={'server':'https://mm.example','channel_id':'channel','post_id':'post','user_id':'user','username':'human','kind':'human'}
    default_external=send_message(bridge,SendMessage(**request(external_source=external)))
    assert [d['session_id'] for d in default_external['deliveries']]==[sb]
    topic=capcom.create_topic(admin,CreateTopic(operation_id=uuid4(),key='notify-check',title='Fixture'))
    def notice(**extra):
        return PublishNotice(operation_id=uuid4(),notice_id=uuid4(),topic_id=topic['topic_id'],content='Synthetic alarm',observed_at=timezone.now(),**extra)
    record=notice(urgency='alarm')
    assert capcom.publish_notice(producer,record)['publication'] is None
    assert capcom.publish_notice(producer,record)['publication'] is None
    alarm=capcom.publish_notice(producer,notice(urgency='alarm',audience={'session_ids':[sa,sb]}))
    assert [d['session_id'] for d in alarm['publication']['deliveries']]==[sb]
    chosen=capcom.publish_notice(producer,notice(kind='routine',urgency='routine',notify_llm=True,notify_llm_reason='Deliberate',audience={'session_ids':[sa]}))
    assert not attention.routine(Delivery.objects.select_related('message').get(message_id=chosen['notice_id']))
    rows=capcom.get_topic_notices(admin,Notices(topic_id=topic['topic_id']))['notices']
    assert rows[0]['notify_llm'] and rows[0]['notify_llm_reason']=='Deliberate'
    from starlette.testclient import TestClient
    from teamcomms.service.asgi import create_app
    with TestClient(create_app(mount_path='/nested/tc')) as client:
        client.headers['Authorization']='Bearer '+token
        body=NotifyLLM(**{**fields,'message_id':uuid4()}).model_dump(mode='json')
        response=client.post('/nested/tc/api/comms/notify-llm',json=body)
        assert response.status_code==200,response.text
        response=client.post('/nested/tc/mcp/',headers={'Accept':'application/json, text/event-stream'},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'notify_llm','arguments':{'request':body}}})
        assert response.status_code==200 and not response.json()['result'].get('isError'),response.text
    connections.close_all()
    asyncio.run(connectors())
    print('PASS: Notify LLM admission, independent severity, Capcom, unchanged peer conversations, retry identity, permissions, HTTP/MCP and connector selection; synthetic only')


async def connectors():
    from check_mattermost import TC, MM, CHANNEL, HUMAN
    from teamcomms.connectors.mattermost import Inbound, Route
    from teamcomms.connectors.state import Store
    from teamcomms.connectors.watcher import notify_llm
    import httpx
    with tempfile.TemporaryDirectory(prefix='tc-notify-outbox-') as directory:
        root=Path(directory);store=Store(root);tc,mm=TC(root),MM()
        original=tc.post
        async def post(path,body):return await original('/messages' if path=='/notify-llm' else path,body)
        tc.post=post
        fields=dict(source='fixture',event_id='one',reason='Review',content='Fixture',audience={'session_ids':[str(uuid4())]},observed_at='2026-09-15T12:00:00Z')
        tc.lose_response=True
        try:await notify_llm(tc,store,**fields)
        except httpx.ReadTimeout:pass
        else:raise AssertionError('Expected lost response')
        assert len(store.outgoing())==1
        await notify_llm(tc,store,**fields)
        assert not store.outgoing() and tc.published[0]==tc.published[1]
        try:await notify_llm(tc,store,**{**fields,'reason':'changed'})
        except ValueError:pass
        else:raise AssertionError('Changed retry accepted')
        route=Route(channel_id=CHANNEL,name='fixture',inbound_audience=fields['audience'])
        inbound=Inbound(mm,tc,store,route,str(uuid4()))
        post=dict(id='one',channel_id=CHANNEL,user_id=HUMAN,message='Ordinary alarm',create_at=1000)
        count=len(tc.published)
        await inbound.publish(post)
        assert len(tc.published)==count
        await inbound.publish({**post,'id':'two','message':'Notify LLM: review this'})
        assert tc.published[-1]['notify_llm'] and tc.published[-1]['external_source']['user_id']==HUMAN
        await inbound.publish({**post,'id':'three','props':{'notify_llm':True}})
        assert len(tc.published)==count+2
        await inbound.publish({**post,'id':'four','user_id':mm.user_id,'props':{'notify_llm':True}})
        assert len(tc.published)==count+2
        store.close()


if __name__=='__main__':
    bindir=Path(subprocess.check_output(['pg_config','--bindir'],text=True).strip())
    with tempfile.TemporaryDirectory(prefix='tc-notify-check-') as directory:
        root=Path(directory);sock=root/'socket';sock.mkdir()
        os.environ.update(TEAMCOMMS_DATABASE_URL=f'postgresql:///postgres?host={sock}',TEAMCOMMS_SECRET_KEY='synthetic-notify',TEAMCOMMS_ALLOWED_HOSTS='testserver,localhost',DJANGO_SETTINGS_MODULE='teamcomms.service.settings')
        subprocess.run([str(bindir/'initdb'),'-D',str(root/'data'),'--auth=trust','--no-locale','--encoding=UTF8'],check=True,stdout=subprocess.DEVNULL)
        subprocess.run([str(bindir/'pg_ctl'),'-D',str(root/'data'),'-l',str(root/'pg.log'),'-o',f"-k {sock} -c listen_addresses=''",'-w','start'],check=True,stdout=subprocess.DEVNULL)
        try:check()
        finally:subprocess.run([str(bindir/'pg_ctl'),'-D',str(root/'data'),'-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL)
