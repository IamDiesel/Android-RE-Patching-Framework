# -*- coding: utf-8 -*-
"""core.java.device_profile — konfigurierbares Geraete-Profil (generisch, zielagnostisch).

Zweck: faithful android.*-Konstanten (Build.*, SDK, Thread-Prioritaet, Paketname) aus EINEM
konfigurierbaren Profil liefern, statt sie im Kern zu verdrahten. Integritaets-Konstanten-
Obfuskierung (DexGuard & Co.) liest solche Werte als „Konstanten"; falsche (0-)Rueckgaben brechen
die Deobfuskierung oder loesen die RASP aus. Werte kommen aus Defaults, Config oder dem Manifest
(Paketname). Keine Target-Spezifik im Kern.
"""
from __future__ import annotations

from core.java.objects import JString


class DeviceProfile:
    DEFAULTS = {
        "package": None,
        "sdk_int": 34,
        "release": "14",
        "model": "Pixel 9 Pro",
        "manufacturer": "Google",
        "brand": "google",
        "device": "caiman",
        "product": "caiman",
        "fingerprint": "google/caiman/caiman:14/AP1A/user",
        "thread_priority": 0,     # Process.getThreadPriority Default
        "clock_epoch_ms": None,   # optionale feste Startzeit (Epoch-ms)
        "clock_iso":      None,   # optionale Startzeit als ISO-String ("2026-11-21 14:30:00")
        "clock_tz":       None,   # optionale Zeitzone ("Europe/Berlin", "UTC", ...; None = Host-Lokal)
        "clock_frozen":   False,  # True = System.currentTimeMillis() schreitet nicht automatisch voran
    }

    def __init__(self, **over):
        self.values = dict(self.DEFAULTS)
        for k, v in (over or {}).items():
            if v is not None:
                self.values[k] = v

    def get(self, key, default=None):
        return self.values.get(key, default)

    @property
    def package(self):
        return self.values.get("package")

    def build_statics(self):
        """cls -> {field: value} fuer android.os.Build und Build$VERSION."""
        v = self.values
        return {
            "Landroid/os/Build;": {
                "MODEL": JString(v["model"]), "MANUFACTURER": JString(v["manufacturer"]),
                "BRAND": JString(v["brand"]), "DEVICE": JString(v["device"]),
                "PRODUCT": JString(v["product"]), "FINGERPRINT": JString(v["fingerprint"]),
            },
            "Landroid/os/Build$VERSION;": {
                "SDK_INT": int(v["sdk_int"]), "RELEASE": JString(str(v["release"])),
            },
        }

    @classmethod
    def from_config(cls, cfg):
        """Aus einem Config-Dict (z. B. ConfigManager.config['DEVICE_PROFILE']) bauen."""
        cfg = cfg or {}
        return cls(**{k: cfg.get(k) for k in cls.DEFAULTS})
