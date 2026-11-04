import os, sys, tempfile, shutil
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.fuzzing_engine import FuzzingEngine
from services.patch_service import PatchService
from core.pipeline.steps.patch_steps import SmartPatchStep
from core.application.session_state import SessionState
from core.domain.exceptions import PatchConflictException

A = """.method public onStart()V
    .locals 1
    .line 42
    invoke-super {p0}, Landroid/app/Activity;->onStart()V
    if-eqz v0, :cond_0
    goto :goto_0
    :cond_0
    return-void
    :goto_0
    return-void
.end method"""

B = """.method public onStart()V
    .locals 1
    .prologue
    .line 999
    invoke-super {p3}, Landroid/app/Activity;->onStart()V
    if-eqz v2, :cond_7
    goto :goto_9    # obfuscation junk
    :cond_7
    return-void
    :goto_9
    return-void
.end method"""

C = A.replace("return-void\n    :goto_0", "nop\n    return-void\n    :goto_0")


def test_label_register_line_drift_is_equal():
    assert FuzzingEngine._normalize_smali(A) == FuzzingEngine._normalize_smali(B)


def test_real_body_change_is_conflict():
    assert FuzzingEngine._normalize_smali(A) != FuzzingEngine._normalize_smali(C)


def test_resolver_and_build_share_normalizer():
    assert PatchService.clean_smali_for_match(A) == FuzzingEngine._normalize_smali(A)


def test_inline_comment_stripped():
    assert FuzzingEngine._normalize_smali("nop # kommentar") == FuzzingEngine._normalize_smali("nop")


# ---- S3-2: Super-Stub-Gate ----
STUB = """.method public onStart()V
    .locals 0
    invoke-super {p0}, Landroid/app/Activity;->onStart()V
    return-void
.end method"""

SURGICAL = """.method public onStart()V
    .locals 1
    invoke-direct {p0}, Lcom/app/Foo;->secret()V
    const/4 v0, 0x2
    return-void
.end method"""


def test_super_stub_detected():
    assert SmartPatchStep._is_super_stub(STUB) is True


def test_surgical_edit_not_a_stub():
    assert SmartPatchStep._is_super_stub(SURGICAL) is False


# ---- S3-2: Integration SmartPatchStep.execute ----
SIG = ".method public onStart()V"
OLD_BODY = SIG + "\n    .locals 0\n    .line 10\n    invoke-direct {p0}, Lcom/app/Foo;->secret()V\n    return-void\n.end method\n"
DST_BODY = SIG + "\n    .locals 0\n    .line 77\n    invoke-direct {p0}, Lcom/app/Foo;->secretRenamed()V\n    const/4 v0, 0x1\n    return-void\n.end method\n"


class _Ctx:
    def __init__(self, base):
        self.cfg = type("C", (), {})()
        self.cfg.paths = {"APP_SOURCE_DIR": os.path.join(base, "source"),
                          "DEST_DIR": os.path.join(base, "dest")}
        self.cfg.config = {"MANIFEST_STRATEGY": "smali_only"}
        self.logs = []

    def get_unpacked_dir_name(self):
        return "base_unpacked_apktool"

    def log(self, m):
        self.logs.append(m)


def _prepare(base, dst_body):
    rel = os.path.join("base_unpacked_apktool", "smali", "com", "app", "Foo.smali")
    src = os.path.join(base, "source", rel)
    dst = os.path.join(base, "dest", rel)
    os.makedirs(os.path.dirname(src), exist_ok=True)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    open(src, "w", encoding="utf-8").write(OLD_BODY)
    open(dst, "w", encoding="utf-8").write(dst_body)
    return dst


def test_smartpatch_superstub_applies_on_body_drift():
    base = tempfile.mkdtemp()
    saved = list(SessionState.active_smali_patches)
    try:
        dst = _prepare(base, DST_BODY)
        SessionState.active_smali_patches[:] = [{
            "type": "smali", "scope": "method", "file": "smali/com/app/Foo.smali",
            "orig": OLD_BODY, "edit": STUB}]
        ok = SmartPatchStep().execute({}, _Ctx(base))
        out = open(dst, encoding="utf-8").read()
        assert ok is True
        assert "invoke-super" in out
        assert "secretRenamed" not in out   # alter/neuer Body ersetzt
    finally:
        SessionState.active_smali_patches[:] = saved
        shutil.rmtree(base, ignore_errors=True)


def test_smartpatch_surgical_raises_conflict_on_drift():
    base = tempfile.mkdtemp()
    saved = list(SessionState.active_smali_patches)
    try:
        _prepare(base, DST_BODY)
        SessionState.active_smali_patches[:] = [{
            "type": "smali", "scope": "method", "file": "smali/com/app/Foo.smali",
            "orig": OLD_BODY, "edit": SURGICAL}]
        raised = False
        try:
            SmartPatchStep().execute({}, _Ctx(base))
        except PatchConflictException:
            raised = True
        assert raised is True
    finally:
        SessionState.active_smali_patches[:] = saved
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); ok += 1
        except Exception:
            print("FAIL", fn.__name__); traceback.print_exc()
    print("%d/%d passed" % (ok, len(fns)))
    sys.exit(0 if ok == len(fns) else 1)
