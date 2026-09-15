"""Finite authenticated offer hints; consumers always re-read and atomically claim."""
from contextlib import aclosing
import json,time,logging
import psycopg
from psycopg import sql
from starlette.responses import JSONResponse,StreamingResponse
from teamcomms.service.access import current_authentication,AccessError
from teamcomms.service.dispatch import database_call
from teamcomms.comms.stream import listener_params,event,RECHECK_SECONDS
from .claim_schemas import AvailableOffers
from .execution import list_work_offers,channel


async def stream_offers(request):
    auth=current_authentication.get();listener=None
    async def read():
        actor=await auth.refresh()
        return actor,await database_call(list_work_offers,actor,query)
    try:
        query=AvailableOffers.model_validate(dict(request.query_params))
        actor,_=await read()
        listener=await psycopg.AsyncConnection.connect(**listener_params(),autocommit=True)
        await listener.execute(sql.SQL('LISTEN {}').format(sql.Identifier(channel(actor.participant_id))))
        _,first=await read()
    except (ValueError,AccessError,psycopg.Error) as error:
        if listener:await listener.close()
        return JSONResponse({'error':str(error) if isinstance(error,AccessError) else 'Offer stream unavailable'},
            status_code=error.status if isinstance(error,AccessError) else 400 if isinstance(error,ValueError) else 503)
    except BaseException:
        if listener:await listener.close()
        raise
    async def generate():
        page=first;last=None;deadline=time.monotonic()+25
        try:
            while True:
                fingerprint=json.dumps(page['offers'],sort_keys=True)
                if fingerprint!=last:
                    yield event('offers',{'session_id':str(query.session_id)});last=fingerprint
                remaining=deadline-time.monotonic()
                if remaining<=0:break
                async with aclosing(listener.notifies(timeout=min(RECHECK_SECONDS,remaining),stop_after=1)) as hints:
                    async for _ in hints:break
                _,page=await read()
                yield ': heartbeat\n\n'
        except AccessError as error:yield event('error',{'error':str(error),'status':error.status})
        except psycopg.Error:
            logging.getLogger(__name__).error('Offer stream database connection failed')
            yield event('error',{'error':'Database unavailable','status':503})
        finally:await listener.close()
    return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
