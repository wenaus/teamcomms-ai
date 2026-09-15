"""Atomic executor claims, retained resource custody and fenced lifecycle."""
from datetime import timedelta
from uuid import uuid4
from django.db import transaction
from django.utils import timezone
from teamcomms.service.access import AccessError
from teamcomms.service.models import Team
from teamcomms.comms.models import Resource, Session
from teamcomms.entries.operations import _expected
from .models import ResourceCustody, WorkOffer, Claim, Reservation, CoordinationReceipt, GuardRun
from .operations import work_for, member, coordinator, snapshot, DONE


def begin(actor, request):
    actor.require('inflight:read');actor.require('inflight:write')
    if not Team.objects.select_for_update().filter(pk=actor.team_id).exists():
        raise AccessError('Team not found',404)
    member(actor,actor.participant_id)
    payload=request.model_dump(mode='json')
    # Retain exact equality with pre-execution coordination receipts.
    if payload.get('operation')=='offer' and payload.get('execution') is None:payload.pop('execution',None)
    if payload.get('operation')=='claim' and payload.get('mode')=='interactive':payload.pop('mode',None)
    receipt=CoordinationReceipt.objects.filter(pk=request.operation_id).first()
    if receipt:
        if receipt.team_id!=actor.team_id or receipt.author_id!=actor.participant_id or receipt.request!=payload:
            raise AccessError('Operation UUID was used with a different request or author',409)
        return receipt.result,payload
    return None,payload


def finish(actor,request,payload,value):
    CoordinationReceipt.objects.create(id=request.operation_id,team_id=actor.team_id,
        author_id=actor.participant_id,request=payload,result=value)
    return value


def resource_for(actor,resource_id):
    row=ResourceCustody.objects.select_related('resource','custodian').filter(pk=resource_id,resource__team_id=actor.team_id).first()
    if not row:raise AccessError('Managed resource not found',404)
    return row


def resource_record(row):
    return {'resource_id':str(row.pk),'key':row.resource.key,'name':row.resource.name,
        'kind':row.resource.kind,'host':row.resource.host,'aliases':row.resource.aliases,
        'custodian_id':str(row.custodian_id),'custodian_name':row.custodian.name,
        'generation':row.generation,'protection':row.protection,'transfer':row.transfer,
        'coverage':'Cooperating local guard entrypoints only' if row.protection=='local_flock' else 'Advisory; external commands are not excluded'}


@transaction.atomic
def manage_work_resource(actor,request):
    cached,payload=begin(actor,request)
    if cached is not None:return cached
    if request.action=='provision':
        actor.require('directory:write',admin=True)
        raw=Resource.objects.filter(pk=request.resource_id,team_id=actor.team_id).first()
        if raw is None:raise AccessError('Canonical resource not found',404)
        if request.custodian_id is None:raise AccessError('Resource needs a custodian',400)
        member(actor,request.custodian_id)
        if request.protection=='local_flock' and not raw.host:
            raise AccessError('Local guarding needs an explicit resource host',400)
        row=ResourceCustody.objects.filter(pk=raw.pk).first()
        if row:
            if row.custodian_id!=request.custodian_id or row.protection!=request.protection:
                raise AccessError('Existing resource custody/protection differs',409)
        else:row=ResourceCustody.objects.create(resource=raw,custodian_id=request.custodian_id,protection=request.protection)
    else:
        row=resource_for(actor,request.resource_id)
        if row.generation!=request.expected_generation:raise AccessError('STALE_GENERATION: resource changed',409)
        if row.reservations.filter(active=True).exists():raise AccessError('Resource remains reserved',409)
        if request.action!='accept_transfer' and actor.participant_id!=row.custodian_id and actor.role!='admin':
            raise AccessError('Only the custodian or administrator may offer/cancel transfer')
        if request.action=='offer_transfer':
            if not request.successor_id or request.successor_id==row.custodian_id or not request.reason.strip():
                raise AccessError('Transfer needs a successor and reason',400)
            member(actor,request.successor_id)
            row.transfer={'transfer_id':str(uuid4()),'successor_id':str(request.successor_id),'reason':request.reason}
        else:
            if not row.transfer or row.transfer['transfer_id']!=str(request.transfer_id):raise AccessError('Transfer superseded or resolved',409)
            if request.action=='accept_transfer':
                if row.transfer['successor_id']!=str(actor.participant_id):raise AccessError('Only the successor may accept custody')
                row.custodian_id=actor.participant_id;row.generation+=1
            row.transfer=None
        row.save()
        row=resource_for(actor,row.pk)
    return finish(actor,request,payload,resource_record(row))


