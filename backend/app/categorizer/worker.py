"""
Categorization Worker — runs LLM categorization on a schedule.

Mirrors the RSS worker pattern (APScheduler + signal handling).
Spawns the MCP server as a subprocess each cycle, connects as an MCP client,
and orchestrates LLM-based article categorization.

Run as: python -m backend.app.categorizer.worker
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import text

from ..config import settings
from ..database import engine
from .agent import run_agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("categorizer-worker")


def run_categorization():
    """Run a single categorization cycle (sync wrapper for async agent)."""
    if not settings.categorizer_enabled:
        log.info("Categorizer disabled via config — skipping")
        return

    try:
        stats = asyncio.run(run_agent())
        if "error" in stats:
            log.error("Categorization cycle failed: %s", stats["error"])
        else:
            log.info("Categorization stats: %s", stats)
    except Exception:
        log.exception("Unhandled error in categorization cycle")


def main():
    log.info("=== Categorization Worker starting ===")
    log.info(
        "Provider: %s | Interval: %d min | Batch size: %d | Threshold: %.2f | "
        "Discovery limit: %d | Discovery window: %d hours",
        settings.llm_provider,
        settings.categorizer_interval_minutes,
        settings.categorizer_batch_size,
        settings.categorizer_relevance_threshold,
        settings.categorizer_discovery_limit,
        settings.categorizer_discovery_since_hours,
    )

    # Verify database connectivity
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        log.info("Database connection OK")
    except Exception:
        log.exception("Cannot connect to database, exiting")
        sys.exit(1)

    if not settings.categorizer_enabled:
        log.warning("CATEGORIZER_ENABLED=false — worker will idle until enabled")

    # Run immediately on startup
    run_categorization()

    # Schedule periodic runs
    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_categorization,
        "interval",
        minutes=settings.categorizer_interval_minutes,
    )
    log.info("Scheduler started — categorizing every %d minutes",
             settings.categorizer_interval_minutes)

    def shutdown(signum, frame):
        log.info("Shutting down scheduler...")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    scheduler.start()


if __name__ == "__main__":
    main()
