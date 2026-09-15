"""Focused pre-render theme and Notify LLM composer checks; synthetic HTTP only."""
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from threading import Thread
from html import escape
import json
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1]/'src/teamcomms/ui'
PREFIX='/nested/tc'
class Handler(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if '/assets/' in self.path:
            self.path=self.path.removeprefix(PREFIX)
            return super().do_GET()
        body=(ROOT/'index.html').read_text().replace('__PREFIX__',PREFIX).replace('__CONFIG__',escape(json.dumps({'prefix':PREFIX,'csrf_url':None}),quote=True)).encode()
        self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'");self.end_headers();self.wfile.write(body)

server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,directory=str(ROOT)))
Thread(target=server.serve_forever,daemon=True).start()
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        for choice,system,expected in [('dark','light','dark'),('light','dark','light'),('system','dark','dark')]:
            page=browser.new_page(color_scheme=system)
            page.add_init_script(f"localStorage.setItem('teamcomms:{PREFIX}:theme', {json.dumps(choice)})")
            held=[]
            page.route('**/assets/interface.js',lambda route:held.append(route))
            page.goto(f'http://127.0.0.1:{server.server_port}{PREFIX}/',wait_until='commit')
            page.locator('header').wait_for()
            def rendered():
                result=page.evaluate("""() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve({theme:document.documentElement.dataset.theme,bg:getComputedStyle(document.documentElement).backgroundColor,paints:performance.getEntriesByType('paint').length}))))""")
                assert result['theme']==expected,result
                assert result['bg']==('rgb(20, 25, 32)' if expected=='dark' else 'rgb(245, 246, 248)'),result
            rendered()
            assert held, 'Main script must still be blocked during first paint check'
            held.pop().fulfill(body='/* Main shell deliberately omitted by fixture. */',content_type='text/javascript')
            page.wait_for_load_state('load')
            page.get_by_role('link',name='Pouch',exact=True).click(no_wait_after=True)
            page.wait_for_url('**/pouch',wait_until='commit');page.locator('header').wait_for();rendered()
            held.pop().fulfill(body='',content_type='text/javascript');page.wait_for_load_state('load')
            page.close()
        page=browser.new_page()
        page.route('**/assets/interface.js',lambda route:route.fulfill(body='',content_type='text/javascript'))
        page.goto(f'http://127.0.0.1:{server.server_port}{PREFIX}/')
        page.evaluate("""() => {
          window.sent=[];window.fixtureErrors=[];
          const ai='11111111-1111-4111-8111-111111111111',session='22222222-2222-4222-8222-222222222222';
          const api=async(path,body)=>{
            if(path.startsWith('/api/comms/sessions'))return {sessions:[{participant_id:ai,session_id:session,name:'Selected AI',host:'fixture',state:'idle'}],next_offset:null};
            if(path.startsWith('/api/participants'))return {participants:[{participant_id:ai,kind:'ai'}],next_offset:null};
            window.sent.push(body);return {};
          };
          const composer=new TeamCommsCapcom('/nested/tc',api,{participant_id:ai,scopes:['comms:write']},e=>fixtureErrors.push(e));
          composer.open=async()=>{};composer.list=async()=>{};
          const panel=document.createElement('div');panel.className='capcom';document.querySelector('main').replaceChildren(panel);
          composer.composer(panel,'33333333-3333-4333-8333-333333333333');
        }""")
        page.get_by_text('Add a notice or decision',exact=True).click()
        expect(page.locator('#capcom-notify-llm')).not_to_be_checked()
        page.fill('#capcom-content','Ordinary notice');page.get_by_role('button',name='Record notice',exact=True).click()
        page.wait_for_function('sent.length===1');assert not page.evaluate('sent[0].notify_llm || false')
        page.locator('#capcom-notify-llm').check()
        page.get_by_label('Reason for requesting model attention').fill('Please review the finding')
        expect(page.get_by_label('Recipients').locator('option')).to_have_count(1)
        page.get_by_label('Recipients').select_option('22222222-2222-4222-8222-222222222222')
        page.fill('#capcom-content','Deliberate notice');page.get_by_role('button',name='Record notice and Notify LLM',exact=True).click()
        page.wait_for_function('sent.length===2')
        assert page.evaluate("sent[1].notify_llm && sent[1].notify_llm_reason==='Please review the finding' && sent[1].audience.session_ids.length===1")
        assert page.evaluate('fixtureErrors')==[]
        browser.close()
    print('PASS: saved dark/light/system themes before deferred shell and on navigation; Notify LLM off by default and explicit recipient/reason publication; synthetic only')
finally:
    server.shutdown();server.server_close()
