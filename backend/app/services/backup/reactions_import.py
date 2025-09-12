import os
from app.services.entities.reaction import Reaction
import os
import glob
import ijson
# Streaming parser for large files; see messages_import for overall rationale.
try:
    import orjson  # type: ignore  # optional small-file fast path
except Exception:  # pragma: no cover
    orjson = None  # type: ignore
from typing import Callable, Awaitable, Optional, List
from app.services.entities.custom_emoji import CustomEmoji
from app.logging_config import backend_logger
from app.models.base import SessionLocal
from app.models.entity import Entity
from sqlalchemy import select


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
    batch_size: int | None = None,
) -> int:
    """Stream files in export and create reactions incrementally with optional batching.
    Batching reduces commit round-trips. Relations are created post-commit per batch.
    """
    # Determine batch size (reuse messages batch env var if present)
    if batch_size in (None, 0):
        try:
            env_batch = int(os.environ.get("IMPORT_BATCH_SIZE", "0") or 0)
            batch_size = env_batch if env_batch > 0 else 0
        except Exception:
            batch_size = 0
    batch_mode = batch_size and batch_size > 1
    total = 0
    custom_emoji_names = set()
    batch_reactions: List[Reaction] = []

    async def flush_reactions(force: bool = False):
        nonlocal batch_reactions, total
        if not batch_mode:
            return
        if not force and len(batch_reactions) < batch_size:
            return
        if not batch_reactions:
            return
        # Bulk insert with ON CONFLICT; fallback to per-row if bulk fails
        bulk_ok = True
        async with SessionLocal() as session:
            try:
                # Prepare rows
                values_sql_parts = []
                params = {}
                for idx, r in enumerate(batch_reactions):
                    rd = getattr(r, 'raw_data', None)
                    if isinstance(rd, dict) and 'ts' not in rd and getattr(r, 'slack_id', None):
                        try:
                            rd['ts'] = str(getattr(r, 'slack_id')).split('_')[0]
                        except Exception:
                            pass
                    values_sql_parts.append(
                        f"(:entity_type{idx}, :slack_id{idx}, :mattermost_id{idx}, :raw_data{idx}::jsonb, :job_id{idx}, :status{idx}, :error_message{idx})"
                    )
                    params[f"entity_type{idx}"] = getattr(r, 'entity_type', 'reaction')
                    params[f"slack_id{idx}"] = getattr(r, 'slack_id', None)
                    params[f"mattermost_id{idx}"] = getattr(r, 'mattermost_id', None)
                    params[f"raw_data{idx}"] = rd
                    params[f"job_id{idx}"] = getattr(r, 'job_id', None)
                    params[f"status{idx}"] = getattr(r, 'status', 'pending')
                    params[f"error_message{idx}"] = None
                if values_sql_parts:
                    from sqlalchemy import text as _text
                    sql = f"""
                        INSERT INTO entities (entity_type, slack_id, mattermost_id, raw_data, job_id, status, error_message)
                        VALUES {', '.join(values_sql_parts)}
                        ON CONFLICT (entity_type, slack_id, job_id) DO NOTHING
                    """
                    await session.execute(_text(sql), params)
                await session.commit()
            except Exception as e:
                backend_logger.error(f"Bulk insert reactions failed, fallback to row mode: {e}")
                bulk_ok = False
        if not bulk_ok:
            # Row-by-row fallback preserving prior behavior
            for r in batch_reactions:
                try:
                    await r.save_to_db()
                except Exception as e:  # pragma: no cover
                    backend_logger.error(f"Fallback save reaction error {getattr(r,'slack_id',None)}: {e}")
        # Relations after persistence
        for r in batch_reactions:
            try:
                await r.create_reacted_by_relation()
                await r.create_reacted_to_relation()
            except Exception as e:
                backend_logger.error(f"Relation creation error for reaction {getattr(r,'slack_id',None)}: {e}")
        if progress:
            try:
                await progress(len(batch_reactions))
            except Exception:
                pass
        batch_reactions = []
    try:
        orjson_threshold_kb = int(os.environ.get("IMPORT_ORJSON_THRESHOLD_KB", "0") or 0)
    except Exception:
        orjson_threshold_kb = 0

    for folder, _ in folder_channel_map.items():
        folder_path = os.path.join(export_dir, folder)
        if not os.path.isdir(folder_path):
            continue
        for msg_file in glob.glob(os.path.join(folder_path, "*.json")):
            try:
                file_size = 0
                try:
                    file_size = os.path.getsize(msg_file)
                except Exception:
                    pass
                use_fast_path = (
                    orjson is not None and orjson_threshold_kb > 0 and file_size > 0 and file_size <= orjson_threshold_kb * 1024
                )
                if use_fast_path:
                    try:
                        with open(msg_file, "rb") as bf:
                            data = bf.read()
                        parsed = orjson.loads(data)  # type: ignore[union-attr]
                        if not isinstance(parsed, list):
                            use_fast_path = False
                        else:
                            iterator = iter(parsed)
                    except Exception:
                        use_fast_path = False
                if not use_fast_path:
                    f = open(msg_file, "r", encoding="utf-8")
                    iterator = ijson.items(f, "item")  # type: ignore
                try:
                    for msg in iterator:  # type: ignore
                        raw = msg or {}
                        ts = raw.get("ts")
                        for reaction in raw.get("reactions", []) or []:
                            name = reaction.get("name")
                            if not name:
                                continue
                            for user_id in reaction.get("users") or []:
                                reaction_data = dict(reaction)
                                reaction_data["user"] = user_id
                                reaction_data["message_ts"] = ts
                                reaction_data["emoji_name"] = name
                                reaction_data["composite_id"] = f"{ts}_{name}"
                                reaction_entity = Reaction(
                                    slack_id=f"{ts}_{name}_{user_id}",
                                    mattermost_id=None,
                                    raw_data=reaction_data,
                                    status="pending",
                                    auto_save=False,
                                    job_id=job_id,
                                )
                                if batch_mode:
                                    batch_reactions.append(reaction_entity)
                                    if len(batch_reactions) >= batch_size:
                                        await flush_reactions(force=True)
                                    total += 1
                                else:
                                    ent = await reaction_entity.save_to_db()
                                    if ent is not None:
                                        await reaction_entity.create_reacted_by_relation()
                                        await reaction_entity.create_reacted_to_relation()
                                        total += 1
                                        if progress:
                                            await progress(1)
                                if (
                                    emoji_list
                                    and name in emoji_list
                                    and emoji_list[name]
                                ):
                                    custom_emoji_names.add(name)
                finally:
                    try:
                        if not use_fast_path:
                            f.close()  # type: ignore
                    except Exception:
                        pass
            except Exception as e:
                backend_logger.error(f"Ошибка чтения {msg_file} при сборе реакций: {e}")
                continue
    # Final flush for any remaining batched reactions
    if batch_mode:
        await flush_reactions(force=True)

    # Create custom emojis seen in reactions
    for name in sorted(custom_emoji_names):
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
    backend_logger.info(f"Импортировано реакций из экспорта: {total}")
    return total
