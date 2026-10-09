import datetime
from collections.abc import Callable
from typing import Any

from apscheduler.job import Job
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from kmua.database.db import sync_engine
from kmua.logger import logger
from kmua.timezone import BOT_TIMEZONE


class _TaskScheduler:
    """Persistent APScheduler tasks, with memory fallback for non-serializable jobs.

    Saved jobs retain their explicit trigger timezone when restored after a restart."""

    def __init__(self):
        # Configure persistent storage and the in-memory fallback.
        jobstores = {
            "default": SQLAlchemyJobStore(
                engine=sync_engine,
            ),
            "memory": MemoryJobStore(),  # Fallback for non-serializable jobs.
        }
        self._scheduler = AsyncIOScheduler(jobstores=jobstores, timezone=BOT_TIMEZONE)

    def _add_job_with_fallback(
        self,
        func: Callable,
        trigger,
        id: str,
        args: list | None = None,
        kwargs: dict | None = None,
        replace_existing: bool = True,
    ) -> Job:
        """Add a persistent task, falling back to memory for non-serializable callables."""
        try:
            # Try persistent storage first.
            job = self._scheduler.add_job(
                func=func,
                trigger=trigger,
                id=id,
                args=args or [],
                kwargs=kwargs or {},
                replace_existing=replace_existing,
                jobstore="default",
            )
            return job
        except ValueError as e:
            if "cannot be serialized" in str(e) or "could not be determined" in str(e):
                # Non-serializable callables fall back to memory.
                logger.warning(
                    f"Job '{id}' cannot be serialized for persistent storage. "
                    f"Falling back to memory storage. Error: {e}"
                )
                job = self._scheduler.add_job(
                    func=func,
                    trigger=trigger,
                    id=f"{id}_memory",
                    args=args or [],
                    kwargs=kwargs or {},
                    replace_existing=replace_existing,
                    jobstore="memory",
                )
                logger.info(
                    f"Job '{id}' added to memory storage (will NOT survive restart)"
                )
                return job
            else:
                # Propagate unrelated errors.
                raise

    def start(self) -> None:
        """Start scheduling and restore saved jobs from the database."""
        if not self._scheduler.running:
            self._scheduler.start()
            # Check the persistent job store.
            jobstore = self._scheduler._jobstores.get("default")
            if jobstore:
                logger.success(
                    f"Job scheduler started with persistent storage ({jobstore.__class__.__name__})"
                )
                # Report the number of restored jobs.
                try:
                    jobs = self._scheduler.get_jobs(jobstore="default")
                    logger.info(f"Loaded {len(jobs)} jobs from persistent storage")
                except Exception as e:
                    logger.warning(f"Could not list jobs from storage: {e}")
            else:
                logger.warning("Job scheduler started WITHOUT persistent storage")

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the scheduler."""
        self._scheduler.shutdown(wait=wait)
        logger.debug("Job scheduler shutdown")

    def add_onetime_job(
        self,
        job_id: str,
        func: Callable,
        run_date: datetime.datetime,
        args: list | None = None,
        kwargs: dict | None = None,
        replace_existing: bool = True,
    ) -> Job:
        """Schedule one execution. Naive run dates use the bot timezone (UTC+7)."""
        trigger = DateTrigger(run_date=run_date, timezone=BOT_TIMEZONE)
        logger.debug(f"add one-time job: {job_id} at {run_date}")

        return self._add_job_with_fallback(
            func=func,
            trigger=trigger,
            id=job_id,
            args=args,
            kwargs=kwargs,
            replace_existing=replace_existing,
        )

    def add_interval_job(
        self,
        job_id: str,
        func: Callable,
        seconds: int = 0,
        minutes: int = 0,
        hours: int = 0,
        days: int = 0,
        start_date: str | None = None,
        end_date: str | None = None,
        args: list | None = None,
        kwargs: dict | None = None,
        replace_existing: bool = True,
    ) -> Job:
        """Schedule a repeating interval, using UTC+7 for naive date boundaries."""
        trigger = IntervalTrigger(
            seconds=seconds,
            minutes=minutes,
            hours=hours,
            days=days,
            start_date=start_date,
            end_date=end_date,
            timezone=BOT_TIMEZONE,
        )
        logger.debug(
            f"add interval job: {job_id} every {seconds}s, {minutes}m, {hours}h, {days}d"
        )

        return self._add_job_with_fallback(
            func=func,
            trigger=trigger,
            id=job_id,
            args=args,
            kwargs=kwargs,
            replace_existing=replace_existing,
        )

    def add_daily_job(
        self,
        job_id: str,
        func: Callable,
        hour: int | str = 0,
        minute: int | str = 0,
        second: int | str = 0,
        timezone: datetime.tzinfo = BOT_TIMEZONE,
        start_date: str | None = None,
        end_date: str | None = None,
        args: list | None = None,
        kwargs: dict | None = None,
        replace_existing: bool = True,
    ) -> Job:
        """Schedule a daily wall-clock time in UTC+7 unless a timezone is supplied."""
        trigger = CronTrigger(
            hour=hour,
            minute=minute,
            second=second,
            timezone=timezone,
            start_date=start_date,
            end_date=end_date,
        )
        logger.debug(f"add daily job: {job_id} at {hour}:{minute}:{second} {timezone}")

        return self._add_job_with_fallback(
            func=func,
            trigger=trigger,
            id=job_id,
            args=args,
            kwargs=kwargs,
            replace_existing=replace_existing,
        )

    def remove_job(self, job_id: str) -> None:
        """Remove a task from persistent storage or its memory fallback."""
        # Try removing the persistent task first.
        try:
            self._scheduler.remove_job(job_id, jobstore="default")
            logger.debug(f"Removed job: {job_id}")
            return
        except Exception:
            pass

        # Fallback task ids carry the _memory suffix.
        try:
            self._scheduler.remove_job(f"{job_id}_memory", jobstore="memory")
            logger.debug(f"Removed job from memory: {job_id}")
        except Exception:
            pass

    def get_job(self, job_id: str) -> Job | None:
        """Get a persistent or memory task by id, returning None if it is absent."""
        # Look in persistent storage first.
        try:
            job = self._scheduler.get_job(job_id, jobstore="default")
            if job is not None:
                return job
        except Exception:
            pass

        # Fallback task ids carry the _memory suffix.
        try:
            return self._scheduler.get_job(f"{job_id}_memory", jobstore="memory")
        except Exception:
            return None

    def get_all_jobs(self) -> list[Job]:
        """List both persistent and in-memory tasks."""
        jobs = []
        # Read tasks from persistent storage.
        try:
            jobs.extend(self._scheduler.get_jobs(jobstore="default"))
        except Exception:
            pass
        # Read tasks from memory storage.
        try:
            jobs.extend(self._scheduler.get_jobs(jobstore="memory"))
        except Exception:
            pass
        return jobs

    def pause_job(self, job_id: str) -> None:
        """Pause a persistent task or its memory fallback."""
        # Try pausing the persistent task first.
        try:
            self._scheduler.pause_job(job_id, jobstore="default")
            logger.debug(f"Paused job: {job_id}")
            return
        except Exception:
            pass

        # Try pausing its in-memory fallback.
        try:
            self._scheduler.pause_job(f"{job_id}_memory", jobstore="memory")
            logger.debug(f"Paused job from memory: {job_id}")
        except Exception:
            pass

    def resume_job(self, job_id: str) -> None:
        """Resume a persistent task or its memory fallback."""
        # Try resuming the persistent task first.
        try:
            self._scheduler.resume_job(job_id, jobstore="default")
            logger.debug(f"Resumed job: {job_id}")
            return
        except Exception:
            pass

        # Try resuming its in-memory fallback.
        try:
            self._scheduler.resume_job(f"{job_id}_memory", jobstore="memory")
            logger.debug(f"Resumed job from memory: {job_id}")
        except Exception:
            pass

    def modify_job(
        self,
        job_id: str,
        **changes: Any,
    ) -> Job:
        """Update a persistent task and return the modified Job."""
        job = self._scheduler.modify_job(job_id, jobstore="default", **changes)
        logger.debug(f"Modified job: {job_id}")
        return job


jobqueue = _TaskScheduler()

__all__ = ["jobqueue"]
