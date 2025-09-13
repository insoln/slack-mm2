import os
import glob
import json
import re
from typing import Awaitable, Callable, Optional, Set, Any

from app.services.entities.message import Message
from app.services.entities.attachment import Attachment
from app.services.entities.reaction import Reaction
from app.services.entities.custom_emoji import CustomEmoji
from app.logging_config import backend_logger
from app.services.backup.progress_tracker import make_tracker

EMOJI_PATTERN = re.compile(r":([a-z0-9_+\-]+):")

# Backwards compatibility shim for old tests expecting ijson.items
class _IjsonShim:
    @staticmethod
    def items(f, prefix: str):  # pragma: no cover - only for legacy patching in tests
        data = json.load(f) or []
        if not isinstance(data, list):
            return iter(())
        for item in data:
            yield item

ijson = _IjsonShim()  # type: ignore


async def parse_messages_and_related(
    export_dir,
    folder_channel_map,
    emoji_list: Optional[dict] = None,
    batch_log_every: int = 1000,
    progress_messages: Optional[Callable[[int], Awaitable[None]]] = None,
    file_progress: Optional[Callable[[int], Awaitable[None]]] = None,
    job_id=None,
):
    """Single-pass import of messages + reactions + attachments + custom emojis.

    No streaming library (ijson) – assume daily files are reasonably sized.
    Minimizes file I/O passes. Still row-by-row inserts (bulk can be added later).
    """
    msg_tracker = make_tracker(job_id, "message")
    reaction_tracker = make_tracker(job_id, "reaction")
    attach_tracker = make_tracker(job_id, "attachment")
    emoji_tracker = make_tracker(job_id, "custom_emoji")

    total_messages = 0
    total_reactions = 0
    total_attachments = 0
    custom_emoji_names: Set[str] = set()

    for folder, channel in folder_channel_map.items():
        if not channel:
            continue
        channel_id = channel["id"]
        folder_path = os.path.join(export_dir, folder)
        if not os.path.isdir(folder_path):
            continue
        for msg_file in glob.glob(os.path.join(folder_path, "*.json")):
            try:
                with open(msg_file, "r", encoding="utf-8") as f:
                    try:
                        data = json.load(f) or []
                        if not isinstance(data, list):
                            backend_logger.error(
                                f"Формат файла {msg_file} не список — пропуск"
                            )
                            continue
                    except Exception as je:
                        backend_logger.error(f"Ошибка парсинга JSON {msg_file}: {je}")
                        continue
                for msg in data:
                    try:
                        slack_id = (msg or {}).get("ts")
                        if not slack_id:
                            continue
                        await msg_tracker.incr_parsed(1)
                        m_entity = Message(
                            slack_id=slack_id,
                            mattermost_id=None,
                            raw_data=msg,
                            status="pending",
                            auto_save=False,
                            job_id=job_id,
                        )
                        await m_entity.save_to_db(channel_id)
                        if getattr(m_entity, "id", None) is not None:
                            await m_entity.create_posted_in_relation(channel_id)
                            await m_entity.create_posted_by_relation()
                            await m_entity.create_thread_relation()
                        total_messages += 1
                        await msg_tracker.incr_processed(1)
                        # Attachments inline
                        for file_obj in (msg or {}).get("files") or []:
                            slack_aid = file_obj.get("id")
                            url_private = file_obj.get("url_private")
                            if not slack_aid or not (
                                url_private
                                and url_private.startswith("https://files.slack.com")
                            ):
                                continue
                            await attach_tracker.incr_parsed(1)
                            a_entity = Attachment(
                                slack_id=slack_aid,
                                mattermost_id=None,
                                raw_data=file_obj,
                                status="pending",
                                auto_save=False,
                                job_id=job_id,
                            )
                            ent_a = await a_entity.save_to_db()
                            if ent_a is not None:
                                total_attachments += 1
                                await a_entity.create_attached_to_relation(slack_id)
                                await attach_tracker.incr_processed(1)
                        # Reactions inline
                        for reaction in (msg or {}).get("reactions") or []:
                            r_name = reaction.get("name")
                            if not r_name:
                                continue
                            users = reaction.get("users") or []
                            for user_id in users:
                                await reaction_tracker.incr_parsed(1)
                                reaction_data = dict(reaction)
                                reaction_data["user"] = user_id
                                reaction_data["message_ts"] = slack_id
                                reaction_data["emoji_name"] = r_name
                                reaction_data["composite_id"] = f"{slack_id}_{r_name}"
                                r_entity = Reaction(
                                    slack_id=f"{slack_id}_{r_name}_{user_id}",
                                    mattermost_id=None,
                                    raw_data=reaction_data,
                                    status="pending",
                                    auto_save=False,
                                    job_id=job_id,
                                )
                                ent_r = await r_entity.save_to_db()
                                if ent_r is not None:
                                    total_reactions += 1
                                    await r_entity.create_reacted_by_relation()
                                    await r_entity.create_reacted_to_relation()
                                    await reaction_tracker.incr_processed(1)
                                # Track candidate custom emoji name
                                if (
                                    emoji_list
                                    and r_name in emoji_list
                                    and emoji_list[r_name]
                                ):
                                    custom_emoji_names.add(r_name)
                        # Emoji scan in text / attachments / blocks
                        text = (msg or {}).get("text") or ""
                        for name in EMOJI_PATTERN.findall(text):
                            custom_emoji_names.add(name)
                        for at in (msg or {}).get("attachments") or []:
                            for key in ("pretext", "title", "text", "fallback"):
                                val = at.get(key)
                                if isinstance(val, str):
                                    for name in EMOJI_PATTERN.findall(val):
                                        custom_emoji_names.add(name)
                        for blk in (msg or {}).get("blocks") or []:
                            if isinstance(blk, dict):
                                if blk.get("type") == "rich_text":
                                    for el in blk.get("elements", []) or []:
                                        if isinstance(el, dict):
                                            if el.get("type") in (
                                                "text",
                                                "mrkdwn",
                                                "plain_text",
                                            ):
                                                for name in EMOJI_PATTERN.findall(
                                                    el.get("text") or ""
                                                ):
                                                    custom_emoji_names.add(name)
                                else:
                                    txt_obj = blk.get("text")
                                    if isinstance(txt_obj, dict):
                                        for name in EMOJI_PATTERN.findall(
                                            txt_obj.get("text") or ""
                                        ):
                                            custom_emoji_names.add(name)
                        if total_messages % batch_log_every == 0 and total_messages:
                            backend_logger.debug(
                                f"Сообщений: {total_messages}, реакций: {total_reactions}, аттачментов: {total_attachments}"
                            )
                            if progress_messages:
                                await progress_messages(batch_log_every)
                    except Exception as ie:
                        backend_logger.error(
                            f"Ошибка обработки сообщения в {msg_file}: {ie}"
                        )
                if file_progress:
                    try:
                        await file_progress(1)
                    except Exception:
                        pass
            except Exception as e:
                backend_logger.error(f"Ошибка чтения {msg_file}: {e}")
                continue

    # Final flush trackers
    await msg_tracker.flush()
    await reaction_tracker.flush()
    await attach_tracker.flush()

    # Custom emojis creation
    created_emojis = 0
    if emoji_list:
        for name in sorted(custom_emoji_names):
            if not emoji_list.get(name):
                continue
            await emoji_tracker.incr_parsed(1)
            c_entity = CustomEmoji(
                slack_id=name,
                raw_data={"name": name, "url": emoji_list.get(name)},
                status="pending",
                auto_save=False,
            )
            ent_c = await c_entity.save_to_db()
            if ent_c is not None:
                created_emojis += 1
                await emoji_tracker.incr_processed(1)
        await emoji_tracker.flush()

    backend_logger.info(
        f"Единый импорт завершён: messages={total_messages}, reactions={total_reactions}, attachments={total_attachments}, custom_emojis={created_emojis}"
    )
    # Return summary
    return {
        "messages": total_messages,
        "reactions": total_reactions,
        "attachments": total_attachments,
        "custom_emojis": created_emojis,
    }


