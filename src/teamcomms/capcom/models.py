"""Shared topics, immutable notices and per-participant attention records."""
from django.db import models


class Topic(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    team = models.ForeignKey('teamcomms_service.Team', on_delete=models.PROTECT)
    owner = models.ForeignKey('teamcomms_service.Participant', on_delete=models.PROTECT)
    key = models.CharField(max_length=160)
    title = models.CharField(max_length=240)
    revision = models.PositiveIntegerField(default=1)
    links = models.JSONField(default=dict)
    last_sequence = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['team','key'],name='tc_topic_key')]


class TopicRevision(models.Model):
    topic = models.ForeignKey(Topic,on_delete=models.PROTECT)
    number = models.PositiveIntegerField()
    author = models.ForeignKey('teamcomms_service.Participant',on_delete=models.PROTECT)
    state = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['topic','number'],name='tc_topic_revision')]


class TopicReference(models.Model):
    topic_revision = models.ForeignKey(TopicRevision,on_delete=models.PROTECT)
    entry = models.ForeignKey('teamcomms_entries.Entry',on_delete=models.PROTECT)
    revision = models.ForeignKey('teamcomms_entries.Revision',null=True,on_delete=models.PROTECT)


class Notice(models.Model):
    id = models.UUIDField(primary_key=True,editable=False)
    topic = models.ForeignKey(Topic,on_delete=models.PROTECT)
    sequence = models.PositiveBigIntegerField()
    author = models.ForeignKey('teamcomms_service.Participant',on_delete=models.PROTECT)
    kind = models.CharField(max_length=20)
    urgency = models.CharField(max_length=16)
    content = models.TextField()
    source = models.CharField(max_length=240,blank=True)
    observed_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    published_message = models.OneToOneField('teamcomms_comms.Message',null=True,on_delete=models.PROTECT,related_name='capcom_notice')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['topic','sequence'],name='tc_notice_sequence')]


class Decision(models.Model):
    notice = models.OneToOneField(Notice,primary_key=True,on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(default=1)
    resolution = models.TextField(blank=True)
    resolved_by = models.ForeignKey('teamcomms_service.Participant',null=True,on_delete=models.PROTECT)
    resolved_at = models.DateTimeField(null=True)


class Follow(models.Model):
    topic = models.ForeignKey(Topic,on_delete=models.PROTECT)
    participant = models.ForeignKey('teamcomms_service.Participant',on_delete=models.PROTECT)
    following = models.BooleanField(default=False)
    read_sequence = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['topic','participant'],name='tc_topic_follow')]


class MutationReceipt(models.Model):
    id = models.UUIDField(primary_key=True,editable=False)
    team = models.ForeignKey('teamcomms_service.Team',on_delete=models.PROTECT)
    author = models.ForeignKey('teamcomms_service.Participant',on_delete=models.PROTECT)
    request = models.JSONField()
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)


class AttentionPolicy(models.Model):
    session = models.OneToOneField('teamcomms_comms.Session',primary_key=True,on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(default=1)
    routine_mode = models.CharField(max_length=16,default='immediate')
    batch_seconds = models.PositiveIntegerField(default=30)
    quiet_until = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(auto_now=True)


class Presentation(models.Model):
    delivery = models.OneToOneField('teamcomms_comms.Delivery',primary_key=True,on_delete=models.PROTECT)
    disposition = models.CharField(max_length=16)
    due_at = models.DateTimeField(null=True)
    coalesced_into = models.ForeignKey('teamcomms_comms.Delivery',null=True,on_delete=models.PROTECT,related_name='+')
    updated_at = models.DateTimeField(auto_now=True)


class ConversationRead(models.Model):
    participant = models.ForeignKey('teamcomms_service.Participant',on_delete=models.PROTECT)
    message = models.ForeignKey('teamcomms_comms.Message',on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['participant','message'],name='tc_capcom_conversation_read')]
