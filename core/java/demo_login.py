# -*- coding: utf-8 -*-
"""core.java.demo_login — ECHTE Login-Beispiel-App (klassische android.view) MIT nativer Lib.

Nordstern-Vertical-Slice: Java-UI (Benutzername/Passwort/Anmelden) + echte native Methode (ueber die
Fusions-Bruecke auf der arm64-Engine) + GUI-Bedienung. `onCreate` baut den Login-Screen (View-Baum
aufgezeichnet), `onClick` liest das Passwort, ruft nativ `Auth.check(len)` (=> len-6) und setzt den
Status. Zeigt einen vollstaendigen App-Lauf bis zum Login-Screen mit Lib. Zielagnostisch."""
from __future__ import annotations

ENTRY = "Lcom/demo/LoginActivity;"

# native Auth: check(len) = len - 6  (>=0 => Passwort lang genug)
AUTH_BLOB = "401800d1c0035fd6"   # sub x0, x2, #6 ; ret
AUTH_CLASS = "Lcom/demo/Auth;"
AUTH_ADDR = 0x1000

LOGIN_SMALI = """.class public Lcom/demo/LoginActivity;
.super Landroid/app/Activity;

.field private pass:Landroid/widget/EditText;
.field private status:Landroid/widget/TextView;

.method public constructor <init>()V
    .locals 0
    invoke-direct {p0}, Landroid/app/Activity;-><init>()V
    return-void
.end method

.method public onCreate(Landroid/os/Bundle;)V
    .locals 4
    invoke-super {p0, p1}, Landroid/app/Activity;->onCreate(Landroid/os/Bundle;)V
    new-instance v0, Landroid/widget/LinearLayout;
    invoke-direct {v0, p0}, Landroid/widget/LinearLayout;-><init>(Landroid/content/Context;)V
    new-instance v1, Landroid/widget/TextView;
    invoke-direct {v1, p0}, Landroid/widget/TextView;-><init>(Landroid/content/Context;)V
    const-string v2, "Anmelden"
    invoke-virtual {v1, v2}, Landroid/widget/TextView;->setText(Ljava/lang/CharSequence;)V
    invoke-virtual {v0, v1}, Landroid/widget/LinearLayout;->addView(Landroid/view/View;)V
    new-instance v1, Landroid/widget/EditText;
    invoke-direct {v1, p0}, Landroid/widget/EditText;-><init>(Landroid/content/Context;)V
    const-string v2, "Benutzername"
    invoke-virtual {v1, v2}, Landroid/widget/EditText;->setHint(Ljava/lang/CharSequence;)V
    invoke-virtual {v0, v1}, Landroid/widget/LinearLayout;->addView(Landroid/view/View;)V
    new-instance v1, Landroid/widget/EditText;
    invoke-direct {v1, p0}, Landroid/widget/EditText;-><init>(Landroid/content/Context;)V
    const-string v2, "Passwort"
    invoke-virtual {v1, v2}, Landroid/widget/EditText;->setHint(Ljava/lang/CharSequence;)V
    iput-object v1, p0, Lcom/demo/LoginActivity;->pass:Landroid/widget/EditText;
    invoke-virtual {v0, v1}, Landroid/widget/LinearLayout;->addView(Landroid/view/View;)V
    new-instance v1, Landroid/widget/Button;
    invoke-direct {v1, p0}, Landroid/widget/Button;-><init>(Landroid/content/Context;)V
    const-string v2, "Anmelden"
    invoke-virtual {v1, v2}, Landroid/widget/Button;->setText(Ljava/lang/CharSequence;)V
    invoke-virtual {v1, p0}, Landroid/widget/Button;->setOnClickListener(Landroid/view/View$OnClickListener;)V
    invoke-virtual {v0, v1}, Landroid/widget/LinearLayout;->addView(Landroid/view/View;)V
    new-instance v1, Landroid/widget/TextView;
    invoke-direct {v1, p0}, Landroid/widget/TextView;-><init>(Landroid/content/Context;)V
    const-string v2, "Bitte anmelden"
    invoke-virtual {v1, v2}, Landroid/widget/TextView;->setText(Ljava/lang/CharSequence;)V
    iput-object v1, p0, Lcom/demo/LoginActivity;->status:Landroid/widget/TextView;
    invoke-virtual {v0, v1}, Landroid/widget/LinearLayout;->addView(Landroid/view/View;)V
    invoke-virtual {p0, v0}, Lcom/demo/LoginActivity;->setContentView(Landroid/view/View;)V
    return-void
.end method

.method public onClick(Landroid/view/View;)V
    .locals 4
    iget-object v0, p0, Lcom/demo/LoginActivity;->pass:Landroid/widget/EditText;
    invoke-virtual {v0}, Landroid/widget/EditText;->getText()Ljava/lang/CharSequence;
    move-result-object v1
    invoke-virtual {v1}, Ljava/lang/String;->length()I
    move-result v2
    invoke-static {v2}, Lcom/demo/Auth;->check(I)I
    move-result v2
    iget-object v0, p0, Lcom/demo/LoginActivity;->status:Landroid/widget/TextView;
    if-gez v2, :ok
    const-string v3, "Passwort zu kurz (nativ geprueft)"
    invoke-virtual {v0, v3}, Landroid/widget/TextView;->setText(Ljava/lang/CharSequence;)V
    return-void
    :ok
    const-string v3, "Angemeldet (nativ bestaetigt)"
    invoke-virtual {v0, v3}, Landroid/widget/TextView;->setText(Ljava/lang/CharSequence;)V
    return-void
.end method
"""