def list_work_resources(actor,request):
    actor.require('inflight:read')
    rows=list(ResourceCustody.objects.select_related('resource','custodian').filter(resource__team_id=actor.team_id)
        .order_by('resource__key')[request.offset:request.offset+request.limit+1])
    values=[]
    for row in rows[:request.limit]:
        reservation=row.reservations.filter(active=True).select_related('claim').first()
        values.append({**resource_record(row),'reservation':{'claim_id':str(reservation.claim_id),
            'holder_id':str(reservation.claim.holder_id),'deadline':reservation.claim.deadline.isoformat(),
            'state':'expired_held' if reservation.claim.deadline<=timezone.now() else 'active'} if reservation else None})
    return {'resources':values,'next_offset':request.offset+request.limit if len(rows)>request.limit else None}


def expected_work(work,request):
    _expected(work.entry,request.expected_revision)
    if work.generation!=request.expected_generation:raise AccessError('STALE_GENERATION: executor or ownership changed',409)
    if work.state in DONE:raise AccessError('Closed work cannot be offered or claimed',409)
    if work.claims.filter(state='active').exists():raise AccessError('Work has an active or expired-held claim',409)


@transaction.atomic
def offer_work(actor,request):
    cached,payload=begin(actor,request)
    if cached is not None:return cached
    work=work_for(actor,request.entry_id);coordinator(actor,work);expected_work(work,request)
    if work.executor_id or work.handoff:raise AccessError('Clear existing executor or ownership offer first',409)
    if request.expires_at<=timezone.now():raise AccessError('Offer deadline must be in the future',400)
    for person in request.eligible_participant_ids:member(actor,person)
    resource_generations={}
    for resource_id in request.resource_ids:
        resource=resource_for(actor,resource_id)
        resource_generations[str(resource_id)]=resource.generation
        if request.policy=='guarded' and resource.protection!='local_flock':raise AccessError('Guarded offer contains an advisory resource',400)
    work.offers.filter(state='open').update(state='canceled')
    spec={key:payload[key] for key in ('eligible_participant_ids','resource_ids','required_capabilities','lease_seconds','policy')}
    if request.execution:
        if request.execution.headless_after and request.execution.headless_after>=request.expires_at:
            raise AccessError('Fallback deadline must precede offer expiry',400)
        if request.execution.permissions=='workspace_write' and request.policy!='guarded':
            raise AccessError('Mutation execution requires guarded resources',400)
        spec['execution']=request.execution.model_dump(mode='json')
    spec['resource_generations']=resource_generations
    offer=WorkOffer.objects.create(id=uuid4(),work=work,generation=work.generation,specification=spec,expires_at=request.expires_at)
    work.detail['execution_offer']={'offer_id':str(offer.id),**spec,'expires_at':offer.expires_at.isoformat()}
    value=snapshot(actor,work,'offer_work')
    from .execution import ring_offers
    ring_offers(request.eligible_participant_ids)
    return finish(actor,request,payload,{**value,'offer':work.detail['execution_offer']})


def claim_for(actor,claim_id):
    row=Claim.objects.select_related('offer','work__entry','holder').filter(pk=claim_id,work__entry__team_id=actor.team_id).first()
    if not row:raise AccessError('Claim not found',404)
    return row


def claim_record(row):
    resources=[{**resource_record(r.resource),'reservation_generation':r.generation,'active':r.active}
        for r in row.reservations.select_related('resource__resource','resource__custodian').order_by('resource_id')]
    from .execution import execution_record
    run=getattr(row,'execution_run',None)
    return {'execution_run':execution_record(run) if run else None,'claim_id':str(row.id),'offer_id':str(row.offer_id),'entry_id':str(row.work_id),
        'holder_id':str(row.holder_id),'holder_name':row.holder.name,
        'session_id':str(row.session_id) if row.session_id else None,'generation':row.generation,
        'state':'expired_held' if row.state=='active' and row.deadline<=timezone.now() else row.state,
        'deadline':row.deadline.isoformat(),'server_time':timezone.now().isoformat(),
        'lease_seconds':row.offer.specification['lease_seconds'],'policy':row.offer.specification['policy'],
        'execution':row.offer.specification.get('execution'),
        'guard_run_ids':[str(x) for x in row.guard_runs.filter(state='active').values_list('pk',flat=True)],
        'resources':resources,'work_revision':row.work.entry.revision,'owner_id':str(row.work.owner_id)}


