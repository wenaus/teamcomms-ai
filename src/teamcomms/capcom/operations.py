"""Topic history is durable; current component state retains its own authority."""
from uuid import uuid4
from django.db import transaction
from django.db.models import Q,Exists,OuterRef
from django.db.models.fields.json import KeyTextTransform
from django.utils import timezone
from teamcomms.service.access import AccessError
from teamcomms.service.models import Team,Membership
from teamcomms.entries.models import Revision
from teamcomms.entries.operations import reference_entry,read_entry
from teamcomms.entries.schemas import ReadEntry
from teamcomms.comms.models import Message,Delivery
from teamcomms.comms.operations import send_message,get_message,delivery_record,notify_llm
from teamcomms.comms.schemas import SendMessage,Subscribe,MessageQuery,NotifyLLM
from teamcomms.comms.directory import subscribe,own_session
from teamcomms.dialog.operations import visible_events,event_record
from .models import Topic,TopicRevision,TopicReference,Notice,Decision,Follow,MutationReceipt,Presentation,ConversationRead


def begin(actor,request):
    actor.require('capcom:read');actor.require('capcom:write')
    Team.objects.select_for_update().get(pk=actor.team_id)
    if not Membership.objects.filter(pk=actor.membership_id,active=True).exists():raise AccessError('Inactive membership')
    payload=request.model_dump(mode='json')
    if payload.get('operation') == 'publish_notice' and not payload.get('notify_llm'):
        payload.pop('notify_llm',None);payload.pop('notify_llm_reason',None)
    old=MutationReceipt.objects.filter(pk=request.operation_id).first()
    if old:
        if old.team_id!=actor.team_id or old.author_id!=actor.participant_id or old.request!=payload:raise AccessError('Operation UUID already used differently',409)
        return old.result,payload
    return None,payload


def finish(actor,request,payload,result):
    MutationReceipt.objects.create(id=request.operation_id,team_id=actor.team_id,author_id=actor.participant_id,request=payload,result=result)
    return result


def topic_for(actor,topic_id):
    actor.require('capcom:read')
    row=Topic.objects.filter(pk=topic_id,team_id=actor.team_id).first()
    if row is None:raise AccessError('Topic not found',404)
    return row


def visible_conversation(actor):
    return Message.objects.filter(team_id=actor.team_id).filter(
        Q(author_id=actor.participant_id)|Q(deliveries__session__membership__participant_id=actor.participant_id)).distinct()


def unread_conversation(actor):
    return visible_conversation(actor).exclude(pk__in=ConversationRead.objects.filter(participant_id=actor.participant_id).values('message_id'))


def topic_record(row,actor):
    follow=Follow.objects.filter(topic=row,participant_id=actor.participant_id).first()
    conversation_unread='comms:read' in actor.scopes and unread_conversation(actor).filter(envelope__topic=row.key).exists()
    return {'topic_id':str(row.id),'key':row.key,'title':row.title,'revision':row.revision,'owner_id':str(row.owner_id),
        'last_sequence':row.last_sequence,'following':follow.following if follow else False,'read_sequence':follow.read_sequence if follow else 0,
        'conversation_unread':conversation_unread,'unread':conversation_unread or row.last_sequence>(follow.read_sequence if follow else 0),'updated_at':row.updated_at.isoformat(),'path':'/capcom/'+str(row.id)}


def save_revision(actor,row,links):
    references=[]
    for ref in links.entries:
        entry=reference_entry(actor,ref.entry_id)
        revision=Revision.objects.filter(entry=entry,number=ref.revision).first() if ref.revision else None
        if ref.revision and revision is None:raise AccessError('Referenced revision not found',404)
        references.append((entry,revision))
    if links.dialog_event_ids:
        actor.require('dialog:read')
        if visible_events(actor).filter(pk__in=links.dialog_event_ids).count()!=len(links.dialog_event_ids):raise AccessError('Dialog reference unavailable or duplicated',404)
    row.links=links.model_dump(mode='json');row.save()
    revision=TopicRevision.objects.create(topic=row,number=row.revision,author_id=actor.participant_id,state={'title':row.title,'links':row.links})
    for entry,version in references:TopicReference.objects.create(topic_revision=revision,entry=entry,revision=version)


