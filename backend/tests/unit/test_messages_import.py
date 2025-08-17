import pytest
import asyncio
from unittest.mock import patch, MagicMock, mock_open, AsyncMock
from app.services.backup import messages_import

@pytest.mark.asyncio
async def test_parse_channel_messages_creates_entities(monkeypatch):
    # Подготовка тестовых данных
    export_dir = '/fake/export'
    folder_channel_map = {'general': {'id': 'C123', 'name': 'general'}}
    fake_messages = [
        {'ts': '123.456', 'user': 'U1', 'text': 'hello'},
        {'ts': '789.012', 'user': 'U2', 'text': 'world'},
    ]
    # Мокаем os.path.isdir
    monkeypatch.setattr(messages_import.os.path, 'isdir', lambda p: True)
    # Мокаем glob.glob
    monkeypatch.setattr(messages_import.glob, 'glob', lambda p: ['/fake/export/general/2024-01-01.json'])
    # Мокаем open и json.load
    m = mock_open(read_data='[]')
    with patch('builtins.open', m):
        with patch('app.services.backup.messages_import.json.load', return_value=fake_messages):
            # Мокаем Message и его методы
            with patch('app.services.backup.messages_import.Message') as MockMessage:
                mock_msg = MagicMock()
                mock_msg.save_to_db = AsyncMock()
                mock_msg.create_posted_in_relation = AsyncMock()
                mock_msg.create_posted_by_relation = AsyncMock()
                mock_msg.create_thread_relation = AsyncMock()
                MockMessage.side_effect = lambda **kwargs: mock_msg
                # Запуск
                result = await messages_import.parse_channel_messages(export_dir, folder_channel_map)
                # Проверки
                assert len(result) == 2
                assert MockMessage.call_count == 2
                mock_msg.save_to_db.assert_any_call()
                mock_msg.create_posted_in_relation.assert_any_call('C123')
                mock_msg.create_posted_by_relation.assert_any_call()
                mock_msg.create_thread_relation.assert_any_call() 