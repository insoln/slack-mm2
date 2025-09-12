import os
from app.services.entities.reaction import Reaction
import glob
import ijson
import time
from typing import Callable, Awaitable, Optional, List, Tuple
from app.services.entities.custom_emoji import CustomEmoji
from app.logging_config import backend_logger
from app.models.base import SessionLocal
from sqlalchemy import text as _text


def _extract_reactions_from_message(message):
    """Извлечь все реакции из одного сообщения"""
    reactions = []
    raw = message.raw_data or {}
    ts = raw.get("ts")

    for reaction in raw.get("reactions", []):
        name = reaction.get("name")
        if not name:
            continue

        user_ids = reaction.get("users") or []
        for user_id in user_ids:
            reaction_data = dict(reaction)
            reaction_data["user"] = user_id
            # Add convenience fields for later dedupe/merging
            reaction_data["message_ts"] = ts
            reaction_data["emoji_name"] = name
            reaction_data["composite_id"] = f"{ts}_{name}"

            reaction_entity = Reaction(
                slack_id=f"{ts}_{name}_{user_id}",
                mattermost_id=None,
                raw_data=reaction_data,
                status="pending",
                auto_save=False,
                job_id=getattr(message, "job_id", None),
            )
            reactions.append((reaction_entity, name))

    return reactions


def _create_custom_emoji_entities(custom_emoji_names, emoji_list):
    """Создать сущности CustomEmoji для кастомных эмодзи"""
    entities = []

    for name in custom_emoji_names:
        emoji_data = {"name": name}
        if emoji_list and name in emoji_list:
            emoji_data["url"] = emoji_list[name]
            backend_logger.debug(f"Добавлен URL для эмодзи {name}: {emoji_list[name]}")
        else:
            backend_logger.debug(f"URL для эмодзи {name} не найден в Slack API")

        emoji_entity = CustomEmoji(
            slack_id=name,
            raw_data=emoji_data,
            status="pending",
            auto_save=False,
        )
        entities.append(emoji_entity)

    return entities


async def parse_reactions_from_messages(message_entities, emoji_list=None):
    """Парсинг реакций из сообщений и создание кастомных эмодзи"""

    # 1. Извлекаем все реакции из сообщений
    all_reactions = []
    custom_emoji_names = set()

    for message in message_entities:
        message_reactions = _extract_reactions_from_message(message)
        all_reactions.extend(message_reactions)

        # Собираем имена кастомных эмодзи (только те, что есть в emoji_list с валидным URL)
        for _, emoji_name in message_reactions:
            if (
                emoji_list and emoji_name in emoji_list and emoji_list[emoji_name]
            ):  # Проверяем, что URL не пустой
                custom_emoji_names.add(emoji_name)
                backend_logger.debug(f"Добавлен кастомный эмодзи: {emoji_name}")
            elif emoji_list and emoji_name in emoji_list and not emoji_list[emoji_name]:
                backend_logger.debug(f"Пропущен эмодзи без URL: {emoji_name}")

    # 2. Сохраняем реакции в БД
    saved_reactions = []
    for reaction_entity, _ in all_reactions:
        entity = await reaction_entity.save_to_db()
        if entity is not None:
            await reaction_entity.create_reacted_by_relation()
            await reaction_entity.create_reacted_to_relation()
            saved_reactions.append(reaction_entity)

    backend_logger.info(f"Импортировано реакций: {len(saved_reactions)}")

    # 3. Создаем и сохраняем кастомные эмодзи (только с валидными URL)
    if custom_emoji_names:
        custom_emoji_entities = _create_custom_emoji_entities(
            custom_emoji_names, emoji_list
        )

        for emoji_entity in custom_emoji_entities:
            await emoji_entity.save_to_db()

        backend_logger.info(
            f"Импортировано кастомных эмодзи: {len(custom_emoji_entities)}"
        )

        # 4. Создаем связи между реакциями и кастомными эмодзи
        for reaction_entity, emoji_name in all_reactions:
            if emoji_name in custom_emoji_names:
                await reaction_entity.create_custom_emoji_relation(emoji_name)


