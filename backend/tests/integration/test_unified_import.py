import pytest
from unittest.mock import patch, AsyncMock, MagicMock, mock_open
from app.services.backup import messages_import


@pytest.mark.asyncio
async def test_unified_import_messages_reactions_attachments_emojis(monkeypatch):
    """Integration-style test of unified single-pass importer on synthetic data.

    Focuses on verifying counts across messages, reactions, attachments, emojis
    without hitting a real database (DB interactions mocked).
    """
    export_dir = "/fake/export"
    folder_channel_map = {"general": {"id": "C777", "name": "general"}}
    fake_messages_day1 = [
        {
            "ts": "1",
            "text": "hello :wave:",
            "reactions": [{"name": "thumbsup", "users": ["U1"]}],
        },
        {
            "ts": "2",
            "text": "file msg",
            "files": [{"id": "F123", "url_private": "https://files.slack.com/f1"}],
        },
    ]
    fake_messages_day2 = [
        {
            "ts": "3",
            "text": "multi react :smile:",
            "reactions": [{"name": "thumbsup", "users": ["U2", "U3"]}],
        },
        {"ts": "4", "text": "emoji only :smile: :wave:"},
    ]

    emoji_list = {
        "wave": "https://emoji.local/wave.png",
        "smile": "https://emoji.local/smile.png",
    }

    # Directory + file listing
    monkeypatch.setattr(messages_import.os.path, "isdir", lambda p: True)
    monkeypatch.setattr(
        messages_import.glob,
        "glob",
        lambda p: [
            f"{export_dir}/general/2024-01-01.json",
            f"{export_dir}/general/2024-01-02.json",
        ],
    )

    def fake_open(path, mode="r", encoding=None):
        m = mock_open(read_data="[]")()
        m.read = lambda: "[]"
        m.__enter__ = lambda s: s
        m.__exit__ = lambda *a: False
        m.name = path
        return m

    with patch("builtins.open", fake_open), patch(
        "json.load",
        side_effect=lambda fh: (
            fake_messages_day1 if "01.json" in fh.name else fake_messages_day2
        ),
    ), patch("app.services.backup.messages_import.Message") as MockMessage, patch(
        "app.services.backup.messages_import.Reaction"
    ) as MockReaction, patch(
        "app.services.backup.messages_import.Attachment"
    ) as MockAttachment, patch(
        "app.services.backup.messages_import.CustomEmoji"
    ) as MockEmoji:
        # Message mock
        msg_inst = MagicMock()
        msg_inst.save_to_db = AsyncMock()
        msg_inst.create_posted_in_relation = AsyncMock()
        msg_inst.create_posted_by_relation = AsyncMock()
        msg_inst.create_thread_relation = AsyncMock()
        MockMessage.side_effect = lambda **kw: msg_inst
        # Reaction mock
        react_inst = MagicMock()
        react_inst.save_to_db = AsyncMock()
        react_inst.create_reacted_by_relation = AsyncMock()
        react_inst.create_reacted_to_relation = AsyncMock()
        MockReaction.side_effect = lambda **kw: react_inst
        # Attachment mock
        att_inst = MagicMock()
        att_inst.save_to_db = AsyncMock()
        att_inst.create_attached_to_relation = AsyncMock()
        MockAttachment.side_effect = lambda **kw: att_inst
        # Emoji mock
        emoji_inst = MagicMock()
        emoji_inst.save_to_db = AsyncMock(return_value=True)
        MockEmoji.side_effect = lambda **kw: emoji_inst

        summary = await messages_import.parse_messages_and_related(
            export_dir,
            folder_channel_map,
            emoji_list=emoji_list,
            job_id=42,
            single_pass=True,
            counters_callback=None,
            batch_log_every=10,
        )

        assert summary["messages"] == 4
        # Reactions: day1 -> 1 user, day2 -> 2 users = 3 total reaction events
        assert summary["reactions"] == 3
        # Attachments: single file
        assert summary["attachments"] == 1
        # Emojis encountered: wave, smile
        assert summary["emojis"] == 2

        # Basic interaction checks
        assert MockMessage.call_count == 4
        # One Reaction entity constructed per user event => 3
        assert MockReaction.call_count == 3
        # Reaction save_to_db called 3 times (one per user event)
        assert react_inst.save_to_db.await_count == 3
        assert MockAttachment.call_count == 1
        assert MockEmoji.call_count >= 2
