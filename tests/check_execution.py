"""Focused execution acceptance: synthetic PostgreSQL, real Wrangler and bounded doers."""
from pathlib import Path
from uuid import uuid4
from datetime import timedelta
from dataclasses import replace
from threading import Barrier,Event
from concurrent.futures import ThreadPoolExecutor
import json,os,subprocess,tempfile,sys


def check(root):
 import django;django.setup()
 from django.core.management import call_command
 from django.db import connections,close_old_connections,transaction,IntegrityError
 from django.utils import timezone
 from teamcomms.service.operations import bootstrap
 from teamcomms.service.access import authenticate,AccessError
 from teamcomms.service.models import Participant,Membership
 from teamcomms.comms.directory import register_session
 from teamcomms.comms.schemas import RegisterSession
 from teamcomms.inflight.operations import create_work,get_work
 from teamcomms.inflight.schemas import CreateWork,ReadWork
 from teamcomms.inflight.claim_schemas import OfferWork,ClaimWork,UpdateClaim,ValidateClaim,AvailableOffers,ExecutionRequest
 from teamcomms.inflight.claims import offer_work,claim_work,update_claim,validate_claim
 from teamcomms.inflight.execution import record_execution,list_work_offers
 from teamcomms.inflight.models import Claim,ExecutionRun
 from teamcomms.connectors.execution import Bullpen,WorkerConfiguration,Journal
 from teamcomms.connectors.config import Configuration
 from wrangle_ai import Wrangler
 import httpx
 call_command('migrate',verbosity=0);call_command('makemigrations',check=True,dry_run=True,verbosity=0)
 _,token=bootstrap('Execution fixture','Owner');owner=authenticate(token)
 p=Participant.objects.create(name='Program',kind='program');m=Membership.objects.create(team_id=owner.team_id,participant=p,role='member');worker=replace(owner,participant_id=p.id,membership_id=m.id,role='member')
 session=register_session(worker,RegisterSession(native_id='worker',client='wrangle-ai',host='fixture',name='worker',capabilities=['analysis']))['session_id']
 human_session=register_session(owner,RegisterSession(native_id='interactive',client='codex',host='fixture',name='interactive',capabilities=['analysis']))['session_id']
 def deny(fn,status=409):
  try:fn()
  except AccessError as e:assert e.status==status,str(e)
  else:raise AssertionError('Expected denial')
 def offer(name,after=True,timeout=30,delay=-1):
  w=create_work(owner,CreateWork(operation_id=uuid4(),state={'title':name}))
  spec={'profile':'read','timeout_seconds':timeout,'headless_after':(timezone.now()+timedelta(seconds=delay)).isoformat() if after else None}
  return offer_work(owner,OfferWork(operation_id=uuid4(),entry_id=w['entry_id'],expected_revision=w['revision'],expected_generation=w['work']['generation'],
   eligible_participant_ids=[owner.participant_id,worker.participant_id],required_capabilities=['analysis'],expires_at=timezone.now()+timedelta(hours=1),lease_seconds=30,execution=spec))
 def take(w,mode='headless',sid=session):return ClaimWork(operation_id=uuid4(),offer_id=w['offer']['offer_id'],expected_revision=w['revision'],expected_generation=w['work']['generation'],session_id=sid,mode=mode)
 no=offer('No fallback',False);deny(lambda:claim_work(worker,take(no)))
 delayed=offer('Future fallback',delay=30);deny(lambda:claim_work(worker,take(delayed)))
 assert not list_work_offers(worker,AvailableOffers(session_id=session,offer_id=delayed['offer']['offer_id']))['offers']
 future=offer('Race');barrier=Barrier(2)
 def compete(i):
  close_old_connections()
  try:
   barrier.wait(3)
   try:return claim_work([owner,worker][i],take(future,['interactive','headless'][i],[human_session,session][i]))
   except AccessError as e:return e.status
  finally:connections.close_all()
 with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(compete,range(2)))
 assert sum(isinstance(r,dict) for r in results)==1
 win=next(i for i,r in enumerate(results) if isinstance(r,dict));actor=[owner,worker][win];won=results[win];c=won['claim'];identity={'claim_id':c['claim_id'],'expected_generation':c['generation']}
 admitted=ExecutionRequest(operation_id=uuid4(),**identity,run_id=uuid4(),action='start',command_sha256='a'*64);record_execution(actor,admitted)
 deny(lambda:record_execution(actor,admitted.model_copy(update={'operation_id':uuid4(),'run_id':uuid4()})))
 deny(lambda:update_claim(actor,UpdateClaim(operation_id=uuid4(),**identity,action='release',expected_revision=c['work_revision'],reason='Not confirmed stopped')))
 deny(lambda:record_execution(actor,admitted.model_copy(update={'operation_id':uuid4(),'expected_generation':c['generation']-1 or c['generation']+1})))
 finish=ExecutionRequest(operation_id=uuid4(),**identity,run_id=admitted.run_id,action='finish',stopped=True,exit_code=0,outcome='Raced once',evidence=['One accepted claim'])
 record_execution(actor,finish);deny(lambda:record_execution(actor,finish.model_copy(update={'operation_id':uuid4()})))
 update_claim(actor,UpdateClaim(operation_id=uuid4(),**identity,action='complete',expected_revision=c['work_revision'],outcome=finish.outcome,evidence=finish.evidence))
 deny(lambda:update_claim(actor,UpdateClaim(operation_id=uuid4(),**identity,action='renew')))
 try:
  with transaction.atomic():ExecutionRun.objects.filter(pk=admitted.run_id).update(result={'forged':True})
 except IntegrityError:pass
 else:raise AssertionError('Execution result changed')
 # HTTP/MCP use the same new contracts; no suite is invoked.
 from starlette.testclient import TestClient
 from teamcomms.service.asgi import create_app
 with TestClient(create_app(mount_path='/nested/tc')) as client:
  client.headers['Authorization']='Bearer '+token
  assert client.get('/nested/tc/api/inflight/offers/available',params={'session_id':human_session,'mode':'interactive'}).status_code==200
  response=client.post('/nested/tc/mcp/',headers={'Accept':'application/json, text/event-stream'},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'list_work_offers','arguments':{'request':{'session_id':human_session,'mode':'interactive'}}}})
  assert response.status_code==200 and not response.json()['result'].get('isError'),response.text
 # Real Wrangler / subprocess, with transport translated to real scoped operations.
 class Api:
  drop=False;renewals=0
  def request(self,method,path,body):
   routes={'/api/inflight/offers/available':(list_work_offers,AvailableOffers),'/api/inflight/claims':(claim_work,ClaimWork),'/api/inflight/claims/validate':(validate_claim,ValidateClaim),'/api/inflight/claims/update':(update_claim,UpdateClaim),'/api/inflight/executions':(record_execution,ExecutionRequest)}
   fn,schema=routes[path]
   try:
    if body.get('action')=='renew':self.renewals+=1
    try:result=fn(worker,schema(**body))
    except AccessError as e:raise httpx.HTTPStatusError(str(e),request=httpx.Request(method,'http://fixture'),response=httpx.Response(e.status))
    if body.get('action')=='complete' and self.drop:self.drop=False;raise httpx.ReadTimeout('Lost completion response')
    return result
   finally:connections.close_all()
 class Bell:
  def __init__(self):self.event=Event()
  def wait(self,timeout):self.event.wait(.03);self.event.clear()
  def ring(self):self.event.set()
  def close(self):pass
 counter=root/'executed';script=root/'doer.py'
 script.write_text('import json,time\nfrom pathlib import Path\np=Path('+repr(str(counter))+')\np.write_text(p.read_text()+"x" if p.exists() else "x")\ntime.sleep(10.5)\nprint(json.dumps({"outcome":"Read once","evidence":["Bounded doer finished"]}))\n')
 profile=WorkerConfiguration(worker_id='fixture',profiles={'read':{'workspace':str(root),'command':[sys.executable,str(script)],'timeout_seconds':30}})
 cfg=Configuration(url='http://localhost',token_file=root/'unused',state_dir=root,host='fixture');api=Api();api.drop=True
 target=offer('Real wrangle');directory=root/'journal';directory.mkdir(mode=0o700)
 def execute_once():
  bullpen=Bullpen(api,cfg,profile,directory,session,'headless',target['offer']['offer_id'],True);bell=Bell();wrangler=Wrangler(bullpen,bell,idle_timeout=.03,max_workers=1);bullpen.wrangler=wrangler;wrangler.register('execute',bullpen.execute,timeout=30)
  return wrangler.run(),bullpen
 status,bullpen=execute_once();assert status==1 and counter.read_text()=='x' and api.renewals>=2
 status,recovered=execute_once();assert status==0 and counter.read_text()=='x'
 current=get_work(owner,ReadWork(entry_id=target['entry_id']));assert current['work']['state']=='completed' and current['work']['owner_id']==str(owner.participant_id) and current['current_execution']['state']=='finished'
 timeout=offer('Bounded timeout',timeout=1);target=timeout;status,pool=execute_once();assert status==1 and counter.read_text()=='xx'
 current=get_work(owner,ReadWork(entry_id=timeout['entry_id']));assert current['work']['state']=='blocked' and current['current_claim'] is None
 # No automatic rerun after uncertain launch, even if the process state is unknown.
 pending=offer('Uncertain startup');claimed=claim_work(worker,take(pending));target=pending
 journal=Journal(directory/(pending['offer']['offer_id']+'.json'));journal.save(phase='executing',claim=claimed['claim'],offer={'specification':pending['offer']})
 status,_=execute_once();assert status==1 and counter.read_text()=='xx' and Claim.objects.get(pk=claimed['claim']['claim_id']).state=='active'
 connections.close_all()
 print('PASS: explicit fallback eligibility, interactive/headless race, immutable generation-bound execution, HTTP/MCP, real Wrangler renewal/timeout, saved-result recovery and no uncertain rerun; no suite')

if __name__=='__main__':
 bindir=Path(subprocess.check_output(['pg_config','--bindir'],text=True).strip())
 with tempfile.TemporaryDirectory(prefix='tc-execution-check-') as directory:
  root=Path(directory);sock=root/'socket';sock.mkdir()
  os.environ.update(TEAMCOMMS_DATABASE_URL=f'postgresql:///postgres?host={sock}',TEAMCOMMS_SECRET_KEY='synthetic-execution',TEAMCOMMS_ALLOWED_HOSTS='testserver,localhost',DJANGO_SETTINGS_MODULE='teamcomms.service.settings')
  subprocess.run([str(bindir/'initdb'),'-D',str(root/'data'),'--auth=trust','--no-locale','--encoding=UTF8'],check=True,stdout=subprocess.DEVNULL)
  subprocess.run([str(bindir/'pg_ctl'),'-D',str(root/'data'),'-l',str(root/'pg.log'),'-o',f"-k {sock} -c listen_addresses=''",'-w','start'],check=True,stdout=subprocess.DEVNULL)
  try:check(root)
  finally:subprocess.run([str(bindir/'pg_ctl'),'-D',str(root/'data'),'-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL)
