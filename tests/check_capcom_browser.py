"""Bounded Capcom browser flow using a private synthetic PostgreSQL fixture."""
import os,json,socket,subprocess,tempfile,time,urllib.request
from pathlib import Path
from uuid import uuid4
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
bindir=Path(subprocess.check_output(['pg_config','--bindir'],text=True).strip())
with tempfile.TemporaryDirectory(prefix='tc-capcom-browser-') as directory:
 root=Path(directory);sockdir=root/'socket';sockdir.mkdir()
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 base=f'http://127.0.0.1:{port}/nested/tc'
 env={**os.environ,'TEAMCOMMS_DATABASE_URL':f'postgresql:///postgres?host={sockdir}','TEAMCOMMS_SECRET_KEY':'synthetic-capcom-ui','TEAMCOMMS_ALLOWED_HOSTS':'127.0.0.1','DJANGO_SETTINGS_MODULE':'teamcomms.service.settings'}
 subprocess.run([str(bindir/'initdb'),'-D',str(root/'data'),'--auth=trust','--no-locale','--encoding=UTF8'],check=True,stdout=subprocess.DEVNULL)
 subprocess.run([str(bindir/'pg_ctl'),'-D',str(root/'data'),'-l',str(root/'pg.log'),'-o',f"-k {sockdir} -c listen_addresses=''",'-w','start'],check=True,stdout=subprocess.DEVNULL)
 proc=None
 try:
  proc=subprocess.Popen([str(ROOT/'.venv/bin/python'),str(ROOT/'tests/check_inflight_browser.py'),'--serve',str(port)],env=env,stdout=(root/'server.log').open('w'),stderr=subprocess.STDOUT)
  for _ in range(100):
   try:urllib.request.urlopen(base+'/health',timeout=.3);break
   except OSError:time.sleep(.1)
  else:raise RuntimeError((root/'server.log').read_text())
  with sync_playwright() as p:
   browser=p.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1440,'height':1050});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   work=page.request.post(base+'/api/inflight',data={'operation_id':str(uuid4()),'state':{'title':'Authoritative browser work'}}).json()
   page.goto(base+'/capcom?view=all');page.locator('summary',has_text='Create topic').click();page.fill('#topic-key','browser-topic');page.fill('#topic-title','Browser Capcom');page.get_by_role('button',name='Create topic',exact=True).click()
   expect(page.locator('#capcom-thread h2')).to_have_text('Browser Capcom')
   page.locator('summary',has_text='Edit title and linked records').click();page.fill('#capcom-refs',work['entry_id']);page.get_by_role('button',name='Save topic',exact=True).click()
   expect(page.locator('#capcom-current')).to_contain_text('Authoritative browser work')
   page.locator('summary',has_text='Add a notice or decision').click()
   composer=page.locator('details:has(#capcom-content)');composer.locator('select').nth(0).select_option('decision');page.fill('#capcom-content','Choose a documented result <script>throw 1</script>');page.get_by_role('button',name='Record notice',exact=True).click()
   expect(page.locator('#capcom-decisions')).to_contain_text('Choose a documented result')
   page.locator('#capcom-decisions textarea').fill('Decision resolved explicitly');page.get_by_role('button',name='Resolve decision',exact=True).click()
   expect(page.locator('#capcom-decisions')).to_contain_text('No outstanding decision')
   expect(page.locator('#capcom-feed')).to_contain_text('Resolved: Decision resolved explicitly')
   page.get_by_role('button',name='Follow',exact=True).click();expect(page.get_by_role('button',name='Unfollow',exact=True)).to_be_visible()
   page.get_by_role('button',name='Mark topic read',exact=True).click();expect(page.locator('#capcom-thread')).to_contain_text('Read · topic revision')
   session=page.request.post(base+'/api/comms/sessions',data={'native_id':'capcom-browser-pull','client':'pull','host':'synthetic','name':'Browser pull'}).json()
   message=page.request.post(base+'/api/comms/messages',data={'message_id':str(uuid4()),'topic':'browser-topic','audience':{'session_ids':[session['session_id']]},'content':'Canonical browser conversation'});assert message.ok,message.text()
   page.get_by_role('button',name='Refresh',exact=True).click();expect(page.locator('#capcom-thread')).to_have_attribute('aria-busy','false');page.locator('summary',has_text='Canonical topic conversation').click();page.get_by_role('button',name='Load conversation',exact=True).click()
   expect(page.locator('#capcom-thread')).to_contain_text('Canonical browser conversation')
   page.get_by_role('button',name='Mark displayed conversation read',exact=True).click();expect(page.locator('#capcom-thread')).to_contain_text('Read · topic revision')
   page.select_option('#theme','dark');assert page.locator('html').get_attribute('data-theme')=='dark'
   assert not errors,errors;page.screenshot(path=str(ROOT/'dist/capcom-browser.png'),full_page=True);browser.close()
  print('PASS: Capcom create/link/current work, decision publication/resolution, explicit follow/read, safe text and shared dark theme; no suite')
 finally:
  if proc:
   proc.terminate()
   try:proc.wait(timeout=5)
   except subprocess.TimeoutExpired:proc.kill();proc.wait()
  subprocess.run([str(bindir/'pg_ctl'),'-D',str(root/'data'),'-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL)
