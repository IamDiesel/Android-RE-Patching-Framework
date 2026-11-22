import threading
from typing import Callable, Dict, List, Any


class EventBus:
    """Zentraler Event-Bus für die entkoppelte Kommunikation zwischen Modulen."""

    _subscribers: Dict[str, List[Callable[[Any], None]]] = {}
    _lock = threading.RLock()

    @classmethod
    def subscribe(cls, event_type: str, callback: Callable[[Any], None]) -> None:
        """Registriert einen Callback für einen bestimmten Event-Typ."""
        with cls._lock:
            subs = cls._subscribers.setdefault(event_type, [])
            if callback not in subs:
                subs.append(callback)

    @classmethod
    def publish(cls, event_type: str, data: Any = None) -> None:
        """Sendet Daten an alle registrierten Callbacks eines Event-Typs."""
        with cls._lock:
            callbacks = list(cls._subscribers.get(event_type, ()))
        for callback in callbacks:
            try:
                callback(data)
            except Exception as e:
                print(f"[EventBus] Fehler im Subscriber für Event '{event_type}': {e}")

    @classmethod
    def unsubscribe(cls, event_type: str, callback: Callable[[Any], None]) -> None:
        """Entfernt einen spezifischen Callback von einem Event-Typ."""
        with cls._lock:
            subs = cls._subscribers.get(event_type)
            if subs and callback in subs:
                subs.remove(callback)
                if not subs:
                    cls._subscribers.pop(event_type, None)

    @classmethod
    def clear(cls) -> None:
        """Löscht alle Abonnements (primär für saubere Unit-Tests)."""
        with cls._lock:
            cls._subscribers.clear()