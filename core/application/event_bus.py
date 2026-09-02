from typing import Callable, Dict, List, Any


class EventBus:
    """Zentraler Event-Bus für die entkoppelte Kommunikation zwischen Modulen."""

    _subscribers: Dict[str, List[Callable[[Any], None]]] = {}

    @classmethod
    def subscribe(cls, event_type: str, callback: Callable[[Any], None]) -> None:
        """Registriert einen Callback für einen bestimmten Event-Typ."""
        if event_type not in cls._subscribers:
            cls._subscribers[event_type] = []

        if callback not in cls._subscribers[event_type]:
            cls._subscribers[event_type].append(callback)

    @classmethod
    def publish(cls, event_type: str, data: Any = None) -> None:
        """Sendet Daten an alle registrierten Callbacks eines Event-Typs."""
        if event_type in cls._subscribers:
            for callback in cls._subscribers[event_type]:
                try:
                    callback(data)
                except Exception as e:
                    print(f"[EventBus] Fehler im Subscriber für Event '{event_type}': {e}")

    @classmethod
    def unsubscribe(cls, event_type: str, callback: Callable[[Any], None]) -> None:
        """Entfernt einen spezifischen Callback von einem Event-Typ."""
        if event_type in cls._subscribers and callback in cls._subscribers[event_type]:
            cls._subscribers[event_type].remove(callback)

    @classmethod
    def clear(cls) -> None:
        """Löscht alle Abonnements (primär für saubere Unit-Tests)."""
        cls._subscribers.clear()