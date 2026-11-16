"""撤销/恢复管理器 — 对应 C# BackupManager

功能：
- JSON 内容快照管理（最多 50 个）
- 撤销 / 恢复 / 跳转到指定快照
- HistoryChanged 事件通知
- 线程安全（lock）
"""
import threading
from datetime import datetime
from typing import Callable, List, Optional

from core.models import SnapshotInfo
from core.operation_constants import LOAD_SCHEME
from infra.log_service import LogService


class BackupManager:
    """JSON 内容快照的撤销/恢复管理器"""

    MaxSnapshots = 50

    def __init__(self):
        self._snapshots: List[str] = []
        self._metadatas: List[SnapshotInfo] = []
        self._currentIndex: int = 0
        self._revision: int = 0
        self._lock = threading.Lock()
        self._history_changed_callbacks: List[Callable[[], None]] = []

    # ── HistoryChanged 事件 ──

    def subscribe_history_changed(self, callback: Callable[[], None]) -> None:
        """注册 HistoryChanged 回调"""
        self._history_changed_callbacks.append(callback)

    def unsubscribe_history_changed(self, callback: Callable[[], None]) -> None:
        """取消注册 HistoryChanged 回调"""
        if callback in self._history_changed_callbacks:
            self._history_changed_callbacks.remove(callback)

    def _fire_history_changed(self) -> None:
        """触发 HistoryChanged 事件"""
        for callback in list(self._history_changed_callbacks):
            try:
                callback()
            except Exception:
                pass

    # ── 属性 ──

    @property
    def can_undo(self) -> bool:
        return self._currentIndex > 0 and len(self._snapshots) > 1

    @property
    def can_redo(self) -> bool:
        return self._currentIndex < len(self._snapshots) - 1

    @property
    def has_history(self) -> bool:
        return len(self._snapshots) > 0

    # ── 公共方法 ──

    def record_initial(self, json_content: str, key_count: int = 0) -> None:
        """记录初始快照 — 清空所有历史，记录第一条。触发 HistoryChanged"""
        LogService.debug("BackupManager", f"record_initial: key_count={key_count}")
        with self._lock:
            self._snapshots.clear()
            self._metadatas.clear()
            self._currentIndex = 0
            self._snapshots.append(json_content)
            self._metadatas.append(SnapshotInfo(
                timestamp=datetime.now(),
                operation_type=LOAD_SCHEME,
                description="打开方案文件",
                key_count=key_count,
            ))
            self._revision += 1
        self._fire_history_changed()

    def record_change(
        self,
        json_content: str,
        operation_type: str = "修改",
        description: str = "修改方案",
        key_count: int = 0,
    ) -> None:
        """记录变更快照。若不在最新位置（用户已撤销），丢弃所有 redo 历史。触发 HistoryChanged"""
        LogService.debug("BackupManager", f"record_change: type={operation_type}, desc={description}, keys={key_count}")
        with self._lock:
            # 如果不在最新位置，丢弃 redo 历史
            if self._currentIndex < len(self._snapshots) - 1:
                remove_start = self._currentIndex + 1
                self._snapshots[remove_start:] = []
                self._metadatas[remove_start:] = []

            self._snapshots.append(json_content)
            self._metadatas.append(SnapshotInfo(
                timestamp=datetime.now(),
                operation_type=operation_type,
                description=description,
                key_count=key_count,
            ))

            # 超过最大快照数，从头部裁剪
            while len(self._snapshots) > self.MaxSnapshots:
                self._snapshots.pop(0)
                self._metadatas.pop(0)

            self._currentIndex = len(self._snapshots) - 1
            self._revision += 1
        self._fire_history_changed()

    def record_change_simple(self, json_content: str) -> None:
        """便捷重载：默认操作类型为"修改"、描述为"修改方案"、键数量为0"""
        self.record_change(json_content, "修改", "修改方案", 0)

    def undo(self) -> Optional[str]:
        """撤销一步，在锁外通知历史变化。"""
        LogService.debug("BackupManager", f"undo: currentIndex={self._currentIndex}")
        with self._lock:
            if not self.can_undo:
                return None
            self._currentIndex -= 1
            self._revision += 1
            result = self._snapshots[self._currentIndex]
        self._fire_history_changed()
        return result

    def redo(self) -> Optional[str]:
        """恢复一步，在锁外通知历史变化。"""
        LogService.debug("BackupManager", f"redo: currentIndex={self._currentIndex}")
        with self._lock:
            if not self.can_redo:
                return None
            self._currentIndex += 1
            self._revision += 1
            result = self._snapshots[self._currentIndex]
        self._fire_history_changed()
        return result

    def jump_to(self, index: int) -> Optional[str]:
        """跳转到指定索引，清除之后的 redo 历史，在锁外通知。"""
        LogService.debug("BackupManager", f"jump_to: index={index}, currentIndex={self._currentIndex}")
        with self._lock:
            if index < 0 or index >= len(self._snapshots):
                return None
            if index < len(self._snapshots) - 1:
                remove_start = index + 1
                self._snapshots[remove_start:] = []
                self._metadatas[remove_start:] = []
            self._currentIndex = index
            self._revision += 1
            result = self._snapshots[self._currentIndex]
        self._fire_history_changed()
        return result

    def get_revision(self) -> int:
        """读取历史版本，供窗口拒绝过期选择。"""
        with self._lock:
            return self._revision

    def peek_snapshot(self, index: int) -> Optional[str]:
        """只读快照，不移动历史指针。"""
        with self._lock:
            return self._snapshots[index] if 0 <= index < len(self._snapshots) else None

    def get_history(self) -> List[SnapshotInfo]:
        """返回元数据列表的副本"""
        with self._lock:
            return list(self._metadatas)

    def get_current_index(self) -> int:
        with self._lock:
            return self._currentIndex

    def get_snapshot_count(self) -> int:
        with self._lock:
            return len(self._snapshots)

    def get_snapshot_info(self, index: int) -> Optional[SnapshotInfo]:
        """获取指定索引的快照信息，越界返回 None"""
        with self._lock:
            if index < 0 or index >= len(self._metadatas):
                return None
            return self._metadatas[index]
