import pytest
from app.services.entities.message import Message
from app.models.base import SessionLocal, engine
from sqlalchemy import select
from app.models.entity import Entity


@pytest.mark.asyncio
async def test_global_uniqueness_reuses_entity_across_jobs():
    # Ensure tables are created for isolated in-memory SQLite environment
    # Create only the entities table to avoid JSONB compilation issues creating all metadata under SQLite.
    async with engine.begin() as conn:
        await conn.run_sync(Entity.metadata.tables["entities"].create, checkfirst=True)

    # Create first message with job A
    m1 = Message(
        slack_id="123.456", raw_data={"ts": "123.456"}, auto_save=False, job_id=1
    )
    ent1 = await m1.save_to_db()
    assert ent1 is not None
    assert hasattr(m1, "id")
    first_id = getattr(m1, "id")

    # Create logically identical message with different job id B
    m2 = Message(
        slack_id="123.456", raw_data={"ts": "123.456"}, auto_save=False, job_id=2
    )
    ent2 = await m2.save_to_db()
    assert ent2 is not None
    # Should reuse same underlying entity id (m2.id set to existing id)
    assert getattr(m2, "id") == first_id

    # DB should contain only one row for this slack_id / entity_type
    async with SessionLocal() as session:
        rows = await session.execute(
            select(Entity).where(
                (Entity.entity_type == "message") & (Entity.slack_id == "123.456")
            )
        )
        ents = rows.scalars().all()
        assert len(ents) == 1
