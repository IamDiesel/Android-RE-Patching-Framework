"""core.native — generischer Usermode-AArch64-Analysekern (RE Studio „Native Studio").

GUI-frei und framework-entkoppelt: dieses Paket kennt weder tkinter noch den
EventBus. Fortschritt/Ereignisse laufen über einen Listener-Callback
(`iface.Listener`); die Service-Schicht brückt diesen später an den EventBus.

Zielbild: beliebige `.so`/PIE laden, emulieren, snapshotten, liften — der
zielspezifische Glue (z. B. die LibreLinkUp-White-Box) dockt ausschließlich
über `iface.TargetPlugin` an, nie im Kern.

Status: M1-Skelett. Loader ist funktional; Emulator/Snapshot sind Gerüste.
Siehe Projekt-Doc `restudio-generalisierung.md` (§2 Stapel, §4 WS-E1..E7).
"""

from . import iface  # noqa: F401
from . import loader  # noqa: F401
from . import emu  # noqa: F401
from . import snapshot  # noqa: F401

__all__ = ["iface", "loader", "emu", "snapshot"]
