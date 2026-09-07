from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class FridaScript:
    id: str
    name: str
    code: str


@dataclass
class FridaConfig:
    """Hält die globale Konfiguration für das Frida-Gadget."""
    mode: str = "listen"  # listen, connect, script, script_directory
    host: str = "127.0.0.1"
    port: int = 27042
    script_directory_path: str = "/data/data/{APP_PACKAGE}/files/frida_scripts"
    active_script_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "host": self.host,
            "port": self.port,
            "script_directory_path": self.script_directory_path,
            "active_script_id": self.active_script_id
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'FridaConfig':
        if not data:
            return cls()
        return cls(
            mode=data.get("mode", "listen"),
            host=data.get("host", "127.0.0.1"),
            port=data.get("port", 27042),
            script_directory_path=data.get("script_directory_path", "/data/data/{APP_PACKAGE}/files/frida_scripts"),
            active_script_id=data.get("active_script_id")
        )
@dataclass
class FridaCollection:
    """Fasst mehrere FridaScripts zu einem Favoriten-Bündel zusammen."""
    id: str
    name: str
    script_ids: List[str] = field(default_factory=list)