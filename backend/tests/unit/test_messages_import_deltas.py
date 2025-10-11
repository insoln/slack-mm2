import pytest
from unittest.mock import patch, MagicMock, AsyncMock, mock_open
from app.services.backup import messages_import
@pytest.mark.asyncio
async def test_parse_channel_messages_delta_counters(monkeypatch):
    export_dir = "/fake/export"
    folder_channel_map = {"general": {"id": "C999", "name": "general"}}
    # Two files each with 3 messages -> triggers two batches if batch_size=3
    fake_messages_file1 = [
        {"ts": "1", "text": "m1", "reactions": [{"name": "smile", "users": ["U1", "U2"]}]},
        {"ts": "2", "text": "m2"},
        {"ts": "3", "text": "m3", "files": [{"id": "F1", "url_private": "https://files.slack.com/a"}]},
    ]
    fake_messages_file2 = [
        {"ts": "4", "text": "m4"},
        {"ts": "5", "text": "m5", "reactions": [{"name": "wow", "users": ["U3"]}]},
        {"ts": "6", "text": "m6", "files": [{"id": "F2", "url_private": "https://files.slack.com/b"}]},
    ]
    # Simulate two JSON files
    monkeypatch.setattr(messages_import.os.path, "isdir", lambda p: True)
    monkeypatch.setattr(
        messages_import.glob, "glob", lambda p: [
            "/fake/export/general/2024-01-01.json",
            "/fake/export/general/2024-01-02.json",
        ]
    )
    # Map filename to iterator
    file_iter_map = {
        "/fake/export/general/2024-01-01.json": iter(fake_messages_file1),
        "/fake/export/general/2024-01-02.json": iter(fake_messages_file2),
    }

    def fake_ijson_items(f, prefix):
        return file_iter_map[f.name]

    # open() must return object with name attr
    def fake_open(path, mode="r", encoding=None):
        m = mock_open(read_data="[]")()
        m.name = path
        return m

    monkeypatch.setattr(messages_import.ijson, "items", fake_ijson_items)
    with patch("builtins.open", fake_open):
        # Patch entities
        with patch("app.services.backup.messages_import.Message") as MockMessage, \
             patch("app.services.backup.messages_import.Reaction") as MockReaction, \
             patch("app.services.backup.messages_import.Attachment") as MockAttachment:
            msg_inst = MagicMock()
            msg_inst.save_to_db = AsyncMock()
            msg_inst.create_posted_in_relation = AsyncMock()
            msg_inst.create_posted_by_relation = AsyncMock()
            msg_inst.create_thread_relation = AsyncMock()
            MockMessage.side_effect = lambda **kw: msg_inst
            react_inst = MagicMock()
            react_inst.create_reacted_by_relation = AsyncMock()
            react_inst.create_reacted_to_relation = AsyncMock()
            MockReaction.side_effect = lambda **kw: react_inst
            att_inst = MagicMock()
            att_inst.create_attached_to_relation = AsyncMock()
            MockAttachment.side_effect = lambda **kw: att_inst

            collected = []
            async def counters_cb(delta):
                collected.append(delta)

            # Run with single_pass so reactions/attachments collected inline; set small batch_size
            result = await messages_import.parse_channel_messages(
                export_dir,
                folder_channel_map,
                batch_size=3,
                progress=None,
                file_progress=None,
                job_id=123,
                single_pass=True,
                counters_callback=counters_cb,
                emoji_list=None,
            )
            assert result["messages"] == 6
            # Summation of deltas
            total_reacts = sum(d.get("reactions", 0) for d in collected)
            total_atts = sum(d.get("attachments", 0) for d in collected)
            assert total_reacts == result["reactions"] == 3  # 2 + 1 users -> 3 individual reaction events
            assert total_atts == result["attachments"] == 2
            # Expect at least two emissions (one per batch/file)
            assert len(collected) >= 2
