from abc import ABC, abstractmethod
from src.models.schemas import Notification, NotificationResult


class BaseNotifier(ABC):
    name: str
    supports_threads: bool = False
    supports_severity: bool = False

    @abstractmethod
    async def send(self, notification: Notification) -> NotificationResult:
        ...

    async def health_check(self) -> bool:
        return True