@transaction.atomic
def claim_work(actor,request):
    cached,payload=begin(actor,request)
    if cached is not None:return cached
    offer=WorkOffer.objects.filter(pk=request.offer_id,work__entry__team_id=actor.team_id).first()
    if offer is None:raise AccessError('Offer not found',404)
    work=work_for(actor,offer.work_id);expected_work(work,request)
    if offer.state!='open' or offer.expires_at<=timezone.now() or offer.generation!=work.generation:
        raise AccessError('Offer expired, superseded or already claimed',409)
    if work.handoff or work.executor_id:raise AccessError('Owner handoff or executor assignment is unresolved',409)
    if str(actor.participant_id) not in offer.specification['eligible_participant_ids']:raise AccessError('Not in the eligible audience')
    session=None
    if request.session_id:
        session=Session.objects.filter(pk=request.session_id,membership__team_id=actor.team_id,
            membership__participant_id=actor.participant_id,membership__active=True).first()
        if session is None:raise AccessError('Claim session does not belong to caller',404)
    from .execution import eligible_mode
    eligible_mode(offer,request.mode)
    if request.mode=='headless' and session is None:raise AccessError('Headless claims require an owned worker session',400)
    needed=set(offer.specification['required_capabilities'])
    if needed and (not session or not needed<=set(session.capabilities)):raise AccessError('Claim session lacks required reported capabilities')
    resources=[resource_for(actor,key) for key in sorted(offer.specification['resource_ids'])]
    for resource in resources:
        if resource.generation!=offer.specification['resource_generations'][str(resource.pk)]:
            raise AccessError('STALE_RESOURCE: resource changed since this offer; make a fresh offer',409)
        if resource.transfer or resource.reservations.filter(active=True).exists():raise AccessError('RESOURCE_BUSY: entire reservation set rejected',409)
    work.executor_id=actor.participant_id;work.session=session;work.generation+=1;work.state='active'
    row=Claim.objects.create(id=uuid4(),offer=offer,work=work,holder_id=actor.participant_id,session=session,
        generation=work.generation,deadline=timezone.now()+timedelta(seconds=offer.specification['lease_seconds']))
    for resource in resources:
        resource.generation+=1;resource.save(update_fields=['generation'])
        Reservation.objects.create(claim=row,resource=resource,generation=resource.generation)
    offer.state='claimed';offer.save(update_fields=['state'])
    work.detail['claim_id']=str(row.id);work.detail['blockers']=''
    saved=snapshot(actor,work,'claim_work')
    row=claim_for(actor,row.pk)
    return finish(actor,request,payload,{**saved,'claim':claim_record(row)})


def validate(row,actor,generation,*,live=True,holder=True):
    if holder and row.holder_id!=actor.participant_id:raise AccessError('Only the claim holder may do this')
    if row.state!='active' or row.generation!=generation or row.work.generation!=generation:
        raise AccessError('STALE_CLAIM: claim or ownership generation changed',409)
    if live and row.deadline<=timezone.now():raise AccessError('CLAIM_EXPIRED: resources remain held pending stopped-work reconciliation',409)
    for reservation in row.reservations.select_related('resource'):
        if not reservation.active or reservation.generation!=reservation.resource.generation:
            raise AccessError('STALE_RESERVATION: resource generation changed',409)


