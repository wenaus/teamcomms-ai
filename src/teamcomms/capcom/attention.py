"""Opt-in routine presentation without fabricated native or model receipts."""
from datetime import datetime,timedelta,timezone as dt_timezone
from django.db import transaction
from django.utils import timezone
from teamcomms.service.access import AccessError
from teamcomms.service.models import Team
from teamcomms.comms.directory import own_session
from teamcomms.comms.models import Delivery
from .models import AttentionPolicy,Presentation
from .operations import begin,finish,presentation_record


def policy_record(policy,session):
    return {'session_id':str(session.pk),'revision':policy.revision if policy else 0,
        'routine_mode':policy.routine_mode if policy else 'immediate','batch_seconds':policy.batch_seconds if policy else 30,
        'quiet_until':policy.quiet_until.isoformat() if policy and policy.quiet_until else None,
        'receiver_support':'attention-v1' in session.capabilities}


def get_attention_policy(actor,request):
    actor.require('capcom:read');session=own_session(actor,request.session_id)
    return policy_record(AttentionPolicy.objects.filter(pk=session.pk).first(),session)


@transaction.atomic
def set_attention_policy(actor,request):
    cached,payload=begin(actor,request)
    if cached is not None:return cached
    actor.require('sessions:write');session=own_session(actor,request.session_id)
    policy=AttentionPolicy.objects.filter(pk=session.pk).first()
    if request.expected_revision!=(policy.revision if policy else 0):raise AccessError('STALE_REVISION: attention policy changed',409)
    if request.quiet_until is not None:
        if request.quiet_until.utcoffset() is None or not timezone.now()<request.quiet_until<=timezone.now()+timedelta(hours=24):
            raise AccessError('Quiet deadline must be timezone-aware and within 24 hours',400)
    if policy is None:policy=AttentionPolicy(session=session,revision=0)
    policy.revision+=1;policy.routine_mode=request.routine_mode;policy.batch_seconds=request.batch_seconds;policy.quiet_until=request.quiet_until;policy.save()
    return finish(actor,request,payload,policy_record(policy,session))


def routine(row):
    notice=getattr(row.message,'capcom_notice',None)
    return bool(notice and not row.message.envelope.get('notify_llm') and notice.kind=='routine' and notice.urgency=='routine'
        and row.message.author_snapshot.get('kind')!='human' and not row.message.envelope.get('external_source')
        and row.message.envelope.get('kind')=='notification' and not row.message.envelope.get('reply_requested'))


@transaction.atomic
def plan_attention(actor,request):
    actor.require('capcom:read');actor.require('capcom:write');actor.require('comms:read')
    Team.objects.select_for_update().get(pk=actor.team_id)
    session=own_session(actor,request.session_id)
    rows=list(Delivery.objects.filter(pk__in=request.delivery_ids,session=session).select_related('message__capcom_notice'))
    if len(rows)!=len(request.delivery_ids):raise AccessError('Delivery does not belong to this session',404)
    policy=AttentionPolicy.objects.filter(pk=session.pk).first();now=timezone.now();results=[]
    for row in rows:
        old=Presentation.objects.filter(pk=row.pk).first()
        if old and old.disposition in {'recorded','coalesced'}:
            results.append({'delivery_id':str(row.pk),**presentation_record(old)});continue
        disposition='eligible';due=None;newest=None
        if policy and routine(row) and row.state=='pending' and not row.acknowledged_at:
            if policy.routine_mode=='record':disposition='recorded'
            else:
                if policy.quiet_until and policy.quiet_until>now:due=policy.quiet_until
                if policy.routine_mode=='batch':
                    seconds=policy.batch_seconds
                    start=datetime.fromtimestamp(int(row.message.created_at.timestamp())//seconds*seconds,dt_timezone.utc)
                    end=start+timedelta(seconds=seconds);due=max(due,end) if due else end
                    if due<=now:
                        notice=row.message.capcom_notice
                        candidates=Delivery.objects.filter(session=session,message__capcom_notice__topic=notice.topic,
                            message__author_id=row.message.author_id,message__capcom_notice__source=notice.source,message__created_at__gte=start,message__created_at__lt=end,
                            message__capcom_notice__kind='routine',message__capcom_notice__urgency='routine')
                        newest=candidates.order_by('-message__created_at','-sequence').first()
                        if newest and newest.pk!=row.pk:disposition='coalesced'
                if due and due>now:disposition='deferred'
        record,_=Presentation.objects.update_or_create(delivery=row,defaults={'disposition':disposition,'due_at':due,
            'coalesced_into':newest if disposition=='coalesced' else None})
        results.append({'delivery_id':str(row.pk),**presentation_record(record)})
    return {'presentations':results,'server_time':now.isoformat()}
