#!/usr/bin/env python3
"""Send one deliberate notification from an existing private config and JSON file."""
import argparse
import asyncio
import json
from pathlib import Path
from teamcomms.connectors.client import ServiceClient
from teamcomms.connectors.config import load
from teamcomms.connectors.state import Store, private_directory
from teamcomms.connectors.watcher import notify_llm

async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('notification', type=Path)
    args = parser.parse_args()
    config = load(args.config)
    service = ServiceClient(config)
    store = Store(private_directory(config.state_dir) / 'outgoing')
    try:
        result = await notify_llm(service, store, **json.loads(args.notification.read_text()))
        print(json.dumps(result))
    finally:
        store.close()
        await service.close()

if __name__ == '__main__':
    asyncio.run(main())
