import uuid
from dataclasses import dataclass, field, asdict


@dataclass
class NativeLib:
    """Domain-Model fuer eine selbst gebaute oder importierte native Bibliothek (LibForge)."""
    id: str = ""
    name: str = ""                 # Basisname ohne "lib"/".so" -> Ausgabe lib<name>.so
    description: str = ""
    version: str = "1.0"          # Version (Freitext); wird in description gespiegelt
    origin: str = "built"          # "built" (aus Quellcode) | "imported" (fertige .so)
    source_code: str = ""          # C-Quelle (nur bei origin == "built")
    active: bool = False
    abi: str = "arm64-v8a"         # arm64-v8a | armeabi-v7a | x86_64 | x86
    api_level: int = 30
    link_libs: str = "log dl"      # -l Flags, Leerzeichen-getrennt
    extra_flags: str = ""          # zusaetzliche clang-Flags
    output_kind: str = "shared"    # "shared" -> lib<name>.so | "executable" -> <name> (PIE-Binary)
    last_build_ok: bool = False
    last_build_at: str = ""
    so_relpath: str = ""           # relativ zu BASE_DIR, z.B. data/native_libs/<id>/libhook.so

    # ---- Run-Profil (nur relevant bei output_kind == "executable"; ExeDeploy) ----
    run_dir: str = "/data/local/tmp"                 # exec-erlaubtes Zielverzeichnis
    run_args: str = ""                               # argv nach dem Binary, z.B. "./libb11bb8.so"
    run_env: str = "LD_LIBRARY_PATH=/data/local/tmp" # Umgebungsvariablen fuer den Lauf
    run_cwd: str = "/data/local/tmp"                 # Arbeitsverzeichnis
    runtime_deps: list = field(default_factory=list) # lokale Pfade, die mitdeployt werden
    stage_inputs: list = field(default_factory=list) # App-Home-Dateinamen, die ins run_dir gestaged werden
    pull_globs: list = field(default_factory=list)   # Output-Dateien fuer optionalen Pull
    cleanup_after: bool = False                       # nach dem Run automatisch aufraeumen

    @staticmethod
    def new(name: str = "", origin: str = "built") -> "NativeLib":
        return NativeLib(id=uuid.uuid4().hex, name=name, origin=origin)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NativeLib":
        fields = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in (d or {}).items() if k in fields})