@transaction.atomic
def create_topic(actor,request):
    cached,payload=begin(actor,request)
    if cached is not None:return cached
    if Topic.objects.filter(team_id=actor.team_id,key=request.key).exists():raise AccessError('Topic key exists; read it before editing',409)
    row=Topic(id=uuid4(),team_id=actor.team_id,owner_id=actor.participant_id,key=request.key,title=request.title)
    save_revision(actor,row,request.links)
    return finish(actor,request,payload,topic_record(row,actor))


@transaction.atomic
def update_topic(actor,request):
    cached,payload=begin(actor,request)
    if cached is not None:return cached
    row=topic_for(actor,request.topic_id)
    if actor.role!='admin' and row.owner_id!=actor.participant_id:raise AccessError('Only the topic owner or administrator may edit its references')
    if row.revision!=request.expected_revision:raise AccessError('STALE_REVISION: topic changed',409)
    row.title=request.title;row.revision+=1;save_revision(actor,row,request.links)
    return finish(actor,request,payload,topic_record(row,actor))


def list_topics(actor,query):
    actor.require('capcom:read')
    rows=Topic.objects.filter(team_id=actor.team_id)
    follow=Follow.objects.filter(topic_id=OuterRef('pk'),participant_id=actor.participant_id)
    if query.view=='followed':rows=rows.annotate(followed=Exists(follow.filter(following=True))).filter(followed=True)
    if query.view=='attention':
        rows=rows.annotate(read=Exists(follow.filter(read_sequence__gte=OuterRef('last_sequence'))),open_decision=Exists(Decision.objects.filter(notice__topic_id=OuterRef('pk'),resolved_at__isnull=True)))
        blocked=TopicReference.objects.filter(topic_revision__topic_id=OuterRef('pk'),topic_revision__number=OuterRef('revision'),entry__work__state='blocked')
        rows=rows.annotate(blocked_work=Exists(blocked) if 'inflight:read' in actor.scopes else Exists(blocked.none()))
        rows=rows.annotate(conversation_unread=Exists(unread_conversation(actor).annotate(topic_key=KeyTextTransform('topic','envelope')).filter(topic_key=OuterRef('key'))) if 'comms:read' in actor.scopes else Exists(Message.objects.none()))
        rows=rows.filter(Q(last_sequence__gt=0,read=False)|Q(open_decision=True)|Q(blocked_work=True)|Q(conversation_unread=True))
    if query.query:rows=rows.filter(Q(title__icontains=query.query)|Q(key__icontains=query.query))
    page=list(rows.order_by('-updated_at','id')[query.offset:query.offset+query.limit+1])
    return {'topics':[topic_record(row,actor) for row in page[:query.limit]],'next_offset':query.offset+query.limit if len(page)>query.limit else None}


