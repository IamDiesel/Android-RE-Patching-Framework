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

    # ---- Persistenz-Bruecke (prozessuebergreifende „Active Session") ----
    @classmethod
    def to_patch_list(cls) -> List[Dict[str, Any]]:
        """Aggregierte, serialisierbare Patch-Liste (= get_all_patches).
        Wird von active_session_service.save() persistiert."""
        return cls.get_all_patches()

    @classmethod
    def load_patches(cls, patches: List[Dict[str, Any]]) -> None:
        """Setzt die aktiven Listen aus einer flachen Patch-Liste (aus active_session.json).
        Aufteilung nach 'type': hex -> hex, lib_replace -> lib, sonst (smali/new_file) -> smali.
        In-place-Clear erhaelt die Listen-Identitaet (GUI-Property zeigt weiter darauf)."""
        cls.active_hex_patches.clear()
        cls.active_lib_replacements.clear()
        cls.active_smali_patches.clear()
        for p in (patches or []):
            t = p.get("type", "smali")
            if t == "hex":
                cls.active_hex_patches.append(p)
            elif t == "lib_replace":
                cls.active_lib_replacements.append(p)
            else:  # smali, new_file
                cls.active_smali_patches.append(p)