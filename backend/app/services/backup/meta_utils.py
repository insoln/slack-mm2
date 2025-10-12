"""Utility for atomic JSONB meta updates on ImportJob.

Provides merge_job_meta coroutine to apply set/incr/max/remove and nested merges
without performing a read-modify-write cycle in Python (prevents lost updates).
"""

from __future__ import annotations
from typing import Any, Dict, Optional
import json
from sqlalchemy import text as _text
from app.models.base import SessionLocal
from app.logging_config import backend_logger

# NOTE: Keep keys small; this builder is simple and adequate for current scale.


async def merge_job_meta(
    job_id: int,
    *,
    set: Optional[Dict[str, Any]] = None,
    incr: Optional[Dict[str, int]] = None,
    max_keys: Optional[Dict[str, int]] = None,
    nested: Optional[Dict[str, Dict[str, Any]]] = None,
    remove: Optional[list[str]] = None,
) -> None:
    """Atomically merge and mutate meta JSONB for the given job_id.

    Parameters:
      set: direct key overwrites (simple JSON-serializable values)
      incr: numeric increments (added to existing int or 0)
      max_keys: numeric maxima (value = GREATEST(existing, candidate))
      nested: mapping of parent_key -> { child_key: value } merged one level deep
      remove: list of top-level keys to remove
    """
    if not any([set, incr, max_keys, nested, remove]):  # nothing to do
        return

    # Build dynamic SQL pieces
    remove_clause = ""
    if remove:
        # apply #- '{key}' sequentially
        for rk in remove:
            # basic sanitation (only allow simple keys)
            if rk and "'" not in rk and "{" not in rk:
                remove_clause += f" #- '{{{rk}}}'"

    set_pairs = []
    params: Dict[str, Any] = {"job_id": job_id}
    if set:
        for k, v in set.items():
            pname = f"set_{k}"
            # Store JSON text form and cast to jsonb in SQL to avoid type ambiguity
            params[pname] = json.dumps(v)
            set_pairs.append((k, pname))

    incr_pairs = []
    if incr:
        for k, delta in incr.items():
            if delta == 0:
                continue
            pname = f"incr_{k}"
            incr_pairs.append((k, pname))
            params[pname] = int(delta)

    max_pairs = []
    if max_keys:
        for k, cand in max_keys.items():
            pname = f"max_{k}"
            max_pairs.append((k, pname))
            params[pname] = int(cand)

    nested_sql_sections = []
    if nested:
        # Each nested parent merges with its jsonb object
        for parent, mapping in nested.items():
            if not mapping:
                continue
            # build jsonb_build_object for child mapping (cast each value to jsonb)
            child_elems = []
            for ck, cv in mapping.items():
                p2 = f"nested_{parent}_{ck}"
                params[p2] = json.dumps(cv)
                child_elems.append(f"'{ck}', (:{p2})::jsonb")
            child_obj = f"jsonb_build_object({', '.join(child_elems)})"
            nested_sql_sections.append(
                f"'{parent}', (COALESCE(meta->'{parent}', '{{}}'::jsonb) || {child_obj})"
            )

    # Compose jsonb_build_object for set/incr/max/nested
    # For incr & max we compute expressions referencing existing meta
    expr_kv = []
    for k, pname in set_pairs:
        expr_kv.append(f"'{k}', (:{pname})::jsonb")
    for k, pname in incr_pairs:
        expr_kv.append(f"'{k}', COALESCE((meta->>'{k}')::int,0) + (:{pname})::int")
    for k, pname in max_pairs:
        expr_kv.append(
            f"'{k}', GREATEST(COALESCE((meta->>'{k}')::int,0), (:{pname})::int)"
        )
    expr_kv.extend(nested_sql_sections)

    if not expr_kv and not remove_clause:
        return  # nothing effective

    build_obj = (
        f"jsonb_build_object({', '.join(expr_kv)})" if expr_kv else "'{}'::jsonb"
    )

    sql = f"""
    UPDATE import_jobs
    SET meta = (
        SELECT (COALESCE(meta,'{{}}'::jsonb){remove_clause}) || {build_obj}
        FROM import_jobs WHERE id=:job_id
    )
    WHERE id=:job_id
    """

    try:
        async with SessionLocal() as s:
            await s.execute(_text(sql), params)
            await s.commit()
    except Exception as e:  # pragma: no cover
        backend_logger.error(f"merge_job_meta failed job_id={job_id}: {e}")
