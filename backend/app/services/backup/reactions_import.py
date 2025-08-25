import os
from app.services.entities.reaction import Reaction
from app.services.entities.custom_emoji import CustomEmoji
from app.logging_config import backend_logger
from app.models.base import SessionLocal

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
                auto_save=False
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
            auto_save=False
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
            if (emoji_list and 
                emoji_name in emoji_list and 
                emoji_list[emoji_name]):  # Проверяем, что URL не пустой
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
        custom_emoji_entities = _create_custom_emoji_entities(custom_emoji_names, emoji_list)
        
        for emoji_entity in custom_emoji_entities:
            await emoji_entity.save_to_db()
        
        backend_logger.info(f"Импортировано кастомных эмодзи: {len(custom_emoji_entities)}")
        
        # 4. Создаем связи между реакциями и кастомными эмодзи
        for reaction_entity, emoji_name in all_reactions:
            if emoji_name in custom_emoji_names:
                await reaction_entity.create_custom_emoji_relation(emoji_name) 