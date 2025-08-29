from sqlalchemy import Column, BigInteger, Text, JSON, DateTime, ForeignKey
from sqlalchemy.sql import func
from .base import Base


class EntityRelation(Base):
    __tablename__ = "entity_relations"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    from_entity_id = Column(BigInteger, ForeignKey("entities.id", ondelete="CASCADE"))
    to_entity_id = Column(BigInteger, ForeignKey("entities.id", ondelete="CASCADE"))
    relation_type = Column(Text, nullable=False)
    job_id = Column(BigInteger, ForeignKey("import_jobs.id", ondelete="CASCADE"))
    raw_data = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