@transaction.atomic
def update_claim(actor,request):
    cached,payload=begin(actor,request)
    if cached is not None:return cached
    row=claim_for(actor,request.claim_id);work=row.work
    stop=request.action=='confirm_stopped'
    if stop:coordinator(actor,work)
    validate(row,actor,request.expected_generation,live=request.action in {'renew','progress','complete'},holder=not stop)
    from .models import ExecutionRun
    execution=ExecutionRun.objects.filter(claim=row).first()
    if request.action in {'release','complete'} and execution and execution.state=='active':
        raise AccessError('Execution is active or its stop is unconfirmed',409)
    if request.action=='complete' and row.offer.specification.get('execution'):
        if not execution or execution.state!='finished' or execution.result['exit_code']!=0:
            raise AccessError('A successful stopped execution result is required',409)
        if request.outcome!=execution.result['outcome'] or request.evidence!=execution.result['evidence']:
            raise AccessError('Completion must match the recorded execution result',409)
    if request.action!='renew':_expected(work.entry,request.expected_revision)
    if request.action in {'release','complete'} and row.guard_runs.filter(state='active').exists():
        raise AccessError('A guarded command is still active or unconfirmed; stop and reconcile it first',409)
    if request.action=='renew':
        row.deadline=timezone.now()+timedelta(seconds=row.offer.specification['lease_seconds']);row.save(update_fields=['deadline'])
        return finish(actor,request,payload,{'claim':claim_record(row)})
    if request.action=='progress':
        if request.state=='blocked' and not request.reason.strip():raise AccessError('Blocked progress needs a reason',400)
        work.state=request.state;work.detail['blockers']=request.reason
    else:
        if request.action=='complete':
            if not request.outcome.strip() or not request.evidence or any(not x.strip() for x in request.evidence):raise AccessError('Completion requires outcome and evidence',400)
            if work.children.exclude(state='completed').exists() or work.dependencies.exclude(prerequisite__state='completed').exists():raise AccessError('Unresolved subtasks or dependencies',409)
            work.state='completed';work.detail.update(outcome=request.outcome,evidence=request.evidence,blockers='');work.handoff=None
            row.state='completed'
        else:
            if not request.reason.strip() or (stop and (not request.evidence or any(not x.strip() for x in request.evidence))):
                raise AccessError('Release needs a stop reason; reconciliation also needs stopped-work evidence',400)
            work.state='blocked';work.detail['blockers']=request.reason
            work.detail['stop_evidence']=request.evidence
            work.executor_id=work.session_id=None;work.generation+=1
            row.state='stopped' if stop else 'released'
            if stop and execution and execution.state=='active':
                execution.state='reconciled';execution.result={'reason':request.reason,'evidence':request.evidence};execution.save(update_fields=['state','result'])
            if stop:row.guard_runs.filter(state='active').update(state='reconciled',outcome={'reason':request.reason,'evidence':request.evidence})
        row.save(update_fields=['state']);row.reservations.update(active=False)
    saved=snapshot(actor,work,'claim_'+request.action)
    row=claim_for(actor,row.pk)
    return finish(actor,request,payload,{**saved,'claim':claim_record(row)})


def get_claim(actor,request):
    actor.require('inflight:read')
    return claim_record(claim_for(actor,request.claim_id))


@transaction.atomic
def validate_claim(actor,request):
    actor.require('inflight:read');actor.require('inflight:write')
    if not Team.objects.select_for_update().filter(pk=actor.team_id).exists():raise AccessError('Team not found',404)
    member(actor,actor.participant_id)
    row=claim_for(actor,request.claim_id);validate(row,actor,request.expected_generation)
    if not set(map(str,request.resource_ids))<=set(str(x) for x in row.reservations.values_list('resource_id',flat=True)):
        raise AccessError('Command resources are not reserved by this claim',409)
    return {'valid':True,'claim':claim_record(row)}


@transaction.atomic
def record_guard_run(actor,request):
    cached,payload=begin(actor,request)
    if cached is not None:return cached
    row=claim_for(actor,request.claim_id)
    validate(row,actor,request.expected_generation,live=request.action=='start')
    if request.action=='start':
        if not request.resource_ids or len(set(request.resource_ids))!=len(request.resource_ids) or not request.command_sha256:
            raise AccessError('A guard run needs distinct resources and command hash',400)
        if row.offer.specification['policy']!='guarded':raise AccessError('Claim is advisory; guarded policy required',409)
        held={r.resource_id:r for r in row.reservations.select_related('resource')}
        if not set(request.resource_ids)<=set(held) or any(held[x].resource.protection!='local_flock' for x in request.resource_ids):
            raise AccessError('Guard resources are not enforced reservations of this claim',409)
        if row.guard_runs.filter(state='active').exists() or GuardRun.objects.filter(pk=request.run_id).exists():
            raise AccessError('A prior guarded run remains active or unconfirmed',409)
        GuardRun.objects.create(id=request.run_id,claim=row,resource_ids=sorted(map(str,request.resource_ids)),command_sha256=request.command_sha256)
    else:
        run=GuardRun.objects.filter(pk=request.run_id,claim=row).first()
        if not run or run.state!='active':raise AccessError('Guard run already resolved or unknown',409)
        if not request.stopped:raise AccessError('Confirm the command process group has stopped',400)
        run.state='finished';run.outcome={'exit_code':request.exit_code,'stopped':True};run.save(update_fields=['state','outcome'])
    return finish(actor,request,payload,{'run_id':str(request.run_id),'state':'active' if request.action=='start' else 'finished'})
