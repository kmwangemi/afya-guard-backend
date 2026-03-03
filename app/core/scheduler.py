"""app/core/scheduler.py — APScheduler CRON jobs."""

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)

scheduler = AsyncIOScheduler()


async def _render_keepalive_job():
    """Ping own health endpoint every 10 min to prevent Render cold starts."""
    url = f"{settings.BASE_URL}/health"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
        logger.debug(
            "Keep-alive ping sent",
            extra={"url": url, "status_code": response.status_code},
        )
    except Exception as exc:
        logger.warning("Keep-alive ping failed", extra={"url": url, "error": str(exc)})


def start_scheduler():
    """Register and start all CRON jobs. Call from app lifespan."""
    scheduler.add_job(
        _render_keepalive_job,
        trigger=IntervalTrigger(minutes=10),
        id="render_keepalive",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started")


def stop_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")
