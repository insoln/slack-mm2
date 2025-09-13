import time
import os
from typing import Optional
from app.models.base import SessionLocal
from sqlalchemy import text as _text
from app.logging_config import backend_logger


KEY_MAP = {
    # entity_type -> (parsed_key, processed_key)
    "user": ("users_parsed", "users_processed"),
    "channel": ("channels_parsed", "channels_processed"),
    "message": ("messages_parsed", "messages_processed"),
    "reaction": ("reactions_parsed", "reactions_processed"),
    "attachment": ("attachments_parsed", "attachments_processed"),
    "custom_emoji": ("emojis_parsed", "emojis_processed"),
}


def _get_interval(entity_type: str) -> float:
    # Global override
    try:
        global_interval = os.environ.get("IMPORT_PROGRESS_FLUSH_INTERVAL_SEC")
        if global_interval:
            return float(global_interval)
    except Exception:
        pass
    # Backwards compatible per-type legacy envs (currently only reactions had one)
    if entity_type == "reaction":
        try:
            r = os.environ.get("REACTIONS_PROGRESS_FLUSH_INTERVAL_SEC")
            if r:
                return float(r)
        except Exception:
            pass
    return 2.0


class ProgressTracker:
    """Generic parsed/processed counters manager with throttled DB persistence.

    Writes absolute values into import_jobs.meta JSONB keys; safe under concurrent updates
    (last writer wins, values monotonic)."""

    def __init__(self, job_id: Optional[int], entity_type: str):
        self.job_id = job_id
        self.entity_type = entity_type
        self.parsed_key, self.processed_key = KEY_MAP[entity_type]
        self.parsed = 0
        self.processed = 0
        self._last_flushed = 0.0
        self._flush_interval = _get_interval(entity_type)
        # Track last written values to avoid redundant UPDATEs
        self._last_written_parsed = -1
        self._last_written_processed = -1

    async def incr_parsed(self, n: int = 1):
        self.parsed += n
        await self._maybe_flush()

    async def incr_processed(self, n: int = 1):
        self.processed += n
        await self._maybe_flush()

    async def flush(self):
        await self._do_flush(force=True)

    async def _maybe_flush(self):
        if self.job_id is None:
            return
        now = time.time()
        if (now - self._last_flushed) >= self._flush_interval:
            await self._do_flush()

    async def _do_flush(self, force: bool = False):
        if self.job_id is None:
            return
        if not force and (
            self.parsed == self._last_written_parsed
            and self.processed == self._last_written_processed
        ):
            return
        try:
            async with SessionLocal() as session:
                await session.execute(
                    _text(
                        f"""
                        UPDATE import_jobs
                        SET meta = COALESCE(meta, '{{}}'::jsonb)
                            || jsonb_build_object(:parsed_key, :parsed_val, :processed_key, :processed_val)
                        WHERE id = :job_id
                        """
                    ),
                    {
                        "parsed_key": self.parsed_key,
                        "processed_key": self.processed_key,
                        "parsed_val": int(self.parsed),
                        "processed_val": int(self.processed),
                        "job_id": self.job_id,
                    },
                )
                await session.commit()
            self._last_flushed = time.time()
            self._last_written_parsed = self.parsed
            self._last_written_processed = self.processed
        except Exception as e:
            backend_logger.debug(
                f"ProgressTracker flush failed ({self.entity_type}): {e}"
            )


def make_tracker(job_id: Optional[int], entity_type: str) -> ProgressTracker:
    return ProgressTracker(job_id, entity_type)
