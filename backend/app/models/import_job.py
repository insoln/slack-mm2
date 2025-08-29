from sqlalchemy import Column, BigInteger, Text, DateTime
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import JSONB
from .base import Base
from .job_status_enum import JobStatus
from sqlalchemy import Enum as SAEnum


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    status = Column(SAEnum(JobStatus, name="job_status"), nullable=False, default=JobStatus.queued)
    current_stage = Column(Text, nullable=True)  # extracting, users, channels, messages, emojis, reactions, attachments, exporting
    meta = Column(JSONB, nullable=True)  # optional bag for counters/notes
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
