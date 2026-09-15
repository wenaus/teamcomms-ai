"""Explicit execution offers and durable, generation-fenced subprocess outcomes."""
from datetime import datetime
from django.db import connection,transaction
from django.utils import timezone
from teamcomms.service.access import AccessError
from teamcomms.comms.directory import own_session
from .models import WorkOffer,ExecutionRun
from . import claims


def channel(participant_id):return 'tc_offers_'+str(participant_id).replace('-','')


def ring_offers(participants):
    with connection.cursor() as cursor:
        for participant in participants:cursor.execute('SELECT pg_notify(%s, %s)',[channel(participant),'offers'])


def eligible_mode(offer,mode):
    spec=offer.specification.get('execution')
    if mode=='headless':
        after=spec and spec.get('headless_after')
        if not after or datetime.fromisoformat(after)>timezone.now():
            raise AccessError('Headless fallback is not eligible yet',409)


def list_work_offers(actor,query):
    actor.require('inflight:read');session=own_session(actor,query.session_id)
    rows=WorkOffer.objects.filter(work__entry__team_id=actor.team_id,state='open',expires_at__gt=timezone.now(),
        specification__eligible_participant_ids__contains=[str(actor.participant_id)],specification__has_key='execution')
    if query.offer_id:rows=rows.filter(pk=query.offer_id)
    if query.profile:rows=rows.filter(specification__execution__profile=query.profile)
    # A page cursor advances over all candidates, including temporarily ineligible ones.
    page=list(rows.select_related('work__entry').order_by('created_at','id')[query.offset:query.offset+query.limit+1]);result=[]
    for offer in page[:query.limit]:
        try:eligible_mode(offer,query.mode)
        except AccessError:continue
        if not set(offer.specification['required_capabilities'])<=set(session.capabilities):continue
        result.append({'offer_id':str(offer.pk),'entry_id':str(offer.work_id),'expected_revision':offer.work.entry.revision,
            'expected_generation':offer.generation,'expires_at':offer.expires_at.isoformat(),'specification':offer.specification})
    return {'offers':result,'next_offset':query.offset+query.limit if len(page)>query.limit else None,'server_time':timezone.now().isoformat()}


def execution_record(run):
    return {'run_id':str(run.pk),'claim_id':str(run.claim_id),'state':run.state,'specification':run.specification,'result':run.result}


@transaction.atomic
def record_execution(actor,request):
    cached,payload=claims.begin(actor,request)
    if cached is not None:return cached
    claim=claims.claim_for(actor,request.claim_id)
    claims.validate(claim,actor,request.expected_generation,live=request.action=='start')
    spec=claim.offer.specification.get('execution')
    if not spec:raise AccessError('Claim has no execution profile',409)
    if request.action=='start':
        if not request.command_sha256:raise AccessError('Execution command hash required',400)
        if ExecutionRun.objects.filter(claim=claim).exists() or ExecutionRun.objects.filter(pk=request.run_id).exists():
            raise AccessError('A prior execution exists; never relaunch automatically',409)
        run=ExecutionRun.objects.create(id=request.run_id,claim=claim,command_sha256=request.command_sha256,specification=spec)
    else:
        run=ExecutionRun.objects.filter(pk=request.run_id,claim=claim,state='active').first()
        if not run:raise AccessError('Execution absent or already stopped',409)
        if not request.stopped or request.exit_code is None or not request.outcome.strip() or not request.evidence:
            raise AccessError('Confirmed stop, exit status, outcome and evidence required',400)
        run.state='finished';run.result={'exit_code':request.exit_code,'outcome':request.outcome,'evidence':request.evidence}
        run.save(update_fields=['state','result'])
    return claims.finish(actor,request,payload,execution_record(run))
