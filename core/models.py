"""数据模型 — 对应 C# PBClass / BackupManager.SnapshotInfo"""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ClickKeyInfo:
    """按键数据 — 对应 C# PBClass.ClickKeyInfo"""
    key_text: str = ""
    rel_x: float = 0.0
    rel_y: float = 0.0


@dataclass
class SnapshotInfo:
    """操作快照元数据 — 对应 C# BackupManager.SnapshotInfo"""
    timestamp: datetime = field(default_factory=datetime.now)
    operation_type: str = ""
    description: str = ""
    key_count: int = 0