def build_login_app():
    """Startet die Login-Activity mit nativer Auth-Lib (Fusion). Rueckgabe:
    dict(tree, vm, activity, stubs, eng, hook)."""
    from core.java.dalvik import DalvikVM
    from core.java import smali as Sm
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import _StubRuntime
    from core.java.view_tree import install_view_stubs
    from core.java.objects import JObject
    from services.native_emu_service import NativeEmuService
    from services.native_ffi_service import NativeFfiService
    from services.native_jni_env_service import NativeJniEnvService
    from core.java.native_link import install_native_link, NativeMethodTable
    from core.java.jni_boundary import install_jni_boundary

    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    ffi = NativeFfiService(publish=False); jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    svc.load_blob(eng, AUTH_ADDR, bytes.fromhex(AUTH_BLOB))

    vm = DalvikVM(); vm.lenient = True
    for md in Sm.parse_class(LOGIN_SMALI)[1]:
        vm.register(md)
    stubs = AndroidStubs(vm=vm)
    tree = install_view_stubs(stubs)
    try:
        stubs.seed_framework_statics(vm)
    except Exception:
        pass
    vm.runtime = _StubRuntime(stubs)
    table = NativeMethodTable()
    install_native_link(vm, eng, jni, table=table)
    table.register(AUTH_CLASS, "check", "(I)I", AUTH_ADDR, ret="int")   # wie RegisterNatives
    hook = install_jni_boundary(vm.bridge, vm=vm)

    activity = JObject(ENTRY)
    vm.invoke(ENTRY, "onCreate", "(Landroid/os/Bundle;)V", (activity, None))
    return {"tree": tree, "vm": vm, "activity": activity, "stubs": stubs, "eng": eng, "hook": hook}


def set_password(ctx, text):
    """Simuliert die Eingabe ins Passwort-Feld (sonst via GUI-Input-Injektion)."""
    pw = ctx["activity"].fields.get("pass")
    node = pw.fields.get("$view") if pw is not None else None
    if node is not None:
        node.text = text
        ctx["tree"].touch()
    return node


def do_login(ctx):
    """Feuert den Anmelden-Klick (onClick der Activity). Rueckgabe: Status-Text."""
    ctx["vm"].invoke(ENTRY, "onClick", "(Landroid/view/View;)V", (ctx["activity"], None))
    st = ctx["activity"].fields.get("status")
    node = st.fields.get("$view") if st is not None else None
    return node.text if node is not None else None
