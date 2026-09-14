"""Bounded receiver attention behavior; no native clients, credentials or suite."""
import asyncio,json,tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from teamcomms.connectors.receiver import Receiver
from teamcomms.connectors.state import Store


async def check(root):
    class Service:
        config=SimpleNamespace(attention_controls=True)
        def __init__(self):self.mode='recorded';self.calls=[]
        async def request(self,method,path,body):
            self.calls.append(path)
            return {'presentations':[{'delivery_id':x,'disposition':self.mode,'due_at':None,'coalesced_into':None} for x in body['delivery_ids']]}
        async def post(self,path,body):
            self.calls.append(path);return {'revision':2}
    class Adapter:
        def __init__(self):self.calls=0
        async def send(self,*args):self.calls+=1;return {'state':'accepted','detail':'Synthetic native acceptance'}
    def delivery():
        return {'delivery_id':str(uuid4()),'message_id':str(uuid4()),'sequence':1,'revision':1,'state':'pending','acknowledged_at':None,
            'message':{'message_id':str(uuid4()),'author_id':str(uuid4()),'author':{'name':'Synthetic program','kind':'program'},'content':'Routine update','reply_requested':False}}
    service=Service();adapter=Adapter();store=Store(root)
    receiver=Receiver(service,store,adapter,{},'synthetic.json');receiver.session_id=str(uuid4());store.put('instructions',True)
    one=delivery();store.ingest(one);await receiver.dispatch_pending()
    assert adapter.calls==0 and not store.pending() and service.calls==['/api/capcom/attention/plan']
    store.close();store=Store(root);receiver.store=store;store.ingest(one,advance=False);await receiver.dispatch_pending();assert adapter.calls==0
    service.mode='deferred';two=delivery();store.ingest(two);await receiver.dispatch_pending();assert adapter.calls==0 and len(store.pending())==1
    service.mode='eligible';await receiver.dispatch_pending();assert adapter.calls==1 and not store.pending()
    await receiver.dispatch_pending();assert adapter.calls==1
    three=delivery();store.ingest(three);store.update(three['delivery_id'],'injecting');service.mode='recorded';await receiver.dispatch_pending()
    assert store.pending()[0]['phase']=='uncertain' and adapter.calls==1
    store.close();print('PASS: routine record without model/native call, restart retention, visible deferral/resume, one native dispatch and uncertain-dispatch preservation; no native commissioning')


with tempfile.TemporaryDirectory(prefix='tc-capcom-receiver-') as root:asyncio.run(check(Path(root)))
