import re
from typing import Dict, Set, Optional, Callable, Awaitable
import os
import glob
import ijson
from app.logging_config import backend_logger
from app.services.entities.custom_emoji import CustomEmoji
from app.models.base import SessionLocal
from app.models.entity import Entity
from sqlalchemy import select


EMOJI_PATTERN = re.compile(r":([a-z0-9_+\-]+):")


def _resolve_emoji_url(
    name: str,
    emoji_list: Dict[str, str],
    depth: int = 0,
    visited: Optional[Set[str]] = None,
) -> Optional[str]:
    """Resolve Slack emoji URL, handling alias:foo chains safely."""
    if not name or name not in emoji_list:
        return None
    if visited is None:
        visited = set()
    if name in visited or depth > 10:
        return None
    visited.add(name)
    val = emoji_list.get(name)
    if not val:
        return None
    if val.startswith("alias:"):
        target = val.split(":", 1)[1]
        return _resolve_emoji_url(target, emoji_list, depth + 1, visited)
    return val


def _collect_emoji_from_blocks(blocks: list) -> Set[str]:
    names: Set[str] = set()
    for b in blocks or []:
        btype = b.get("type")
        if btype == "rich_text":
            for el in b.get("elements", []) or []:
                names |= _collect_from_rich_element(el)
        elif btype in ("section", "context"):
            # Text objects may be mrkdwn with :shortcodes:
            txt_obj = b.get("text")
            if isinstance(txt_obj, dict):
                ttext = txt_obj.get("text") or ""
                names |= set(EMOJI_PATTERN.findall(ttext))
            # fields/elements arrays
            for f in b.get("fields", []) or []:
                if isinstance(f, dict):
                    names |= set(EMOJI_PATTERN.findall(f.get("text") or ""))
            for el in b.get("elements", []) or []:
                if isinstance(el, dict) and el.get("type") in ("mrkdwn", "plain_text"):
                    names |= set(EMOJI_PATTERN.findall(el.get("text") or ""))
        elif btype == "header":
            txt_obj = b.get("text")
            if isinstance(txt_obj, dict):
                names |= set(EMOJI_PATTERN.findall(txt_obj.get("text") or ""))
    return names


def _collect_from_rich_element(el: dict) -> Set[str]:
    names: Set[str] = set()
    t = el.get("type")
    if t == "emoji":
        n = el.get("name")
        if n:
            names.add(n)
    # Nested elements
    for child in el.get("elements", []) or []:
        if isinstance(child, dict):
            names |= _collect_from_rich_element(child)
    # Text content may contain :shortcodes:
    if t in ("text", "mrkdwn", "plain_text"):
        names |= set(EMOJI_PATTERN.findall(el.get("text") or ""))
    return names


async def parse_custom_emojis_from_messages(message_entities, emoji_list=None):
    """Scan message bodies (text, blocks, attachments) for :shortcode: usages, and
    create custom_emoji entities for those found in Slack emoji list.
    """
    if not emoji_list:
        backend_logger.info(
            "Список эмодзи Slack пуст или не задан; пропускаю создание custom_emoji из сообщений"
        )
        return []

    wanted: Set[str] = set()

    for msg in message_entities:
        raw = msg.raw_data or {}
        # From plain text
        wanted |= set(EMOJI_PATTERN.findall(raw.get("text") or ""))
        # From blocks
        wanted |= _collect_emoji_from_blocks(raw.get("blocks") or [])
        # From classic attachments
        for a in raw.get("attachments", []) or []:
            for key in ("pretext", "title", "text", "fallback"):
                val = a.get(key)
                if isinstance(val, str):
                    wanted |= set(EMOJI_PATTERN.findall(val))

    # Resolve URLs (handle aliases) and filter only those with valid URLs
    resolved = {name: _resolve_emoji_url(name, emoji_list) for name in wanted}
    resolved = {k: v for k, v in resolved.items() if v}

    if not resolved:
        backend_logger.info(
            "В сообщениях не найдено кастомных эмодзи со ссылками в Slack API"
        )
        return []

    # Exclude already present custom_emoji entities
    existing: Set[str] = set()
    async with SessionLocal() as session:
        q = await session.execute(
            select(Entity.slack_id).where(Entity.entity_type == "custom_emoji")
        )
        existing = {row[0] for row in q.all()}

    to_create = [name for name in resolved.keys() if name not in existing]
    if not to_create:
        backend_logger.info("Все найденные кастомные эмодзи уже существуют в БД")
        return []

    created = []
    for name in to_create:
        emoji_entity = CustomEmoji(
            slack_id=name,
            raw_data={"name": name, "url": resolved[name]},
            status="pending",
            auto_save=False,
        )
        ent = await emoji_entity.save_to_db()
        if ent is not None:
            created.append(emoji_entity)

    backend_logger.info(f"Импортировано кастомных эмодзи из сообщений: {len(created)}")
    return created


async def parse_custom_emojis_from_export(
    export_dir: str,
    folder_channel_map: Dict[str, dict],
    emoji_list: Optional[Dict[str, str]] = None,
    progress: Optional[Callable[[int], Awaitable[None]]] = None,
) -> int:
    """Stream files in export to collect custom emoji usages and create entities.
    Returns number of created emojis.
    """
    if not emoji_list:
        backend_logger.info(
            "Список эмодзи Slack пуст или не задан; пропускаю создание custom_emoji из экспорта"
        )
        return 0

    wanted: Set[str] = set()
    for folder, _ in folder_channel_map.items():
        folder_path = os.path.join(export_dir, folder)
        if not os.path.isdir(folder_path):
            continue
        for msg_file in glob.glob(os.path.join(folder_path, "*.json")):
            try:
                with open(msg_file, "r", encoding="utf-8") as f:
                    for msg in ijson.items(f, "item"):
                        # From plain text
                        wanted |= set(
                            EMOJI_PATTERN.findall((msg or {}).get("text") or "")
                        )
                        # From blocks
                        wanted |= _collect_emoji_from_blocks(
                            (msg or {}).get("blocks") or []
                        )
                        # From classic attachments
                        for a in (msg or {}).get("attachments", []) or []:
                            for key in ("pretext", "title", "text", "fallback"):
                                val = a.get(key)
                                if isinstance(val, str):
                                    wanted |= set(EMOJI_PATTERN.findall(val))
            except Exception as e:
                backend_logger.error(
                    f"Ошибка чтения {msg_file} при сборе custom emojis: {e}"
                )
                continue

    resolved = {name: _resolve_emoji_url(name, emoji_list) for name in wanted}
    resolved = {k: v for k, v in resolved.items() if v}
    if not resolved:
        backend_logger.info(
            "В экспорте не найдено кастомных эмодзи со ссылками в Slack API"
        )
        return 0

    # Exclude already present
    existing: Set[str] = set()
    async with SessionLocal() as session:
        q = await session.execute(
            select(Entity.slack_id).where(Entity.entity_type == "custom_emoji")
        )
        existing = {row[0] for row in q.all()}

    to_create = [name for name in resolved.keys() if name not in existing]
    created = 0
    for name in to_create:
        emoji_entity = CustomEmoji(
            slack_id=name,
            raw_data={"name": name, "url": resolved[name]},
            status="pending",
            auto_save=False,
        )
        ent = await emoji_entity.save_to_db()
        if ent is not None:
            created += 1
            if progress:
                await progress(1)
    backend_logger.info(f"Импортировано кастомных эмодзи из экспорта: {created}")
    return created
