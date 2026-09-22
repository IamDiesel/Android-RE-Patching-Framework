"""
LogCollector — EventBus-Seam.

Sammelt waehrend eines Tool-Aufrufs die Framework-Meldungen (Default: LOG_INFO),
die Services/Engine sonst an die GUI-Konsole schicken, und gibt sie als Text
zurueck. So wird das Pub/Sub-Konsolenmodell zu synchronen MCP-Antworten.
"""
from core.application.event_bus import EventBus


class LogCollector:
    def __init__(self, events=("LOG_INFO",)):
        self.events = tuple(events)
        self.lines = []
        self._cbs = {}

    def __enter__(self):
        for ev in self.events:
            def cb(data, _self=self):
                _self.lines.append(str(data))
            self._cbs[ev] = cb
            EventBus.subscribe(ev, cb)
        return self

    def __exit__(self, *a):
        for ev, cb in self._cbs.items():
            EventBus.unsubscribe(ev, cb)
        self._cbs.clear()
        return False

    def text(self) -> str:
        return "\n".join(self.lines)
