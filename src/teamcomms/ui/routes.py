"""Browser routes inside the existing authenticated application boundary."""

import difflib
from html import escape
import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from teamcomms.entries.operations import read_entry
from teamcomms.entries.schemas import ReadEntry
from teamcomms.service.access import AccessError, current_principal
from teamcomms.service.dispatch import invoke
from .rendering import render_body

ROOT = Path(__file__).parent
SECURITY_HEADERS = {
    'Cache-Control': 'no-store',
    'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'",
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'same-origin',
}


class Preview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content: str = Field(max_length=40000)


class Comparison(Preview):
    component: str = Field(default="entries", pattern="^(entries|inflight)$")
    entry_id: UUID
    revision: int = Field(ge=1)


def routes(browser_csrf_url=None):
    if browser_csrf_url is not None:
        parsed = urlsplit(browser_csrf_url)
        if (not browser_csrf_url.startswith('/') or browser_csrf_url.startswith('//')
                or parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
                or any(c in browser_csrf_url for c in '\\%')
                or any(p in {'.', '..'} for p in browser_csrf_url.split('/'))):
            raise ValueError('browser_csrf_url must be a same-origin absolute path')

    async def page(request):
        try:
            path = request.url.path.removeprefix(request.scope.get('root_path', ''))
            current_principal.get().require('capcom:read' if path == '/capcom' or path.startswith('/capcom/') else 'inflight:read' if path == '/inflight' or path.startswith('/inflight/') else 'entries:read')
        except AccessError as error:
            return JSONResponse({'error': str(error)}, status_code=error.status)
        prefix = request.scope.get('root_path', '').rstrip('/')
        config = escape(json.dumps({'prefix': prefix, 'csrf_url': browser_csrf_url}), quote=True)
        html = (ROOT / 'index.html').read_text().replace('__CONFIG__', config)
        guide = (ROOT / 'notify-llm.html').read_text() if path == '/' else ''
        html = html.replace('__NOTIFY_GUIDE__', guide)
        html = html.replace('__PREFIX__', escape(prefix, quote=True))
        return HTMLResponse(html, headers=SECURITY_HEADERS)

    async def render(request):
        try:
            actor = current_principal.get()
            if not actor.scopes & {'entries:read', 'inflight:read'}:
                raise AccessError('Insufficient permission')
            data = Preview.model_validate(await request.json())
            html = await run_in_threadpool(render_body, data.content)
            return JSONResponse({'html': html}, headers=SECURITY_HEADERS)
        except ValueError:
            return JSONResponse({'error': 'Invalid preview request'}, status_code=400)
        except AccessError as error:
            return JSONResponse({'error': str(error)}, status_code=error.status)

    async def compare(request):
        try:
            data = Comparison.model_validate(await request.json())
            operation = read_entry
            if data.component == 'inflight':
                from teamcomms.inflight.operations import get_work
                operation = get_work
            saved = await invoke(operation, ReadEntry(entry_id=data.entry_id,
                revision=data.revision, max_content_length=40000))
            def difference():
                return ''.join(difflib.unified_diff(
                    saved['state']['content'].splitlines(keepends=True),
                    data.content.splitlines(keepends=True),
                    fromfile=f'revision-{data.revision}', tofile='your-draft'))
            return JSONResponse({'diff': await run_in_threadpool(difference)}, headers=SECURITY_HEADERS)
        except ValueError:
            return JSONResponse({'error': 'Invalid comparison request'}, status_code=400)
        except AccessError as error:
            return JSONResponse({'error': str(error)}, status_code=error.status)

    return [Route('/', page), Route('/entries', page), Route('/entries/{entry_id:uuid}', page), Route('/pouch', page),
            Route('/capcom', page), Route('/capcom/{topic_id:uuid}', page), Route('/inflight', page), Route('/inflight/{entry_id:uuid}', page), Route('/sessions', page), Route('/dialog', page),
            Route('/api/entries/render', render, methods=['POST']),
            Route('/api/entries/compare', compare, methods=['POST']),
            Mount('/assets', StaticFiles(directory=ROOT / 'assets'))]