def get_topic(actor,query):
    row=topic_for(actor,query.topic_id);result=topic_record(row,actor);sources=[];work=[];unavailable=0
    for ref in row.links.get('entries',[]):
        try:
            entry=reference_entry(actor,ref['entry_id'])
            if entry.kind=='inflight':
                from teamcomms.inflight.operations import get_work
                current=get_work(actor,ReadEntry(entry_id=entry.pk,max_content_length=1200))
                work.append({'entry_id':str(entry.pk),'title':current['state']['title'],'revision':current['revision'],'work':{k:current['work'][k] for k in ('state','owner_id','executor_id','blockers','outcome','evidence','parent_id','dependencies')},
                    'path':'/inflight/'+str(entry.pk),'sampled_at':timezone.now().isoformat(),'linked_revision':ref['revision']})
            else:
                saved=read_entry(actor,ReadEntry(**ref,max_content_length=1200))
                sources.append({'entry_id':str(entry.pk),'title':saved['state']['title'],'revision':saved['revision'],'content':saved['state']['content'],
                    'path':'/entries/'+str(entry.pk)+'?revision='+str(saved['revision'])})
        except AccessError:unavailable+=1
    events=[]
    ids=row.links.get('dialog_event_ids',[])
    if ids and 'dialog:read' in actor.scopes:
        events=[event_record(e,1000) for e in visible_events(actor).filter(pk__in=ids).select_related('session__membership').order_by('id')]
    unavailable+=len(ids)-len(events)
    decisions=Decision.objects.filter(notice__topic=row,resolved_at__isnull=True).select_related('notice').order_by('-notice__sequence')[:31]
    samples=Notice.objects.filter(topic=row,kind='state').order_by('source','-sequence').distinct('source')[:30]
    result.update(sampled_state=[{'source':n.source,'observed_at':n.observed_at.isoformat(),'content':n.content[:1200],'sequence':n.sequence} for n in samples],links=row.links,current_work=work,documents=sources,dialog=events,unavailable_references=unavailable,
        decisions=[{'notice_id':str(d.pk),'revision':d.revision,'content':d.notice.content[:1200],'author_id':str(d.notice.author_id)} for d in decisions[:30]],more_decisions=len(decisions)>30)
    return result


def notice_record(row,actor):
    decision=Decision.objects.filter(pk=row.pk).first()
    result={'notice_id':str(row.pk),'sequence':row.sequence,'author_id':str(row.author_id),'author_name':row.author.name,'kind':row.kind,'urgency':row.urgency,
        'content':row.content,'source':row.source,'observed_at':row.observed_at.isoformat(),'created_at':row.created_at.isoformat(),
        'decision':{'revision':decision.revision,'resolution':decision.resolution,'resolved_by':str(decision.resolved_by_id) if decision.resolved_by_id else None,
            'resolved_at':decision.resolved_at.isoformat() if decision.resolved_at else None} if decision else None,'deliveries':[]}
    envelope=row.published_message.envelope if row.published_message_id else {}
    result.update(notify_llm=envelope.get('notify_llm',False),notify_llm_reason=envelope.get('notify_llm_reason',''))
    if row.published_message_id and 'comms:read' in actor.scopes:
        deliveries=Delivery.objects.filter(message_id=row.published_message_id)
        if row.author_id!=actor.participant_id:deliveries=deliveries.filter(session__membership__participant_id=actor.participant_id)
        for d in deliveries.order_by('session_id')[:100]:
            presentation=Presentation.objects.filter(pk=d.pk).first()
            result['deliveries'].append({**delivery_record(d),'presentation':presentation_record(presentation)})
    return result


def presentation_record(row):
    return {'disposition':row.disposition,'due_at':row.due_at.isoformat() if row.due_at else None,
        'coalesced_into':str(row.coalesced_into_id) if row.coalesced_into_id else None} if row else {'disposition':'unplanned'}


def get_topic_notices(actor,query):
    row=topic_for(actor,query.topic_id)
    notices=Notice.objects.filter(topic=row).select_related('author','published_message').order_by('-sequence')
    if query.before is not None:notices=notices.filter(sequence__lt=query.before)
    page=list(notices[:query.limit+1]);selected=page[:query.limit]
    return {'notices':[notice_record(n,actor) for n in selected],'next_before':selected[-1].sequence if len(page)>query.limit else None,
        'last_sequence':row.last_sequence}


