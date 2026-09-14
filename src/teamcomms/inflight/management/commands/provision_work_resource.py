"""Explicit trusted-host provisioning; never exposed through embedded HTTP auth."""
import json
from uuid import UUID,uuid4
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from teamcomms.service.access import Principal,SCOPES
from teamcomms.service.models import Membership
from teamcomms.comms.directory import register_resource
from teamcomms.comms.schemas import NewResource
from teamcomms.inflight.claims import manage_work_resource
from teamcomms.inflight.claim_schemas import ManageResource


class Command(BaseCommand):
    help='Trusted local administration: provision a canonical resource and optional custody; requires host DB administration access.'

    def add_arguments(self,parser):
        parser.add_argument('--custodian-id',required=True,type=UUID)
        parser.add_argument('--key',required=True)
        parser.add_argument('--kind',required=True,choices=['project','checkout','service'])
        parser.add_argument('--name',required=True)
        parser.add_argument('--host',default='')
        parser.add_argument('--project-id',type=UUID)
        parser.add_argument('--alias',action='append',default=[])
        parser.add_argument('--protection',choices=['advisory','local_flock'],default='advisory')

    @transaction.atomic
    def handle(self,*args,**options):
        member=Membership.objects.filter(participant_id=options['custodian_id'],active=True).first()
        if member is None:raise CommandError('Active custodian membership not found')
        # This is an explicit host administration boundary, like local migration
        # or provisioning. It does not alter membership or grant remote scopes.
        actor=Principal(member.participant_id,member.team_id,member.id,None,'admin',SCOPES)
        resource=register_resource(actor,NewResource(key=options['key'],kind=options['kind'],name=options['name'],
            host=options['host'],project_id=options['project_id'],aliases=options['alias']))
        if options['kind']!='project':
            resource=manage_work_resource(actor,ManageResource(operation_id=uuid4(),action='provision',resource_id=resource['resource_id'],
                custodian_id=member.participant_id,protection=options['protection'],reason='Explicit trusted-host resource provisioning'))
        self.stdout.write(json.dumps(resource))
