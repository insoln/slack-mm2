import pytest
from sqlalchemy import select

from app.models.base import SessionLocal, engine
from app.models.entity import Entity
from app.models.entity_relation import EntityRelation
from app.models.status_enum import MappingStatus
from app.services.entities.attachment import Attachment
from app.services.entities.base_mixin import BaseMapping
from app.services.entities.message import Message


@pytest.fixture(autouse=True)
async def reset_entities_table():
    entities_table = Entity.metadata.tables["entities"]
    relations_table = EntityRelation.metadata.tables["entity_relations"]
    async with engine.begin() as conn:
        await conn.run_sync(entities_table.create, checkfirst=True)
        await conn.run_sync(relations_table.create, checkfirst=True)
        # Truncate tables instead of dropping to preserve schema/AI settings
        await conn.execute(relations_table.delete())
        await conn.execute(entities_table.delete())


@pytest.mark.asyncio
async def test_global_uniqueness_reuses_entity_across_jobs():
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


@pytest.mark.asyncio
async def test_failed_mapping_takeover_updates_status_and_job():
    original = Attachment(
        slack_id="F_takeover",
        raw_data={"url": "https://bad"},
        status="pending",
        auto_save=False,
        job_id=1,
    )
    existing = await original.save_to_db()
    assert existing is not None
    existing_id = getattr(original, "id")
    await original.set_status("failed", error="Plugin upload failed")

    takeover = Attachment(
        slack_id="F_takeover",
        raw_data={"url": "https://good"},
        status="pending",
        auto_save=False,
        job_id=2,
    )

    result = await takeover.save_to_db()
    assert result is not None
    assert getattr(takeover, "id") == existing_id

    async with SessionLocal() as session:
        refreshed = await session.get(Entity, existing_id)
        assert refreshed is not None
        assert refreshed.job_id == 2
        assert refreshed.status == MappingStatus.pending
        assert refreshed.error_message is None
        assert refreshed.raw_data == {"url": "https://good"}


@pytest.mark.asyncio
async def test_batch_takeover_requeues_failed_entities():
    base_failed = Attachment(
        slack_id="F_batch_failed",
        raw_data={"url": "bad1"},
        status="pending",
        auto_save=False,
        job_id=1,
    )
    base_skipped = Attachment(
        slack_id="F_batch_skipped",
        raw_data={"url": "bad2"},
        status="pending",
        auto_save=False,
        job_id=1,
    )
    await base_failed.save_to_db()
    await base_skipped.save_to_db()
    await base_failed.set_status("failed", error="bad link")
    await base_skipped.set_status("skipped", error="dependency missing")

    attachments = [
        Attachment(
            slack_id="F_batch_failed",
            raw_data={"url": "good1"},
            status="pending",
            auto_save=False,
            job_id=2,
        ),
        Attachment(
            slack_id="F_batch_skipped",
            raw_data={"url": "good2"},
            status="pending",
            auto_save=False,
            job_id=2,
        ),
        Attachment(
            slack_id="F_batch_new",
            raw_data={"url": "good3"},
            status="pending",
            auto_save=False,
            job_id=2,
        ),
    ]

    result = await BaseMapping.batch_save_to_db(attachments)
    assert result["saved"] == 3  # 2 takeovers + 1 fresh insert
    assert result["existing"] == 0

    async with SessionLocal() as session:
        rows = await session.execute(
            select(Entity)
            .where(Entity.entity_type == "attachment")
            .order_by(Entity.slack_id.asc())
        )
        entities = rows.scalars().all()

    assert len(entities) == 3
    by_id = {ent.slack_id: ent for ent in entities}

    assert by_id["F_batch_failed"].job_id == 2
    assert by_id["F_batch_failed"].status == MappingStatus.pending
    assert by_id["F_batch_failed"].error_message is None
    assert by_id["F_batch_failed"].raw_data == {"url": "good1"}

    assert by_id["F_batch_skipped"].job_id == 2
    assert by_id["F_batch_skipped"].status == MappingStatus.pending
    assert by_id["F_batch_skipped"].error_message is None
    assert by_id["F_batch_skipped"].raw_data == {"url": "good2"}

    assert by_id["F_batch_new"].job_id == 2
    assert by_id["F_batch_new"].status == MappingStatus.pending


@pytest.mark.asyncio
async def test_attachment_relation_falls_back_to_any_job():
    message = Message(
        slack_id="555.777",
        raw_data={"ts": "555.777"},
        auto_save=False,
        job_id=1,
    )
    await message.save_to_db()
    assert hasattr(message, "id")

    attachment = Attachment(
        slack_id="F_attach",
        raw_data={"files": []},
        status="pending",
        auto_save=False,
        job_id=2,
    )
    await attachment.save_to_db()
    assert hasattr(attachment, "id")

    await attachment.create_attached_to_relation(message.slack_id)

    async with SessionLocal() as session:
        rows = await session.execute(select(EntityRelation))
        relations = rows.scalars().all()

    assert len(relations) == 1
    relation = relations[0]
    assert relation.from_entity_id == getattr(attachment, "id")
    assert relation.to_entity_id == getattr(message, "id")
    assert relation.relation_type == "attached_to"
