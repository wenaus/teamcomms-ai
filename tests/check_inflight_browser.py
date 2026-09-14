"""Bounded Inflight browser check against real operations in private PostgreSQL.
Uses synthetic actors only; no deployed service or full suite.
"""
import os
from pathlib import Path
import subprocess
import socket
import tempfile
import time
import urllib.request
import sys

ROOT=Path(__file__).resolve().parents[1]
PREFIX='/nested/tc'


def serve(port):
    import django
    django.setup()
    from django.core.management import call_command
    from teamcomms.service.operations import bootstrap
    from teamcomms.service.access import authenticate
    from teamcomms.pouch.api import initialize_pouch
    from teamcomms.service.asgi import create_app
    import uvicorn
    call_command('migrate',verbosity=0)
    _,token=bootstrap('Synthetic browser team','Browser owner')
    initialize_pouch(authenticate(token))
    app=create_app(mount_path=PREFIX)
    async def local_fixture(scope,receive,send):
        if scope['type']=='http':
            scope=dict(scope);scope['headers']=list(scope['headers'])+[(b'authorization',('Bearer '+token).encode())]
        await app(scope,receive,send)
    uvicorn.run(local_fixture,host='127.0.0.1',port=port,log_level='warning')


def check():
    from playwright.sync_api import sync_playwright, expect
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}{PREFIX}'
    bindir=Path(subprocess.check_output(['pg_config','--bindir'],text=True).strip())
    with tempfile.TemporaryDirectory(prefix='tc-work-browser-') as directory:
        root=Path(directory);sockdir=root/'socket';sockdir.mkdir()
        env={**os.environ,'TEAMCOMMS_DATABASE_URL':f'postgresql:///postgres?host={sockdir}',
            'TEAMCOMMS_SECRET_KEY':'synthetic-work-browser','TEAMCOMMS_ALLOWED_HOSTS':'127.0.0.1',
            'DJANGO_SETTINGS_MODULE':'teamcomms.service.settings'}
        subprocess.run([str(bindir/'initdb'),'-D',str(root/'data'),'--auth=trust','--no-locale','--encoding=UTF8'],check=True,stdout=subprocess.DEVNULL)
        subprocess.run([str(bindir/'pg_ctl'),'-D',str(root/'data'),'-l',str(root/'postgres.log'),'-o',f"-k {sockdir} -c listen_addresses=''",'-w','start'],check=True,stdout=subprocess.DEVNULL)
        proc=None
        try:
            proc=subprocess.Popen([str(ROOT/'.venv/bin/python'),__file__,'--serve',str(port)],env=env,stdout=(root/'server.log').open('w'),stderr=subprocess.STDOUT)
            for _ in range(100):
                try:urllib.request.urlopen(base+'/health',timeout=.3);break
                except OSError:time.sleep(.1)
            else:raise RuntimeError((root/'server.log').read_text())
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1100})
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('dialog',lambda dialog:dialog.accept())
                page.goto(base+'/pouch');expect(page.locator('#save-state')).to_have_text('Saved')
                page.click('#create-source-work');page.locator('#editor-pane').wait_for(state='visible')
                page.fill('#title','Browser commissioning');page.fill('#work-criteria','Keep owner and evidence')
                edit=lambda value:page.evaluate('(s)=>document.querySelector(".CodeMirror").CodeMirror.setValue(s)',value)
                edit('# Plan\n\n**Shared editor**\n');page.click('#save')
                expect(page.locator('#save-state')).to_have_text('Saved')
                expect(page.locator('#work-summary')).to_contain_text('planned')
                work_id=page.url.rsplit('/',1)[1]
                assert page.locator('#work-links a').count()==1
                page.click('#rendered-mode');expect(page.locator('#rendered strong')).to_have_text('Shared editor')
                page.click('#source-mode');edit('# Plan\n\nLocal draft survives\n')
                current=page.request.get(base+'/api/inflight/read',params={'entry_id':work_id}).json()
                from uuid import uuid4
                response=page.request.post(base+'/api/inflight/mutate',data={'operation_id':str(uuid4()),'entry_id':work_id,'expected_revision':current['revision'],'expected_generation':current['work']['generation'],'change':{'action':'progress','state':'active'}})
                assert response.ok,response.text()
                page.click('#save');expect(page.locator('#conflict')).to_be_visible()
                assert page.evaluate('document.querySelector(".CodeMirror").CodeMirror.getValue()').endswith('Local draft survives\n')
                page.click('#show-remote');expect(page.locator('#comparison')).to_be_visible()
                page.click('#rebase');page.click('#save');expect(page.locator('#save-state')).to_have_text('Saved')
                page.locator('#work-actions > summary').click();page.select_option('#work-action','completed');page.fill('#work-reason','Browser acceptance complete');page.fill('#work-evidence','Synthetic browser receipt')
                page.click('#work-transition');expect(page.locator('#work-summary')).to_contain_text('completed')
                expect(page.locator('#save')).to_be_disabled()
                page.select_option('#status-filter','done');page.locator('#search-form button').click();expect(page.locator('#entry-list')).to_contain_text('Browser commissioning')
                revision=page.request.get(base+'/api/inflight/read',params={'entry_id':work_id}).json()['revision']
                page.goto(base+'/inflight/'+work_id+'?revision='+str(revision));expect(page.locator('#save-state')).to_have_text('Saved revision — read only')
                expect(page.locator('#work-transition')).to_be_disabled()
                page.click('#current-document');expect(page.locator('#save-state')).to_have_text('Saved')
                page.locator('#work-actions > summary').click();page.select_option('#work-action','reopen');page.fill('#work-reason','Follow-up');page.click('#work-transition');expect(page.locator('#work-summary')).to_contain_text('planned')
                expect(page.locator('#save')).to_be_enabled()
                before=page.request.get(base+'/api/inflight/read',params={'entry_id':work_id}).json()['revision']
                edit('# Replay-safe draft\n')
                def lose_response(route):
                    route.fetch();route.abort()
                page.route('**/api/inflight/mutate',lose_response)
                page.click('#save');expect(page.locator('#save-state')).to_have_text('Save failed — draft retained')
                page.unroute('**/api/inflight/mutate',lose_response)
                page.click('#save');expect(page.locator('#save-state')).to_have_text('Saved')
                assert page.request.get(base+'/api/inflight/read',params={'entry_id':work_id}).json()['revision']==before+1
                page.screenshot(path=str(ROOT/'dist/inflight-browser.png'),full_page=True)
                assert not errors,errors
                browser.close()
            print('PASS: Pouch-to-work source revision, shared editor/rendering, revision conflict/draft recovery, Live/Done, completion evidence, fixed revision and explicit reopen; no suite')
        finally:
            if proc:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
            subprocess.run([str(bindir/'pg_ctl'),'-D',str(root/'data'),'-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL)


if __name__=='__main__':
    if len(sys.argv)>1:serve(int(sys.argv[2]))
    else:check()
