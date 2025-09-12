import os
import glob
import ijson
from app.services.entities.message import Message
from app.services.entities.reaction import Reaction
from app.services.entities.attachment import Attachment
from app.services.entities.custom_emoji import CustomEmoji
import re
from app.logging_config import backend_logger
from typing import Awaitable, Callable, Optional


async def parse_channel_messages(
    export_dir,
    folder_channel_map,
    batch_size: int = 1000,
    progress: Optional[Callable[[int], Awaitable[None]]] = None,
    file_progress: Optional[Callable[[int], Awaitable[None]]] = None,
    job_id=None,
    single_pass: bool | None = None,
    counters_callback: Optional[Callable[[dict], Awaitable[None]]] = None,
    emoji_list: Optional[dict] = None,
):
    """Stream-parse messages by JSON file and persist incrementally.
    If single_pass is True, also persist reactions & attachments and collect custom emojis in one pass.
    counters_callback receives incremental counter deltas: {messages, reactions, attachments, emojis}.
    Returns dict of final counters.
    """
    if single_pass is None:
        import os as _os
        single_pass = _os.environ.get("IMPORT_SINGLE_PASS", "0") in ("1", "true", "TRUE")

    EMOJI_PATTERN = re.compile(r":([a-z0-9_+\-]+):")
    saved_count = 0
    reactions_count = 0
    attachments_count = 0
    emojis_seen: set[str] = set()
    for folder, channel in folder_channel_map.items():
        backend_logger.debug(
            f"Обработка папки: {folder}, канал: {(channel.get('name') if channel else None) or (channel.get('slack_id') if channel else None)}"
        )
        if not channel:
            backend_logger.debug(f"Пропуск папки {folder}: канал не найден")
            continue
        channel_id = channel["id"]
        folder_path = os.path.join(export_dir, folder)
        if not os.path.isdir(folder_path):
            backend_logger.debug(f"Пропуск: {folder_path} не является директорией")
            continue
        for msg_file in glob.glob(os.path.join(folder_path, "*.json")):
            backend_logger.debug(f"Чтение файла сообщений: {msg_file}")
            try:
                with open(msg_file, "r", encoding="utf-8") as f:
                    # Slack daily files are JSON arrays; stream items
                    for msg in ijson.items(f, "item"):
                        try:
                            slack_id = msg.get("ts")
                            if not slack_id:
                                continue
                            message_entity = Message(
                                slack_id=slack_id,
                                mattermost_id=None,
                                raw_data=msg,
                                status="pending",
                                auto_save=False,
                                job_id=job_id,
                            )
                            # Save and link immediately to avoid memory growth
                            await message_entity.save_to_db(channel_id)
                            if getattr(message_entity, "id", None) is not None:
                                await message_entity.create_posted_in_relation(
                                    channel_id
                                )
                                await message_entity.create_posted_by_relation()
                                await message_entity.create_thread_relation()
                            saved_count += 1

                            if single_pass:
                                # Reactions
                                for reaction in (msg or {}).get("reactions") or []:
                                    rname = reaction.get("name")
                                    if not rname:
                                        continue
                                    for user_id in reaction.get("users") or []:
                                        reaction_data = dict(reaction)
                                        reaction_data["user"] = user_id
                                        reaction_data["message_ts"] = slack_id
                                        reaction_data["emoji_name"] = rname
                                        reaction_data["composite_id"] = f"{slack_id}_{rname}"
                                        reaction_entity = Reaction(
                                            slack_id=f"{slack_id}_{rname}_{user_id}",
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
                                            reactions_count += 1
                                # Attachments (files array)
                                for file_obj in (msg or {}).get("files") or []:
                                    slack_file_id = file_obj.get("id")
                                    url_private = file_obj.get("url_private")
                                    if not slack_file_id or not (
                                        url_private and url_private.startswith("https://files.slack.com")
                                    ):
                                        continue
                                    attachment = Attachment(
                                        slack_id=slack_file_id,
                                        mattermost_id=None,
                                        raw_data=file_obj,
                                        status="pending",
                                        auto_save=False,
                                        job_id=job_id,
                                    )
                                    ent_a = await attachment.save_to_db()
                                    if ent_a is not None:
                                        await attachment.create_attached_to_relation(slack_id)
                                        attachments_count += 1
                                # Emojis (collect names; creation deferred until after pass)
                                if emoji_list:
                                    text = (msg or {}).get("text") or ""
                                    for name in EMOJI_PATTERN.findall(text):
                                        if emoji_list.get(name):
                                            emojis_seen.add(name)
                                    for a in (msg or {}).get("attachments") or []:
                                        for key in ("pretext", "title", "text", "fallback"):
                                            val = a.get(key)
                                            if isinstance(val, str):
                                                for name in EMOJI_PATTERN.findall(val):
                                                    if emoji_list.get(name):
                                                        emojis_seen.add(name)
                                    for b in (msg or {}).get("blocks") or []:
                                        if isinstance(b, dict):
                                            if b.get("type") == "rich_text":
                                                for el in b.get("elements", []) or []:
                                                    if isinstance(el, dict):
                                                        if el.get("type") in ("text", "mrkdwn", "plain_text"):
                                                            t = el.get("text") or ""
                                                            for name in EMOJI_PATTERN.findall(t):
                                                                if emoji_list.get(name):
                                                                    emojis_seen.add(name)
                                            else:
                                                t = (
                                                    (b.get("text") or {}).get("text")
                                                    if isinstance(b.get("text"), dict)
                                                    else None
                                                )
                                                if t:
                                                    for name in EMOJI_PATTERN.findall(t):
                                                        if emoji_list.get(name):
                                                            emojis_seen.add(name)

                            if saved_count % batch_size == 0:
                                backend_logger.debug(f"Сохранено сообщений: {saved_count}…")
                                if progress:
                                    await progress(batch_size)
                                if single_pass and counters_callback:
                                    try:
                                        await counters_callback(
                                            {
                                                "messages": batch_size,
                                                "reactions": reactions_count,
                                                "attachments": attachments_count,
                                                # emojis are de-duplicated; report full so far (idempotent)
                                                "emojis": len(emojis_seen),
                                            }
                                        )
                                    except Exception:
                                        pass
                        except Exception as e:
                            backend_logger.error(
                                f"Ошибка при сохранении сообщения из {msg_file}: {e}"
                            )
                # file processed successfully
                if file_progress:
                    try:
                        await file_progress(1)
                    except Exception:
                        pass
            except Exception as e:
                backend_logger.error(f"Ошибка чтения {msg_file}: {e}")
                continue
    backend_logger.info(f"Импортировано сообщений: {saved_count}")
    # Flush remaining deltas
    remaining = saved_count % batch_size
    if progress and remaining:
        await progress(remaining)
    if single_pass and counters_callback:
        try:
            await counters_callback(
                {
                    "messages": remaining if remaining else 0,
                    "reactions": reactions_count,
                    "attachments": attachments_count,
                    "emojis": len(emojis_seen),
                }
            )
        except Exception:
            pass

    # Persist custom emojis if single-pass
    created_emojis = 0
    if single_pass and emojis_seen:
        for name in sorted(emojis_seen):
            emoji_entity = CustomEmoji(
                slack_id=name,
                raw_data={"name": name, "url": emoji_list.get(name) if emoji_list else None},
                status="pending",
                auto_save=False,
            )
            ent_e = await emoji_entity.save_to_db()
            if ent_e is not None:
                created_emojis += 1

    return {
        "messages": saved_count,
        "reactions": reactions_count,
        "attachments": attachments_count,
        "emojis": created_emojis if created_emojis else len(emojis_seen),
    }
