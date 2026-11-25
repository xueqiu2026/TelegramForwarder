import asyncio
import logging
from datetime import datetime, timedelta
import pytz

from models.models import get_session
from alpha.storage import job
from alpha.pipeline import run_pipeline, AlreadyRunning

logger = logging.getLogger(__name__)
TZ = pytz.timezone('Asia/Shanghai')


def latest_slot(now, hours=(7, 13, 19, 22)):
    now = now.astimezone(TZ)
    slots = [now.replace(hour=h, minute=0, second=0, microsecond=0) for h in hours]
    slots += [(now - timedelta(days=1)).replace(hour=max(hours), minute=0, second=0, microsecond=0)]
    return max(s for s in slots if s <= now).isoformat()


async def scheduler_loop(client, business='alpha'):
    if business == 'ai':
        from ai_digest.pipeline import job as load_job, run_pipeline as execute
    else:
        load_job, execute = job, run_pipeline
    while True:
        try:
            db = get_session()
            try:
                config = load_job(db)
                enabled, last_slot = config.enabled, config.last_slot
                hours = tuple(config.schedule_hours)
                db.commit()
            finally:
                db.close()
            slot = latest_slot(datetime.now(TZ), hours)
            if enabled and last_slot != slot:
                await execute(client=client, slot=slot)
        except AlreadyRunning:
            pass
        except Exception:
            logger.exception('%s任务异常；保留状态并继续调度', business)
        await asyncio.sleep(30)
