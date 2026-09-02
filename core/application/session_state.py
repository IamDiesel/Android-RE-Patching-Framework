from typing import List, Dict, Any


class SessionState:
    """Zentraler Status-Container für die aktuelle Workspace-Session."""

    package_name: str = "app"
    architecture: str = "ARM32"

    # Hier halten wir die Listen, die zuvor wild in der UI verstreut waren
    active_hex_patches: List[Dict[str, Any]] = []
    active_lib_replacements: List[Dict[str, Any]] = []
    active_smali_patches: List[Dict[str, Any]] = []

    @classmethod
    def get_all_patches(cls) -> List[Dict[str, Any]]:
        """Gibt eine aggregierte Liste aller Patches für die Pipeline zurück."""
        all_patches = []
        all_patches.extend(cls.active_hex_patches)
        all_patches.extend(cls.active_lib_replacements)
        all_patches.extend(cls.active_smali_patches)
        return all_patches

    @classmethod
    def clear_session(cls) -> None:
        """Setzt die Session für einen neuen Workspace zurück."""
        cls.active_hex_patches.clear()
        cls.active_lib_replacements.clear()
        cls.active_smali_patches.clear()