from __future__ import annotations
from sqlalchemy import and_, or_
from app.models.entity import Entity


def job_scoped_condition(base_cond, entity_type: str, job_id):
    """
    Build a condition adding job scoping rules:
    - For job-specific types (message, reaction, attachment): restrict to the given job_id (or NULL if not provided).
    - For global types (user, channel, custom_emoji): do not constrain by job_id (to pick up legacy/global rows).
    """
    if entity_type in ("message", "reaction", "attachment"):
        if job_id is not None:
            return and_(base_cond, Entity.job_id == job_id)
        else:
            return and_(base_cond, Entity.job_id.is_(None))
    return base_cond
