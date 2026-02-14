import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

# Configure logging properly
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


# Your cron job functions
async def daily_fraud_analysis():
    """Run daily at 2 AM"""
    logger.info(f"Running daily fraud analysis at {datetime.now()}")
    print(
        f"CRON JOB: Running daily fraud analysis at {datetime.now()}"
    )  # Add print for visibility
    # Your logic here
    pass


async def hourly_risk_assessment():
    """Run every hour"""
    logger.info(f"Running hourly risk assessment at {datetime.now()}")
    print(f"CRON JOB: Running hourly risk assessment at {datetime.now()}")
    # Your logic here
    pass


async def weekly_report_generation():
    """Run every Monday at 9 AM"""
    logger.info(f"Generating weekly report at {datetime.now()}")
    print(f"CRON JOB: Generating weekly report at {datetime.now()}")
    # Your logic here
    pass


async def every_14_minutes_task():
    """Run every 14 minutes"""
    logger.info(f"Running task every 14 minutes at {datetime.now()}")
    print(f"CRON JOB: Running task every 14 minutes at {datetime.now()}")
    # Your logic here
    pass


def start_scheduler():
    """Configure and start the scheduler"""

    print("=" * 50)
    print("STARTING SCHEDULER...")
    print("=" * 50)

    # Daily job at 2 AM
    scheduler.add_job(
        daily_fraud_analysis,
        CronTrigger(hour=2, minute=0),
        id="daily_fraud_analysis",
        name="Daily Fraud Analysis",
        replace_existing=True,
    )
    print("✓ Added: Daily Fraud Analysis (2 AM)")

    # Every hour
    scheduler.add_job(
        hourly_risk_assessment,
        CronTrigger(minute=0),
        id="hourly_risk_assessment",
        name="Hourly Risk Assessment",
        replace_existing=True,
    )
    print("✓ Added: Hourly Risk Assessment (Every hour)")

    # Every Monday at 9 AM
    scheduler.add_job(
        weekly_report_generation,
        CronTrigger(day_of_week="mon", hour=9, minute=0),
        id="weekly_report_generation",
        name="Weekly Report Generation",
        replace_existing=True,
    )
    print("✓ Added: Weekly Report Generation (Monday 9 AM)")

    # Every 14 minutes
    scheduler.add_job(
        every_14_minutes_task,
        IntervalTrigger(minutes=14),
        id="every_14_minutes_task",
        name="Every 14 Minutes Task",
        replace_existing=True,
    )
    print("✓ Added: Every 14 Minutes Task")

    scheduler.start()
    logger.info("Scheduler started successfully")
    print("=" * 50)
    print("SCHEDULER STARTED SUCCESSFULLY")
    print("=" * 50)

    # Print all scheduled jobs
    jobs = scheduler.get_jobs()
    print(f"\nTotal jobs scheduled: {len(jobs)}")
    for job in jobs:
        print(f"  - {job.name}: Next run at {job.next_run_time}")
    print("=" * 50)


def shutdown_scheduler():
    """Shutdown the scheduler gracefully"""
    print("Shutting down scheduler...")
    scheduler.shutdown()
    logger.info("Scheduler shut down")
    print("Scheduler shut down successfully")
