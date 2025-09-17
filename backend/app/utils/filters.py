from __future__ import annotations
from sqlalchemy import and_, or_
from app.models.entity import Entity


def job_scoped_condition(base_cond, entity_type: str, job_id):
    """
    Build a condition adding job scoping rules:
    - message / reaction / attachment: always scoped to the provided job_id (or restricted to NULL if job_id not set explicitly).
    - user / channel / custom_emoji: treated as global reference data (shared across jobs) — no job filter applied.
    """
    if entity_type in ("message", "reaction", "attachment"):
        if job_id is not None:
            return and_(base_cond, Entity.job_id == job_id)
        else:
            return and_(base_cond, Entity.job_id.is_(None))
    return base_cond
