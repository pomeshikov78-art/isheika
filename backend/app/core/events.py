"""Шина событий миссии для WebSocket (ТЗ 10.2).

События: TASK_STARTED, TASK_COMPLETED, AGENT_OUTPUT, CONFLICT_FOUND,
PROGRESS, FINISHED, ERROR.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

MAX_HISTORY = 200
QUEUE_SIZE = 100


class EventBus:
    """Публикация без блокировок: медленный подписчик теряет события,
    но не тормозит выполнение миссии."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._history: dict[str, list[dict]] = defaultdict(list)

    def publish(self, mission_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        event = {
            "type": event_type,
            "mission_id": mission_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": payload or {},
        }

        history = self._history[mission_id]
        history.append(event)
        if len(history) > MAX_HISTORY:
            del history[: len(history) - MAX_HISTORY]

        for queue in list(self._subscribers.get(mission_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.debug("Подписчик миссии %s отстаёт, событие пропущено", mission_id)

    def subscribe(self, mission_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers[mission_id].add(queue)
        # Новый подписчик сразу получает предысторию, иначе UI, подключившийся
        # к уже идущей миссии, увидит пустой экран до следующего события.
        for event in self._history.get(mission_id, []):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                break
        return queue

    def unsubscribe(self, mission_id: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(mission_id)
        if subscribers:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(mission_id, None)

    def history(self, mission_id: str) -> list[dict]:
        return list(self._history.get(mission_id, []))

    def clear(self, mission_id: str) -> None:
        self._history.pop(mission_id, None)


bus = EventBus()
