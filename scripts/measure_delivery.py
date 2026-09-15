#!/usr/bin/env python3
"""Publish one explicitly addressed marker or report its independent receipts.

The private evidence directory fixes the request before publication. Repeating
publish reuses its exact UUID/body. Report performs reads only, with no polling.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

from teamcomms.connectors.client import ServiceClient
from teamcomms.connectors.config import load


def now():
    return datetime.now(timezone.utc).isoformat()


def write_new(path, value):
    with path.open('x') as out:
        json.dump(value, out, indent=2)
        out.write('\n')
        out.flush()
        os.fsync(out.fileno())


def timing(created, delivery, history):
    """Use original receipt time; delivery.updated_at also changes on acknowledgment."""
    if history.get('next_offset') is not None:
        raise RuntimeError('Receipt history is truncated')
    def elapsed(value):
        return None if value is None else (datetime.fromisoformat(value) - created).total_seconds()
    receipts = history['receipts']
    reserved = next((r['created_at'] for r in receipts if r['request']['state'] == 'uncertain'), None)
    completed = next((r for r in receipts if r['request']['state'] in ('written', 'accepted')), None)
    return {'session_id': delivery['session_id'],
            'transport_reserved_seconds': elapsed(reserved),
            'native_report_state': completed['request']['state'] if completed else None,
            'native_report_seconds': elapsed(completed['created_at']) if completed else None,
            'considered_seconds': elapsed(delivery['considered_at'])}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['publish', 'report'])
    parser.add_argument('--config', required=True)
    parser.add_argument('--evidence-dir', required=True, type=Path)
    parser.add_argument('--session-id', action='append', default=[])
    parser.add_argument('--content-file', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    root = args.evidence_dir
    if not root.exists():
        if args.action != 'publish':
            parser.error('No publication evidence exists')
        root.mkdir(mode=0o700)
    request_path = root / 'request.json'
    if not request_path.exists():
        if args.action != 'publish' or not args.session_id or not args.content_file:
            parser.error('First publication requires explicit session IDs and content file')
        identity = str(uuid4())
        write_new(request_path, {'message_id': identity, 'kind': 'notification',
            'notify_llm': True, 'notify_llm_reason': 'Explicit bounded delivery measurement',
            'notify_llm_source': 'teamcomms-delivery-measurement', 'notify_llm_event_id': identity,
            'content': args.content_file.read_text(), 'observed_at': now(),
            'audience': {'session_ids': args.session_id}})
    elif args.session_id or args.content_file:
        parser.error('For a saved publication omit session IDs/content; the frozen request is reused')
    request = json.loads(request_path.read_text())
    client = ServiceClient(load(args.config))
    try:
        if args.action == 'publish':
            if (root / 'publication.json').exists():
                parser.error('Publication already confirmed; use report')
            started = now()
            result = await client.post('/messages', request)
            write_new(root / 'publication.json', {'started_at': started, 'returned_at': now(), 'result': result})
            print(json.dumps({'message_id': request['message_id'], 'destinations': len(result['deliveries'])}))
            return
        message = await client.get('/messages/read', message_id=request['message_id'], limit=100)
        if message.get('next_offset') is not None:
            raise RuntimeError('Unexpected truncated destination evidence')
        created = datetime.fromisoformat(message['created_at'])
        observed = datetime.fromisoformat(message['observed_at'])
        destinations = []
        for delivery in message['deliveries']:
            history = await client.get('/deliveries/history', delivery_id=delivery['delivery_id'], limit=100)
            destinations.append({'delivery': delivery, 'history': history,
                                 'timing': timing(created, delivery, history)})
        result = {'read_at': now(), 'message': message, 'destinations': destinations,
            'detection_to_commit_seconds': (created - observed).total_seconds(),
            'measurement': 'Synthetic observation at request preparation. Server receipt times bound transport/client reporting; model consideration is separate acknowledgment. Raw histories retain all timestamps. No source watcher polling delay is measured.'}
        path = root / ('report-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.json')
        write_new(path, result)
        print(json.dumps({'report': str(path), 'message_id': request['message_id'],
                          'deliveries': message['deliveries']}))
    finally:
        await client.close()


if __name__ == '__main__':
    asyncio.run(main())
