"""Bounded claims UI acceptance using the existing private browser server fixture."""
import os,json,socket,subprocess,tempfile,time,urllib.request
from pathlib import Path
from uuid import uuid4
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]
bindir=Path(subprocess.check_output(['pg_config','--bindir'],text=True).strip())
with tempfile.TemporaryDirectory(prefix='tc-claims-browser-') as directory:
    root=Path(directory);sockdir=root/'socket';sockdir.mkdir()
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    base=f'http://127.0.0.1:{port}/nested/tc'
    env={**os.environ,'TEAMCOMMS_DATABASE_URL':f'postgresql:///postgres?host={sockdir}',
        'TEAMCOMMS_SECRET_KEY':'synthetic-claims-ui','TEAMCOMMS_ALLOWED_HOSTS':'127.0.0.1','DJANGO_SETTINGS_MODULE':'teamcomms.service.settings'}
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
            browser=p.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1440,'height':1050})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            identity=page.request.get(base+'/api/whoami').json()
            r=page.request.post(base+'/api/comms/resources',data={'key':'browser-guard','kind':'service','host':'synthetic','name':'Browser guard'})
            assert r.ok,r.text();rid=r.json()['resource_id']
            r=page.request.post(base+'/api/inflight/resources/manage',data={'operation_id':str(uuid4()),'action':'provision','resource_id':rid,'custodian_id':identity['participant_id'],'protection':'local_flock'})
            assert r.ok,r.text()
            page.goto(base+'/inflight');page.click('#new-entry');page.fill('#title','Claim browser work');page.click('#save')
            expect(page.locator('#save-state')).to_have_text('Saved')
            page.locator('#claim-controls summary').click();page.locator('#claim-controls input[type=checkbox]').check()
            page.locator('#claim-controls select').select_option('guarded')
            page.get_by_role('button',name='Offer to selected participant').click()
            expect(page.get_by_role('button',name='Claim offered work')).to_be_visible()
            page.get_by_role('button',name='Claim offered work').click()
            expect(page.locator('#claim-controls')).to_contain_text('1 resources retained')
            expect(page.locator('#claim-controls')).to_contain_text('Cooperating local guard entrypoints only')
            expect(page.locator('#work-transition')).to_be_disabled()
            wid=page.url.rsplit('/',1)[1]
            before=page.request.get(base+'/api/inflight/read',params={'entry_id':wid}).json()
            page.get_by_role('button',name='Renew lease').click()
            expect(page.get_by_role('button',name='Complete with evidence')).to_be_enabled()
            page.locator('#claim-controls textarea').nth(0).fill('Completed under the held claim')
            page.locator('#claim-controls textarea').nth(1).fill('Synthetic claims UI receipt')
            page.get_by_role('button',name='Complete with evidence').click()
            expect(page.locator('#work-summary')).to_contain_text('completed')
            after=page.request.get(base+'/api/inflight/read',params={'entry_id':wid}).json()
            assert after['revision']==before['revision']+1 and after['current_claim'] is None
            resources=page.request.get(base+'/api/inflight/resources').json()['resources']
            assert resources[0]['reservation'] is None and resources[0]['custodian_id']==identity['participant_id']
            assert not errors,errors
            page.screenshot(path=str(ROOT/'dist/claims-browser.png'),full_page=True);browser.close()
        print('PASS: explicit guarded offer, resource selection/coverage, claim/renew, generic bypass disabled, completion and retained idle custodian; no suite')
    finally:
        if proc:
            proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
        subprocess.run([str(bindir/'pg_ctl'),'-D',str(root/'data'),'-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL)