@transaction.atomic
def publish_notice(actor,request):
    cached,payload=begin(actor,request)
    if cached is not None:return cached
    topic=topic_for(actor,request.topic_id)
    if Notice.objects.filter(pk=request.notice_id).exists():raise AccessError('Notice ID already exists; retry its exact operation UUID',409)
    publication=None
    if request.audience:
        schema,operation=(NotifyLLM,notify_llm) if request.notify_llm else (SendMessage,send_message)
        intent={'notify_llm_reason':request.notify_llm_reason,'notify_llm_source':f'capcom:{topic.id}',
                'notify_llm_event_id':str(request.notice_id)} if request.notify_llm else {}
        publication=operation(actor,schema(message_id=request.notice_id,sender_session_id=request.sender_session_id,audience=request.audience,
            topic=topic.key,kind='notification',content=f'[{request.urgency} · {topic.title}] {request.content}\nCapcom topic: /capcom/{topic.id}',observed_at=request.observed_at,**intent))
    topic.last_sequence+=1;topic.save(update_fields=['last_sequence','updated_at'])
    notice=Notice.objects.create(id=request.notice_id,topic=topic,sequence=topic.last_sequence,author_id=actor.participant_id,kind=request.kind,urgency=request.urgency,
        content=request.content,source=request.source,observed_at=request.observed_at,published_message_id=request.notice_id if publication else None)
    if request.kind=='decision':Decision.objects.create(notice=notice)
    return finish(actor,request,payload,{'notice_id':str(notice.pk),'sequence':notice.sequence,'topic_id':str(topic.id),'publication':publication})


@transaction.atomic
def resolve_decision(actor,request):
    cached,payload=begin(actor,request)
    if cached is not None:return cached
    decision=Decision.objects.select_related('notice__topic').filter(pk=request.notice_id,notice__topic__team_id=actor.team_id).first()
    if not decision:raise AccessError('Decision not found',404)
    if actor.role!='admin' and decision.notice.author_id!=actor.participant_id:raise AccessError('Only the author or administrator resolves this decision')
    if decision.revision!=request.expected_revision or decision.resolved_at:raise AccessError('STALE_REVISION: decision already changed',409)
    if not request.resolution.strip():raise AccessError('Resolution required',400)
    decision.revision+=1;decision.resolution=request.resolution;decision.resolved_by_id=actor.participant_id;decision.resolved_at=timezone.now();decision.save()
    # Resolution is an immutable new result notice, so read watermarks cannot hide it.
    topic=decision.notice.topic;topic.last_sequence+=1;topic.save(update_fields=['last_sequence','updated_at'])
    result_notice=Notice.objects.create(id=uuid4(),topic=topic,sequence=topic.last_sequence,author_id=actor.participant_id,kind='result',urgency='normal',
        content=request.resolution,source='decision:'+str(decision.pk),observed_at=timezone.now())
    return finish(actor,request,payload,{'notice_id':str(decision.pk),'revision':decision.revision,'resolution_notice_id':str(result_notice.pk)})


@transaction.atomic
def follow_topic(actor,request):
    cached,payload=begin(actor,request)
    if cached is not None:return cached
    row=topic_for(actor,request.topic_id)
    follow,_=Follow.objects.get_or_create(topic=row,participant_id=actor.participant_id)
    if request.following is not None:follow.following=request.following
    if request.read_sequence is not None:
        if request.read_sequence>row.last_sequence:raise AccessError('Read watermark exceeds topic sequence',400)
        follow.read_sequence=max(follow.read_sequence,request.read_sequence)
    if request.session_id:
        if request.following is None:raise AccessError('Session subscription requires explicit following value',400)
        subscribe(actor,Subscribe(session_id=request.session_id,topic=row.key,active=request.following))
    if request.message_ids:
        actor.require('comms:read')
        visible=visible_conversation(actor).filter(pk__in=request.message_ids,envelope__topic=row.key)
        if visible.count()!=len(set(request.message_ids)):raise AccessError('Conversation message unavailable',404)
        for message in visible:ConversationRead.objects.get_or_create(participant_id=actor.participant_id,message=message)
    follow.save()
    return finish(actor,request,payload,topic_record(row,actor))


def get_topic_conversation(actor,query):
    topic=topic_for(actor,query.topic_id);actor.require('comms:read')
    rows=Message.objects.filter(team_id=actor.team_id,envelope__topic=topic.key).filter(
        Q(author_id=actor.participant_id)|Q(deliveries__session__membership__participant_id=actor.participant_id)).distinct().order_by('-created_at','id')
    page=list(rows[query.offset:query.offset+query.limit+1])
    return {'messages':[get_message(actor,MessageQuery(message_id=row.pk,limit=100)) for row in page[:query.limit]],'next_offset':query.offset+query.limit if len(page)>query.limit else None}
