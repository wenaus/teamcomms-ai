"""Bounded browser acceptance of the editor with synthetic data, no database.

Run: /home/admin/tools/playwright/python tests/check_interface.py
The browser environment supplies Playwright; the repository venv runs ASGI.
"""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ENTRY = '11111111-1111-4111-8111-111111111111'
PERSON = '22222222-2222-4222-8222-222222222222'
PREFIX = '/nested/teamcomms'
SOURCE = '# A shared document\n\n| Name | Value |\n| --- | --- |\n| One | Two |\n\n[Link](https://example.org/)\n\n```python\nx = "<safe>"\nprint(x)\n```\n\n\\(E=mc^2\\)\n\n- First\n  - Nested\n\n<script>window.INJECTED=true</script>\n'


def serve(port):
    import os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','teamcomms.service.settings')
    os.environ.setdefault('TEAMCOMMS_DATABASE_URL','postgresql://unused:unused@127.0.0.1:1/unused')
    import django
    django.setup()
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route
    from starlette.responses import JSONResponse
    import uvicorn
    from teamcomms.service.access import Principal, current_principal
    import teamcomms.ui.routes as ui
    from uuid import UUID
    states = [{'title':'Shared document','content':SOURCE,'tags':[],'status':'active','priority':None,'metadata':{'kept':True},'relations':[]}]
    actor=Principal(UUID(PERSON),UUID(ENTRY),UUID(PERSON),None,'member',frozenset({'entries:read','entries:write'}))
    def record(n=None):
        number=n or len(states)
        return {'entry_id':ENTRY,'slug':'shared-document','kind':'document','revision':number,
                'author_id':PERSON,'created_at':'2026-09-14T17:00:00Z','state':states[number-1],
                'next_content_offset':None,'restored_from':None}
    async def invoke(_operation,request): return record(request.revision)
    ui.invoke=invoke
    async def fixture(request):
        path=request.url.path.removeprefix(PREFIX)
        if path=='/api/whoami':return JSONResponse({'name':'Browser reviewer','team_name':'Synthetic team','team_id':ENTRY,'participant_id':PERSON,'scopes':['entries:read','entries:write']})
        if path=='/browser-csrf':return JSONResponse({'header_name':'X-CSRFToken','token':'fixture-csrf'})
        if path in {'/api/entries/read','/api/pouch'}:return JSONResponse(record(int(request.query_params['revision']) if 'revision' in request.query_params else None))
        if path=='/api/pouch/export':return JSONResponse({**record(int(request.query_params['revision'])),'format':'teamcomms-pouch-export-v1','revision_path':'/pouch?revision='+request.query_params['revision']})
        if path=='/api/entries/revisions':return JSONResponse({'revisions':[record(i) for i in range(len(states),0,-1)],'next_offset':None})
        if path=='/api/entries' and request.method=='GET':return JSONResponse({'entries':[dict(record(),title=states[-1]['title'],modified_at='2026-09-14T17:00:00Z')],'next_offset':None})
        if path=='/_fixture/advance':
            states.append(dict(states[-1],content=states[-1]['content']+'\nRemote edit\n'));return JSONResponse(record())
        if path in {'/api/entries/update','/api/entries/restore'}:
            data=await request.json()
            import asyncio
            await asyncio.sleep(.3)
            if data['expected_revision']!=len(states):return JSONResponse({'error':'STALE_REVISION'},status_code=409)
            states.append(dict(states[-1],**data['changes']) if 'changes' in data else dict(states[data['revision']-1]))
            return JSONResponse(record())
        return JSONResponse({'error':'Unknown fixture request '+path},status_code=404)
    app=Starlette(routes=[Route('/api/whoami',fixture),Route('/browser-csrf',fixture),
        Route('/api/entries',fixture,methods=['GET','POST']),Route('/api/entries/read',fixture),Route('/api/pouch',fixture),Route('/api/pouch/export',fixture),
        Route('/api/entries/revisions',fixture),Route('/api/entries/update',fixture,methods=['POST']),
        Route('/api/entries/restore',fixture,methods=['POST']),Route('/_fixture/advance',fixture,methods=['POST']),*ui.routes(PREFIX+'/browser-csrf')])
    async def guard(scope,receive,send):
        if scope['type']=='http' and scope['method']=='POST' and '/api/' in scope['path']:
            if dict(scope['headers']).get(b'x-csrftoken')!=b'fixture-csrf':return await JSONResponse({'error':'CSRF required'},status_code=403)(scope,receive,send)
        token=current_principal.set(actor)
        try:await app(scope,receive,send)
        finally:current_principal.reset(token)
    uvicorn.run(Starlette(routes=[Mount(PREFIX,guard)]),host='127.0.0.1',port=port,log_level='warning')


