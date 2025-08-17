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
    status = Column(
        SAEnum(MappingStatus, name="mapping_status"),
        nullable=False,
        default=MappingStatus.pending,
    )
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
