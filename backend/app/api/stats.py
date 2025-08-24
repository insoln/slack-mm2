from fastapi import APIRouter
from sqlalchemy import select, func
from app.models.base import SessionLocal
from app.models.entity import Entity
from app.models.status_enum import MappingStatus

router = APIRouter()


@router.get("/stats/mappings")
async def get_mapping_stats():
    """Return counts of mappings grouped by entity_type and status, plus totals and a matrix for table rendering."""
    async with SessionLocal() as session:
        # total entities
        total_q = await session.execute(select(func.count()).select_from(Entity))
        total = total_q.scalar_one()

        # by entity_type
        by_type_q = await session.execute(
            select(Entity.entity_type, func.count()).group_by(Entity.entity_type)
        )
        by_type_rows = by_type_q.all()
        by_type = {etype: cnt for etype, cnt in by_type_rows}

        # by status
        by_status_q = await session.execute(
            select(Entity.status, func.count()).group_by(Entity.status)
        )
        by_status_rows = by_status_q.all()
        by_status = {str(status.value if hasattr(status, "value") else str(status)): cnt for status, cnt in by_status_rows}

        # matrix: (type, status) -> count
        matrix_q = await session.execute(
            select(Entity.entity_type, Entity.status, func.count())
            .group_by(Entity.entity_type, Entity.status)
        )
        matrix_rows = matrix_q.all()
        # Collect all types and build a nested dict with zero-filled statuses
        all_types = sorted({row[0] for row in matrix_rows} | set(by_type.keys()))
        statuses_order = [s.value for s in (
            MappingStatus.pending,
            MappingStatus.skipped,
            MappingStatus.failed,
            MappingStatus.success,
        )]
        matrix: dict[str, dict[str, int]] = {t: {st: 0 for st in statuses_order} for t in all_types}
        for etype, status, cnt in matrix_rows:
            st = status.value if hasattr(status, "value") else str(status)
            matrix[etype][st] = cnt

        totals_row = {st: 0 for st in statuses_order}
        for t in all_types:
            for st in statuses_order:
                totals_row[st] += matrix[t][st]

        return {
            "total": total,
            "by_type": by_type,
            "by_status": by_status,
            "statuses": statuses_order,
            "types": all_types,
            "matrix": matrix,
            "totals_row": totals_row,
        }
