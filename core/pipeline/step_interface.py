from abc import ABC, abstractmethod
from typing import Dict, Any


class PipelineStep(ABC):
    """Schnittstelle für alle modularen Pipeline-Schritte."""

    @abstractmethod
    def execute(self, step_config: Dict[str, Any], engine_context: Any) -> bool:
        """
        Führt den Schritt aus.
        :param step_config: Das JSON-Dictionary aus der config.py für diesen Schritt.
        :param engine_context: Referenz auf die PipelineEngine (für Logging, Pfade etc.)
        :return: True bei Erfolg, False bei Abbruch/Fehler.
        """
        pass