def check():
    from playwright.sync_api import sync_playwright, expect
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}{PREFIX}'
    proc=subprocess.Popen([str(ROOT/'.venv/bin/python'),__file__,'--serve',str(port)])
    try:
        for _ in range(50):
            try:urllib.request.urlopen(base+'/browser-csrf',timeout=.3);break
            except OSError:time.sleep(.1)
        else:raise RuntimeError('Browser fixture did not start')
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1100})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base+'/entries/'+ENTRY)
            page.locator('#editor-pane').wait_for(state='visible');expect(page.locator('#save-state')).to_have_text('Saved')
            value=lambda:page.evaluate("document.querySelector('.CodeMirror').CodeMirror.getValue()")
            edit=lambda text:page.evaluate('(text)=>document.querySelector(".CodeMirror").CodeMirror.setValue(text)',text)
            assert value()==SOURCE
            # Pouch follows the canonical route; a pinned revision cannot save.
            page.goto(base+'/pouch?revision=1')
            expect(page.locator('#save-state')).to_have_text('Saved revision — read only')
            assert value()==SOURCE
            expect(page.locator('#save')).to_be_disabled()
            assert page.evaluate("document.querySelector('.CodeMirror').CodeMirror.getOption('readOnly')") is True
            assert page.locator('#permalink').get_attribute('href')==PREFIX+'/pouch?revision=1'
            page.click('#current-document')
            page.locator('#editor-pane').wait_for(state='visible')
            expect(page.locator('#save-state')).to_have_text('Saved')
            assert page.url==base+'/pouch'
            edit(SOURCE+'\nPouch draft\n');page.click('#save')
            expect(page.locator('#save-state')).to_have_text('Saved')
            assert page.url==base+'/pouch'
            with page.expect_download() as download:
                page.click('#export-saved')
            exported=json.loads(Path(download.value.path()).read_text())
            assert exported['revision']==2 and exported['state']['content'].endswith('Pouch draft\n'), exported
            page.goto(base+'/entries/'+ENTRY+'?revision=1')
            expect(page.locator('#save')).to_be_disabled()
            page.goto(base+'/entries/'+ENTRY)
            page.locator('#editor-pane').wait_for(state='visible')
            expect(page.locator('#save-state')).to_have_text('Saved')
            edit(SOURCE);page.click('#save');expect(page.locator('#save-state')).to_have_text('Saved')
            page.click('#rendered-mode');page.wait_for_selector('#rendered table')
            assert page.locator('#rendered table tr').count()==2
            assert page.locator('#rendered a').get_attribute('href')=='https://example.org/'
            assert page.locator('#rendered code.language-python').inner_text()=='x = "<safe>"\nprint(x)\n'
            assert page.locator('#rendered .katex').count()==1
            assert page.evaluate('window.INJECTED') is None
            page.click('#source-mode');assert value()==SOURCE
            edit(SOURCE+'\nMy local edit\n')
            page.route('**/api/entries/update',lambda route:route.fulfill(status=503,json={'error':'Synthetic save failure'}))
            page.click('#save');expect(page.locator('#save-state')).to_have_attribute('data-state','error')
            assert value().endswith('My local edit\n')
            page.reload();page.wait_for_selector('#recover');page.click('#recover');assert value().endswith('My local edit\n')
            page.unroute('**/api/entries/update')
            page.request.post(base+'/_fixture/advance')
            page.click('#save');page.wait_for_selector('#conflict')
            assert value().endswith('My local edit\n')
            page.click('#show-remote');expect(page.locator('#version-content')).to_contain_text('Remote edit')
            assert 'My local edit' in page.locator('#draft-content').inner_text()
            page.once('dialog',lambda dialog:dialog.accept());page.click('#rebase');page.click('#save')
            expect(page.locator('#save-state')).to_have_text('Saved')
            result=page.request.get(base+'/api/entries/read').json();assert result['state']['content'].endswith('My local edit\n')
            assert result['state']['metadata']=={'kept':True}
            page.click('#history summary');page.get_by_role('button',name='Compare v1',exact=True).click()
            expect(page.locator('#version-content')).to_contain_text('# A shared document')
            page.once('dialog',lambda dialog:dialog.accept());page.click('#restore-version')
            expect(page.locator('#save-state')).to_contain_text('Restored')
            assert value()==SOURCE
            long_text=SOURCE+'\n'+('Long document line with spaces  \n'*800)+'\n'
            edit(long_text);page.click('#save')
            edit(long_text+'Typed during save\n')
            expect(page.locator('#save-state')).to_have_text('Unsaved — newer typing retained')
            assert value()==long_text+'Typed during save\n'
            page.click('#save');expect(page.locator('#save-state')).to_have_text('Saved')
            assert page.request.get(base+'/api/entries/read').json()['state']['content']==value()
            # Copying an HTML table keeps its cells/spans in the source, not flattened text.
            page.evaluate('''() => {
              const cm=document.querySelector('.CodeMirror').CodeMirror;
              cm.setCursor(cm.lineCount(),0);
              const data=new DataTransfer();data.setData('text/html','<table><tr><th>A</th><th>B</th></tr><tr><td colspan="2">Both</td></tr></table>');data.setData('text/plain','A B Both');
              cm.getInputField().dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));
            }''')
            assert '<table>' in value() and 'colspan="2"' in value()
            # Return the fixture to its compact document for visual review.
            edit(SOURCE);page.click('#save');expect(page.locator('#save-state')).to_have_text('Saved')
            page.select_option('#theme','dark');page.screenshot(path=str(ROOT/'dist/interface-dark.png'),full_page=True)
            page.select_option('#theme','light');page.click('#rendered-mode');page.wait_for_selector('#rendered table')
            page.screenshot(path=str(ROOT/'dist/interface-light.png'),full_page=True)
            assert not errors,errors
            browser.close()
            print('PASS: canonical Pouch save/export, pinned read-only links, prefixed assets, source/rendered structure, sanitization/math, failed save/reload recovery, concurrent conflict/diff/reconciliation, metadata preservation, restoration, long-document and in-flight typing preservation, HTML table paste, light/dark rendering')
    finally:
        proc.terminate();proc.wait(timeout=10)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--serve',type=int);args=parser.parse_args()
    if args.serve:serve(args.serve)
    else:
        (ROOT/'dist').mkdir(exist_ok=True)
        check()
