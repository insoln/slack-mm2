"""
Migration: Mark incorrectly created bot users for re-export

This script identifies Slack bots (is_bot=true) that were created as regular
Mattermost users instead of Bot Accounts, and marks them for re-export.

Since Mattermost doesn't provide an API to convert a regular user to a bot,
the best approach is to:
1. Mark these entities as 'pending' status so they will be re-exported
2. On next export, they will be created as Bot Accounts using the bot API
3. The old user accounts can be manually deactivated or deleted in Mattermost

This is a data migration, not an Alembic schema migration.

Revision ID: 004_mark_bots_for_reexport
Revises: 003_add_cascade_deletion_indexes
Create Date: 2024-12-21

Usage:
    python backend/alembic/versions/004_mark_bots_for_reexport.py

"""

import asyncio
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.models.base import SessionLocal
from app.models.entity import Entity
from app.models.status_enum import MappingStatus
from sqlalchemy import select, update
from app.logging_config import backend_logger


async def identify_bot_entities():
    """
    Find all entities where:
    - entity_type = 'user'
    - raw_data contains is_bot=true
    - status = 'success' (already exported as regular users)
    """
    async with SessionLocal() as session:
        # Query for user entities with is_bot=true
        query = await session.execute(
            select(Entity).where(
                (Entity.entity_type == "user")
                & (Entity.raw_data["is_bot"].astext == "true")
            )
        )
        bot_entities = query.scalars().all()

        backend_logger.info(f"Found {len(bot_entities)} bot entities in database")

        # Count by status
        by_status = {}
        for entity in bot_entities:
            status = (
                entity.status.value
                if hasattr(entity.status, "value")
                else str(entity.status)
            )
            by_status[status] = by_status.get(status, 0) + 1

        backend_logger.info(f"Bot entities by status: {by_status}")

        return bot_entities


async def mark_successful_bots_for_reexport():
    """
    Mark bot entities that were successfully exported as regular users
    for re-export as Bot Accounts.
    """
    async with SessionLocal() as session:
        # Update bot entities with status='success' to status='pending'
        result = await session.execute(
            update(Entity)
            .where(
                (Entity.entity_type == "user")
                & (Entity.raw_data["is_bot"].astext == "true")
                & (Entity.status == MappingStatus.success)
            )
            .values(
                status=MappingStatus.pending,
                error_message="Marked for re-export as Bot Account (was incorrectly created as regular user)",
            )
        )

        await session.commit()

        updated_count = result.rowcount
        backend_logger.info(f"Marked {updated_count} bot entities for re-export")

        return updated_count


async def main():
    print("=" * 70)
    print("Migration: Mark incorrectly created bot users for re-export")
    print("=" * 70)

    # Step 1: Identify bot entities
    print("\n1. Identifying bot entities...")
    bot_entities = await identify_bot_entities()

    if not bot_entities:
        print("   No bot entities found. Migration not needed.")
        return

    # Show sample bots
    print("\n   Sample bot entities:")
    for entity in bot_entities[:5]:
        raw_data = entity.raw_data or {}
        username = raw_data.get("name", "unknown")
        status = (
            entity.status.value
            if hasattr(entity.status, "value")
            else str(entity.status)
        )
        mm_id = entity.mattermost_id or "None"
        print(
            f"     - {username} (slack_id={entity.slack_id}, mm_id={mm_id}, status={status})"
        )

    if len(bot_entities) > 5:
        print(f"     ... and {len(bot_entities) - 5} more")

    # Step 2: Mark for re-export
    print("\n2. Marking successfully exported bots for re-export...")
    updated_count = await mark_successful_bots_for_reexport()

    if updated_count > 0:
        print(f"   ✓ Marked {updated_count} bot entities for re-export")
        print("\n   Next steps:")
        print("   1. Run the export process to re-create these as Bot Accounts")
        print("   2. Manually deactivate/delete the old user accounts in Mattermost")
        print("   3. The mapping will be updated with the new bot user_id")
    else:
        print("   No bots needed to be marked (they may already be pending or failed)")

    print("\n" + "=" * 70)
    print("Migration completed!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