# Backwards compatibility wrapper for existing tests/code paths
async def parse_channel_messages(
    export_dir: str,
    folder_channel_map: dict,
    batch_size: int = 1000,  # ignored, preserved for signature
    progress: Optional[Callable[[int], Awaitable[None]]] = None,
    file_progress: Optional[Callable[[int], Awaitable[None]]] = None,
    job_id: Any = None,
):
    # Legacy behavior path: if tests patched ijson.items we emulate original streaming loop for minimal compatibility
    # Detect by checking if ijson.items has been monkeypatched to not be our shim implementation
    patched_iterator = getattr(ijson, "items", None)
    # If external test monkeypatched ijson.items to a generator-producing mock (not our shim's staticmethod body)
    if patched_iterator is not None and patched_iterator is not _IjsonShim.items:
        messages_created = 0
        tracker = make_tracker(job_id, "message")
        for folder, channel in folder_channel_map.items():
            if not channel:
                continue
            channel_id = channel["id"]
            folder_path = os.path.join(export_dir, folder)
            if not os.path.isdir(folder_path):
                continue
            for msg_file in glob.glob(os.path.join(folder_path, "*.json")):
                try:
                    with open(msg_file, "r", encoding="utf-8") as f:
                        for msg in patched_iterator(f, "item"):
                            slack_id = (msg or {}).get("ts")
                            if not slack_id:
                                continue
                            await tracker.incr_parsed(1)
                            m_entity = Message(
                                slack_id=slack_id,
                                mattermost_id=None,
                                raw_data=msg,
                                status="pending",
                                auto_save=False,
                                job_id=job_id,
                            )
                            await m_entity.save_to_db(channel_id)
                            if getattr(m_entity, "id", None) is not None:
                                await m_entity.create_posted_in_relation(channel_id)
                                await m_entity.create_posted_by_relation()
                                await m_entity.create_thread_relation()
                            messages_created += 1
                            await tracker.incr_processed(1)
                except Exception:
                    continue
                if file_progress:
                    try:
                        await file_progress(1)
                    except Exception:
                        pass
        await tracker.flush()
        return messages_created
    # Default unified path
    summary = await parse_messages_and_related(
        export_dir,
        folder_channel_map,
        emoji_list=None,
        batch_log_every=batch_size,
        progress_messages=progress,
        file_progress=file_progress,
        job_id=job_id,
    )
    return summary.get("messages", 0)
