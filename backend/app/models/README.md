# models/

ORM-модели для работы с базой данных (SQLAlchemy, async).

- base.py — настройка engine, session, Base
- entity.py — универсальная модель Entity для всех сущностей
- entity_relation.py — универсальная модель EntityRelation для связей между сущностями
- status_enum.py — Enum MappingStatus для статусов маппинга
- ... (другие модели, если появятся)

## Enum MappingStatus

В проекте используется строгий Enum для статусов маппинга:

```python
from enum import Enum

class MappingStatus(str, Enum):
    pending = "pending"
    skipped = "skipped"
    failed = "failed"
    success = "success"
```

- Используется в Python-коде для типизации и валидации.
- В ORM-модели Entity поле status объявлено как SAEnum(MappingStatus).
- В базе данных (PostgreSQL) поле status имеет тип ENUM mapping_status (см. миграцию).

### Описание статусов
- **pending** — подлежит экспорту
- **skipped** — не подлежит экспорту (например, не нужен в Mattermost)
- **failed** — экспорт не удался (ошибка)
- **success** — экспорт удался

## Пример универсальной модели Entity
```python
from sqlalchemy import Column, BigInteger, Text, JSON, DateTime, Enum as SAEnum
from sqlalchemy.sql import func
from .base import Base
from .status_enum import MappingStatus

class Entity(Base):
    """
    Универсальная таблица для всех сущностей.
    status: MappingStatus (Postgres ENUM)
      - pending: подлежит экспорту
      - skipped: не подлежит экспорту
      - failed: экспорт не удался
      - success: экспорт удался
    """
    __tablename__ = "entities"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    entity_type = Column(Text, nullable=False)
    slack_id = Column(Text, nullable=False)
    mattermost_id = Column(Text)
    raw_data = Column(JSON)
    status = Column(SAEnum(MappingStatus, name="mapping_status"), nullable=False, default=MappingStatus.pending)
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

## Пример универсальной модели EntityRelation
```python
from sqlalchemy import Column, BigInteger, Text, JSON, DateTime, ForeignKey
from sqlalchemy.sql import func
from .base import Base

class EntityRelation(Base):
    __tablename__ = "entity_relations"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    from_entity_id = Column(BigInteger, ForeignKey("entities.id", ondelete="CASCADE"))
    to_entity_id = Column(BigInteger, ForeignKey("entities.id", ondelete="CASCADE"))
    relation_type = Column(Text, nullable=False)
    raw_data = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

## Пример создания связи между сущностями
```python
from app.models.entity_relation import EntityRelation
from app.models.base import SessionLocal

async def create_relation(from_id, to_id, relation_type, raw_data=None):
    async with SessionLocal() as session:
        relation = EntityRelation(
            from_entity_id=from_id,
            to_entity_id=to_id,
            relation_type=relation_type,
            raw_data=raw_data,
        )
        session.add(relation)
        await session.commit()
``` 