async def parse_reactions_from_export(
    export_dir: str,
    folder_channel_map: dict,
    emoji_list=None,
    progress: Optional[Callable[[int], Awaitable[None]]] = None,
    job_id=None,
) -> int:
    """Stream files and import reactions.

    Optimizations added:
      - Optional batching (env: REACTIONS_BATCH_SIZE, REACTIONS_BULK=1)
      - Throttled progress updates (env: REACTIONS_PROGRESS_FLUSH_INTERVAL_SEC)
      - Reduced per-reaction round trips (bulk INSERT + post-processing of relations)

    Falls back to legacy per-row behavior if batching env not enabled (to minimize risk).
    """
    try:
        batch_size = int(os.environ.get("REACTIONS_BATCH_SIZE", "0") or 0)
    except Exception:
        batch_size = 0
    bulk_enabled = os.environ.get("REACTIONS_BULK", "0") in ("1", "true", "TRUE") and batch_size > 0
    try:
        progress_interval = float(os.environ.get("REACTIONS_PROGRESS_FLUSH_INTERVAL_SEC", "2"))
    except Exception:
        progress_interval = 2.0

    # inserted_count == reactions actually persisted (legacy reactions_processed)
    inserted_count = 0
    # parsed_count == reactions encountered while scanning JSON (may run ahead of inserts in bulk mode)
    parsed_count = 0
    custom_emoji_names = set()
    last_progress_emit = time.time()

    # Accumulators when bulk mode is ON
    batch_rows: List[Tuple[str,str,dict]] = []  # (slack_id, emoji_name, raw_data)

    async def _emit_meta_progress(force: bool = False):
        """Persist parsed/processed counters into import_jobs.meta (throttled).

        We keep backwards compatibility: `reactions_processed` continues to reflect inserted_count.
        New field: `reactions_parsed`.
        """
        nonlocal last_progress_emit
        if job_id is None:
            return
        now = time.time()
        if not force and (now - last_progress_emit) < progress_interval:
            return
        from sqlalchemy import text as _t
        try:
            async with SessionLocal() as s:
                await s.execute(
                    _t(
                        """
                        UPDATE import_jobs
                        SET meta = COALESCE(meta, '{}'::jsonb)
                            || jsonb_build_object(
                                'reactions_parsed', :parsed,
                                'reactions_processed', :inserted
                            )
                        WHERE id = :job_id
                        """
                    ),
                    {"parsed": int(parsed_count), "inserted": int(inserted_count), "job_id": job_id},
                )
                await s.commit()
            last_progress_emit = now
        except Exception:
            pass

    async def _flush_batch(force: bool = False):
        nonlocal batch_rows, inserted_count, last_progress_emit
        if not bulk_enabled:
            return
        if not force and len(batch_rows) < batch_size:
            return
        if not batch_rows:
            return
        # Prepare single bulk INSERT using VALUES list + ON CONFLICT DO NOTHING
        values_sql_parts = []
        params = {}
        for idx, (slack_id, _emoji, raw) in enumerate(batch_rows):
            values_sql_parts.append(
                f"(:etype{idx}, :sid{idx}, :mmid{idx}, :raw{idx}::jsonb, :job{idx}, :status{idx}, :err{idx})"
            )
            params[f"etype{idx}"] = "reaction"
            params[f"sid{idx}"] = slack_id
            params[f"mmid{idx}"] = None
            params[f"raw{idx}"] = raw
            params[f"job{idx}"] = job_id
            params[f"status{idx}"] = "pending"
            params[f"err{idx}"] = None
        sql = f"""
            INSERT INTO entities (entity_type, slack_id, mattermost_id, raw_data, job_id, status, error_message)
            VALUES {', '.join(values_sql_parts)}
            ON CONFLICT (entity_type, slack_id, job_id) DO NOTHING
        """
        try:
            async with SessionLocal() as session:
                await session.execute(_text(sql), params)
                await session.commit()
        except Exception as e:
            backend_logger.error(f"Bulk insert reactions failed, fallback row mode for this batch: {e}")
            # Fallback: row-by-row legacy path
            for slack_id, _emoji, raw in batch_rows:
                try:
                    reaction_entity = Reaction(
                        slack_id=slack_id,
                        mattermost_id=None,
                        raw_data=raw,
                        status="pending",
                        auto_save=False,
                        job_id=job_id,
                    )
                    ent = await reaction_entity.save_to_db()
                    if ent is not None:
                        await reaction_entity.create_reacted_by_relation()
                        await reaction_entity.create_reacted_to_relation()
                except Exception as ie:
                    backend_logger.error(f"Row fallback failed for reaction {slack_id}: {ie}")
        # Progress accounting (batch size) — optimistic, relations created lazily in fallback only.
        inc = len(batch_rows)
        inserted_count += inc
        batch_rows.clear()
        # Throttled progress callback + meta emit
        if progress:
            try:
                await progress(inc)
            except Exception:
                pass
        await _emit_meta_progress()

    for folder, _ in folder_channel_map.items():
        folder_path = os.path.join(export_dir, folder)
        if not os.path.isdir(folder_path):
            continue
        for msg_file in glob.glob(os.path.join(folder_path, "*.json")):
            try:
                with open(msg_file, "r", encoding="utf-8") as f:
                    for msg in ijson.items(f, "item"):
                        raw_msg = msg or {}
                        ts = raw_msg.get("ts")
                        reactions_list = raw_msg.get("reactions", []) or []
                        if not reactions_list:
                            continue
                        for reaction in reactions_list:
                            name = reaction.get("name")
                            if not name:
                                continue
                            users = reaction.get("users") or []
                            if not users:
                                continue
                            for user_id in users:
                                reaction_data = dict(reaction)
                                reaction_data["user"] = user_id
                                reaction_data["message_ts"] = ts
                                reaction_data["emoji_name"] = name
                                reaction_data["composite_id"] = f"{ts}_{name}"
                                slack_id = f"{ts}_{name}_{user_id}"
                                # Count parsed immediately (for all modes)
                                parsed_count += 1
                                if bulk_enabled:
                                    batch_rows.append((slack_id, name, reaction_data))
                                else:
                                    # Legacy immediate path
                                    reaction_entity = Reaction(
                                        slack_id=slack_id,
                                        mattermost_id=None,
                                        raw_data=reaction_data,
                                        status="pending",
                                        auto_save=False,
                                        job_id=job_id,
                                    )
                                    ent = await reaction_entity.save_to_db()
                                    if ent is not None:
                                        await reaction_entity.create_reacted_by_relation()
                                        await reaction_entity.create_reacted_to_relation()
                                        inserted_count += 1
                                        if progress:
                                            await progress(1)
                                        # Emit meta occasionally (throttled)
                                        await _emit_meta_progress()
                                if emoji_list and name in emoji_list and emoji_list[name]:
                                    custom_emoji_names.add(name)
                        if bulk_enabled:
                            # Flush if batch full or time exceeded
                            if len(batch_rows) >= batch_size or (time.time() - last_progress_emit >= progress_interval):
                                await _flush_batch()
                        else:
                            # In legacy mode, opportunistically emit meta progress
                            await _emit_meta_progress()
            except Exception as e:
                backend_logger.error(f"Ошибка чтения {msg_file} при сборе реакций: {e}")
                continue
    # Final flush
    await _flush_batch(force=True)
    # Final meta persistence showing any remaining parsed vs inserted delta
    await _emit_meta_progress(force=True)

    # Create custom emojis seen in reactions
    for name in sorted(custom_emoji_names):
        try:
            emoji_entity = CustomEmoji(
                slack_id=name,
                raw_data={
                    "name": name,
                    "url": emoji_list.get(name) if emoji_list else None,
                },
                status="pending",
                auto_save=False,
            )
            await emoji_entity.save_to_db()
        except Exception as e:
            backend_logger.error(f"Не удалось сохранить кастомный эмодзи {name}: {e}")

    backend_logger.info(
        f"Импорт реакций завершён: parsed={parsed_count}, inserted={inserted_count} (bulk={'on' if bulk_enabled else 'off'})"
    )
    return inserted_count
