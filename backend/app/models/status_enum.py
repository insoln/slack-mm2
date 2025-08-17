from enum import Enum


class MappingStatus(str, Enum):
    pending = "pending"
    skipped = "skipped"
    failed = "failed"
    success = "success"
