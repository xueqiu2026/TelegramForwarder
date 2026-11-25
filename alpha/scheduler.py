import asyncio
import logging
from datetime import datetime, timedelta
import pytz

from models.models import get_session
from alpha.storage import job
from alpha.pipeline import run_pipeline, AlreadyRunning

logger = logging.getLogger(__name__)
TZ = pytz.timezone('Asia/Shanghai')


def latest_slot(now):
    now = now.astimezone(TZ)
    slots = [now.replace(hour=h, minute=0, second=0, microsecond=0) for h in (7, 13, 19)]
    slots += [(now - timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)]
    return max(s for s in slots if s <= now).isoformat()


async def scheduler_loop(client):
    while True:
        db = get_session()
        try:
            config = job(db)
            enabled, last_slot = config.enabled, config.last_slot
            db.commit()
        finally:
            db.close()
        slot = latest_slot(datetime.now(TZ))
        if enabled and last_slot != slot:
            try:
                await run_pipeline(client=client, slot=slot)
            except AlreadyRunning:
                pass
            except Exception:
                logger.exception('机会任务失败；已保存处理与推送状态')
        await asyncio.sleep(30)
