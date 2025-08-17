import os
import json
import mimetypes
import re
from requests_toolbelt import MultipartEncoder
from app.logging_config import backend_logger
from .base_exporter import ExporterBase, LoggingMixin
from .mm_api_mixin import MMApiMixin

def transliterate_cyrillic(text):
    """Транслитерация кириллицы в латиницу"""
    cyrillic_to_latin = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
        'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'YO',
        'Ж': 'ZH', 'З': 'Z', 'И': 'I', 'Й': 'Y', 'К': 'K', 'Л': 'L', 'М': 'M',
        'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U',
        'Ф': 'F', 'Х': 'H', 'Ц': 'TS', 'Ч': 'CH', 'Ш': 'SH', 'Щ': 'SCH',
        'Ъ': '', 'Ы': 'Y', 'Ь': '', 'Э': 'E', 'Ю': 'YU', 'Я': 'YA'
    }
    
    result = ''
    for char in text:
        result += cyrillic_to_latin.get(char, char) or char
    
    # Убираем лишние символы, оставляем только буквы, цифры и подчеркивания
    result = re.sub(r'[^a-zA-Z0-9_]', '_', result)
    # Убираем множественные подчеркивания
    result = re.sub(r'_+', '_', result)
    # Убираем подчеркивания в начале и конце
    result = result.strip('_')
    
    return result

def build_emoji_multipart(image_data, emoji_name, creator_id):
    """Создать правильный multipart для загрузки эмодзи в Mattermost"""
    emoji_json = {"name": emoji_name}
    if creator_id:
        emoji_json["creator_id"] = creator_id
    
    # Определяем MIME тип
    mime_type = "image/png"  # По умолчанию PNG
    
    fields = {
        "image": (f'{emoji_name}.png', image_data, mime_type),
        "emoji": (None, json.dumps(emoji_json), "application/json")
    }
    
    m = MultipartEncoder(fields=fields)
    return m, m.content_type

class CustomEmojiExporter(ExporterBase, LoggingMixin, MMApiMixin):
    def __init__(self, entity, mm_user_id=None):
        super().__init__(entity)
        self.mm_user_id = mm_user_id

    def _get_emoji_url(self, raw_data):
        """Получить URL эмодзи из raw_data"""
        return raw_data.get("url") if raw_data else None

    async def export_entity(self):
        original_name = self.entity.slack_id
        emoji_name = transliterate_cyrillic(original_name)
        
        if emoji_name != original_name:
            backend_logger.debug(f"Транслитерировано название эмодзи: {original_name} -> {emoji_name}")
        
        self.log_export(f"Экспорт кастомного эмодзи {emoji_name}")
        
        emoji_url = self._get_emoji_url(self.entity.raw_data)
        
        if not emoji_url:
            backend_logger.error(f"Нет URL для эмодзи {emoji_name} в raw_data")
            await self.set_status("failed", error="No emoji URL found in raw_data")
            return
        
        try:
            # Скачиваем изображение эмодзи
            resp = await self.download_file(emoji_url)
            if resp.status_code != 200:
                backend_logger.error(f"Не удалось скачать эмодзи: {emoji_url}, статус: {resp.status_code}")
                await self.set_status("failed", error=f"Failed to download emoji: {resp.status_code}")
                return
            
            image_data = resp.content
            
            # Используем полученный mm_user_id
            creator_id = self.mm_user_id
            if not creator_id:
                backend_logger.error("Не указан mm_user_id для создания эмодзи")
                await self.set_status("failed", error="No mm_user_id provided")
                return
            
            # Создаем правильный multipart для Mattermost
            m, content_type = build_emoji_multipart(image_data, emoji_name, creator_id)
            
            # Отправляем в Mattermost с правильными заголовками
            headers = {
                "Authorization": f"Bearer {os.environ['MM_TOKEN']}",
                "Content-Type": content_type,
                "Connection": "close",
                "Accept": "application/json",
                "User-Agent": "slack-mm2-sync/1.0"
            }
            
            # Используем mm_api_post_multipart для отправки
            response = await self.mm_api_post_multipart("/api/v4/emoji", m, headers)
            
            if response.status_code in [200, 201]:
                await self.set_status("success")
                backend_logger.debug(f"Кастомный эмодзи {emoji_name} экспортирован в Mattermost")
                return
            
            # Проверяем ошибки дублирования
            data = response.json()
            err = data.get("id", "")
            if err == "api.emoji.create.duplicate.app_error":
                # Эмодзи уже существует, получаем его ID
                get_resp = await self.mm_api_get(f"/api/v4/emoji/name/{emoji_name}")
                if get_resp.status_code == 200:
                    emoji_data = get_resp.json()
                    self.entity.mattermost_id = emoji_data.get("id")
                    await self.set_status("success")
                    backend_logger.debug(f"Эмодзи {emoji_name} уже существует в Mattermost, ID: {self.entity.mattermost_id}")
                    return
            
            backend_logger.error(f"Ошибка загрузки эмодзи в Mattermost: {response.status_code}, {response.text}")
            await self.set_status("failed", error=data.get("message", str(data)))
            
        except Exception as e:
            backend_logger.error(f"Ошибка при загрузке эмодзи: {e}")
            await self.set_status("failed", error=str(e)) 