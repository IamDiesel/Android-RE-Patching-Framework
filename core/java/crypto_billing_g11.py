# -*- coding: utf-8 -*-
"""core.java.crypto_billing_g11 — G11: Vollstaendige JCA-Kryptographie, EncryptedSharedPreferences & Billing/Integrity-Seams."""
from __future__ import annotations

import hashlib
import hmac
import struct
from typing import Any, Dict, List, Optional

from core.java.objects import JArray, JObject, JString

_MD_ALGOS = {
    "md5": "md5",
    "sha-1": "sha1",
    "sha1": "sha1",
    "sha-256": "sha256",
    "sha256": "sha256",
    "sha-384": "sha384",
    "sha384": "sha384",
    "sha-512": "sha512",
    "sha512": "sha512",
}


def _txt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, JString):
        return v.text or ""
    if isinstance(v, JObject) and "text" in v.fields:
        return str(v.fields["text"] or "")
    return str(v)


def _bytes_from(v: Any) -> bytes:
    if v is None:
        return b""
    data = getattr(v, "data", None)
    if data is not None:
        return bytes(int(x) & 0xFF for x in data)
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    return b""


def _get_billing_state(stubs: Any) -> Dict[str, Any]:
    return stubs.__dict__.setdefault("_billing_state", {
        "pro_override": False,
        "entitled_products": ["pro_monthly"],
        "available_products": ["pro_monthly", "pro_yearly"],
        "response_code": 0,
        "purchases": [],
    })


def set_billing_pro_override(stubs: Any, enabled: bool, products: Optional[List[str]] = None) -> Dict[str, Any]:
    st = _get_billing_state(stubs)
    st["pro_override"] = bool(enabled)
    if products is not None:
        st["entitled_products"] = [str(p) for p in products if p]
    return st


def _make_purchase_obj(product_id: str) -> JObject:
    pid = str(product_id or "pro_monthly")
    tok = f"mock_token_{pid}"
    raw_json = f'{{"productId":"{pid}","purchaseToken":"{tok}","purchaseState":0,"acknowledged":true}}'
    return JObject("Lcom/android/billingclient/api/Purchase;", {
        "_product_id": pid,
        "_token": tok,
        "_json": raw_json,
        "_state": 1,   # PURCHASED = 1
        "_ack": 1,
    })


def install_g11_stubs(stubs: Any) -> None:
    """Registriert JCA Crypto (Digest/Hmac/PBKDF2/Signature), EncryptedSharedPreferences & Billing/Integrity."""
    if stubs is None or getattr(stubs, "_g11_installed", False):
        return
    stubs._g11_installed = True
    stubs.__dict__.setdefault("_crypto_log", [])
    _get_billing_state(stubs)

    def _add(cls: str, name: str, sig: str, fn) -> None:
        if (cls, name, sig) not in stubs._stubs:
            stubs.register(cls, name, sig, fn)

    # ---- 1. MessageDigest ---------------------------------------------------
    MD = "Ljava/security/MessageDigest;"

    def _md_new(algo: str) -> JObject:
        norm = _MD_ALGOS.get(algo.strip().lower(), "sha256")
        return JObject(MD, {"_algo": norm, "_h": hashlib.new(norm)})

    _add(MD, "getInstance", f"(Ljava/lang/String;){MD}", lambda a: _md_new(_txt(a[0]) if a else "SHA-256"))
    _add(MD, "update", "([B)V", lambda a: a[0].fields["_h"].update(_bytes_from(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(MD, "update", "([BII)V", lambda a: a[0].fields["_h"].update(_bytes_from(a[1])[int(a[2]):int(a[2]) + int(a[3])]) if len(a) > 3 and isinstance(a[0], JObject) else None)
    _add(MD, "update", "(B)V", lambda a: a[0].fields["_h"].update(bytes((int(a[1]) & 0xFF,))) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(MD, "digest", "()[B", lambda a: JArray("[B", list(a[0].fields["_h"].digest())) if a and isinstance(a[0], JObject) else JArray("[B", []))
    _add(MD, "digest", "([B)[B", lambda a: (a[0].fields["_h"].update(_bytes_from(a[1])), JArray("[B", list(a[0].fields["_h"].digest())))[1] if len(a) > 1 and isinstance(a[0], JObject) else JArray("[B", []))
    _add(MD, "reset", "()V", lambda a: a[0].fields.__setitem__("_h", hashlib.new(a[0].fields.get("_algo", "sha256"))) if a and isinstance(a[0], JObject) else None)

    # ---- 2. Mac / Hmac & SecretKeySpec & PBKDF2 -----------------------------
    SKS = "Ljavax/crypto/spec/SecretKeySpec;"
    _add(SKS, "<init>", "([BLjava/lang/String;)V",
         lambda a: (a[0].fields.update({"_key": _bytes_from(a[1]), "_algo": _txt(a[2]) if len(a) > 2 else "AES"}), a[0])[1] if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(SKS, "getEncoded", "()[B", lambda a: JArray("[B", list(a[0].fields.get("_key", b""))) if a and isinstance(a[0], JObject) else JArray("[B", []))
    _add(SKS, "getAlgorithm", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("_algo", "AES")) if a and isinstance(a[0], JObject) else JString("AES"))

    MAC = "Ljavax/crypto/Mac;"
    _add(MAC, "getInstance", f"(Ljava/lang/String;){MAC}", lambda a: JObject(MAC, {"_algo": _txt(a[0]) if a else "HmacSHA256", "_buf": bytearray(), "_key": b""}))

    def _mac_init(a):
        if len(a) > 1 and isinstance(a[0], JObject):
            kobj = a[1]
            kb = kobj.fields.get("_key", b"") if isinstance(kobj, JObject) else _bytes_from(kobj)
            a[0].fields["_key"] = bytes(kb or b"\x00")
            a[0].fields["_buf"] = bytearray()
        return None

    def _mac_algo_name(raw: str) -> str:
        low = raw.lower()
        if "512" in low:
            return "sha512"
        if "384" in low:
            return "sha384"
        if "1" in low and "256" not in low:
            return "sha1"
        if "md5" in low:
            return "md5"
        return "sha256"

    _add(MAC, "init", "(Ljava/security/Key;)V", _mac_init)
    _add(MAC, "update", "([B)V", lambda a: a[0].fields.setdefault("_buf", bytearray()).extend(_bytes_from(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(MAC, "doFinal", "()[B", lambda a: JArray("[B", list(hmac.new(a[0].fields.get("_key", b""), bytes(a[0].fields.get("_buf", b"")), _mac_algo_name(a[0].fields.get("_algo", "HmacSHA256"))).digest())) if a and isinstance(a[0], JObject) else JArray("[B", []))
    _add(MAC, "doFinal", "([B)[B", lambda a: (a[0].fields.setdefault("_buf", bytearray()).extend(_bytes_from(a[1])), JArray("[B", list(hmac.new(a[0].fields.get("_key", b""), bytes(a[0].fields.get("_buf", b"")), _mac_algo_name(a[0].fields.get("_algo", "HmacSHA256"))).digest())))[1] if len(a) > 1 and isinstance(a[0], JObject) else JArray("[B", []))

    PBE = "Ljavax/crypto/spec/PBEKeySpec;"
    _add(PBE, "<init>", "([C[BII)V", lambda a: a[0].fields.update({"_pw": "".join(chr(int(c)) for c in (getattr(a[1], "data", ()) or ())), "_salt": _bytes_from(a[2]), "_iter": int(a[3]), "_len": int(a[4])}) if len(a) > 4 and isinstance(a[0], JObject) else None)
    SKF = "Ljavax/crypto/SecretKeyFactory;"
    _add(SKF, "getInstance", f"(Ljava/lang/String;){SKF}", lambda a: JObject(SKF, {"_algo": _txt(a[0]) if a else "PBKDF2WithHmacSHA256"}))

    def _skf_gen(a):
        spec = a[1] if len(a) > 1 and isinstance(a[1], JObject) else None
        pw = (spec.fields.get("_pw", "") if spec else "").encode("utf-8")
        salt = (spec.fields.get("_salt", b"salt") if spec else b"salt")
        iters = max(1, min(100_000, int(spec.fields.get("_iter", 1000)) if spec else 1000))
        dklen = max(1, int(spec.fields.get("_len", 256)) if spec else 256) // 8
        algo = "sha1" if (a and "sha1" in a[0].fields.get("_algo", "").lower()) else "sha256"
        dk = hashlib.pbkdf2_hmac(algo, pw, salt, iters, dklen=dklen)
        return JObject(SKS, {"_key": dk, "_algo": "PBKDF2"})

    _add(SKF, "generateSecret", "(Ljava/security/spec/KeySpec;)Ljavax/crypto/SecretKey;", _skf_gen)

    # ---- 3. KeyPairGenerator & Signature (deterministic sign/verify) -------
    KPG = "Ljava/security/KeyPairGenerator;"
    KP = "Ljava/security/KeyPair;"
    SIG = "Ljava/security/Signature;"
    _add(KPG, "getInstance", f"(Ljava/lang/String;){KPG}", lambda a: JObject(KPG, {"_algo": _txt(a[0]) if a else "RSA"}))
    _add(KPG, "getInstance", f"(Ljava/lang/String;Ljava/lang/String;){KPG}", lambda a: JObject(KPG, {"_algo": _txt(a[0]) if a else "RSA"}))
    _add(KPG, "initialize", "(I)V", lambda a: None)

    def _kpg_gen(a):
        algo = a[0].fields.get("_algo", "RSA") if a and isinstance(a[0], JObject) else "RSA"
        sec = hashlib.sha256(f"g11-kp:{algo}".encode("utf-8")).digest()
        priv = JObject("Ljava/security/PrivateKey;", {"_secret": sec, "_algo": algo})
        pub = JObject("Ljava/security/PublicKey;", {"_secret": sec, "_algo": algo})
        return JObject(KP, {"_priv": priv, "_pub": pub})

    _add(KPG, "generateKeyPair", f"(){KP}", _kpg_gen)
    _add(KP, "getPrivate", "()Ljava/security/PrivateKey;", lambda a: a[0].fields.get("_priv") if a and isinstance(a[0], JObject) else None)
    _add(KP, "getPublic", "()Ljava/security/PublicKey;", lambda a: a[0].fields.get("_pub") if a and isinstance(a[0], JObject) else None)

    _add(SIG, "getInstance", f"(Ljava/lang/String;){SIG}", lambda a: JObject(SIG, {"_algo": _txt(a[0]) if a else "SHA256withRSA", "_buf": bytearray()}))
    _add(SIG, "initSign", "(Ljava/security/PrivateKey;)V", lambda a: a[0].fields.update({"_key": a[1], "_buf": bytearray()}) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(SIG, "initVerify", "(Ljava/security/PublicKey;)V", lambda a: a[0].fields.update({"_key": a[1], "_buf": bytearray()}) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(SIG, "update", "([B)V", lambda a: a[0].fields.setdefault("_buf", bytearray()).extend(_bytes_from(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)

    def _sig_tag(sig_obj: JObject) -> bytes:
        kobj = sig_obj.fields.get("_key")
        sec = kobj.fields.get("_secret", b"g11") if isinstance(kobj, JObject) else b"g11"
        return hmac.new(sec, bytes(sig_obj.fields.get("_buf", b"")), "sha256").digest()

    _add(SIG, "sign", "()[B", lambda a: JArray("[B", list(_sig_tag(a[0]))) if a and isinstance(a[0], JObject) else JArray("[B", []))
    _add(SIG, "verify", "([B)Z", lambda a: 1 if (len(a) > 1 and isinstance(a[0], JObject) and hmac.compare_digest(_sig_tag(a[0]), _bytes_from(a[1]))) else 0)

    # ---- 4. EncryptedSharedPreferences & MasterKey --------------------------
    MKB = "Landroidx/security/crypto/MasterKey$Builder;"
    MK = "Landroidx/security/crypto/MasterKey;"
    _add(MKB, "<init>", "(Landroid/content/Context;)V", lambda a: a[0].fields.update({"_alias": "_androidx_security_master_key_"}) if a else None)
    _add(MKB, "<init>", "(Landroid/content/Context;Ljava/lang/String;)V", lambda a: a[0].fields.update({"_alias": _txt(a[2]) if len(a) > 2 else "mk"}) if a else None)
    _add(MKB, "setKeyScheme", f"(Landroidx/security/crypto/MasterKey$KeyScheme;){MKB}", lambda a: a[0] if a else None)
    _add(MKB, "build", f"(){MK}", lambda a: JObject(MK, {"_alias": a[0].fields.get("_alias", "mk") if a and isinstance(a[0], JObject) else "mk"}))
    _add("Landroidx/security/crypto/MasterKeys;", "getOrCreate", "(Landroid/security/keystore/KeyGenParameterSpec;)Ljava/lang/String;", lambda a: JString("master_key"))

    ESP = "Landroidx/security/crypto/EncryptedSharedPreferences;"
    def _open_enc_prefs(name: str) -> JObject:
        pname = f"enc:{name or 'secret_prefs'}"
        stubs.__dict__.setdefault("_prefs_store", {}).setdefault(pname, {})
        return JObject("Landroid/content/SharedPreferences;", {"__prefs_name": pname})

    _add(ESP, "create", "(Ljava/lang/String;Ljava/lang/String;Landroid/content/Context;Landroidx/security/crypto/EncryptedSharedPreferences$PrefKeyEncryptionScheme;Landroidx/security/crypto/EncryptedSharedPreferences$PrefValueEncryptionScheme;)Landroid/content/SharedPreferences;",
         lambda a: _open_enc_prefs(_txt(a[0]) if a else "secret_prefs"))
    _add(ESP, "create", f"(Landroid/content/Context;Ljava/lang/String;{MK}Landroidx/security/crypto/EncryptedSharedPreferences$PrefKeyEncryptionScheme;Landroidx/security/crypto/EncryptedSharedPreferences$PrefValueEncryptionScheme;)Landroid/content/SharedPreferences;",
         lambda a: _open_enc_prefs(_txt(a[1]) if len(a) > 1 else "secret_prefs"))

    # ---- 5. Google Play Billing & Integrity Seams ---------------------------
    BC = "Lcom/android/billingclient/api/BillingClient;"
    BCB = "Lcom/android/billingclient/api/BillingClient$Builder;"
    BR = "Lcom/android/billingclient/api/BillingResult;"
    BP = "Lcom/android/billingclient/api/Purchase;"

    def _cur_br() -> JObject:
        st = _get_billing_state(stubs)
        return JObject(BR, {"_code": int(st.get("response_code", 0)), "_msg": "OK"})

    _add(BR, "getResponseCode", "()I", lambda a: int(a[0].fields.get("_code", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(BR, "getDebugMessage", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("_msg", "") if a and isinstance(a[0], JObject) else ""))
    _add(BP, "getPurchaseState", "()I", lambda a: int(a[0].fields.get("_state", 1)) if a and isinstance(a[0], JObject) else 1)
    _add(BP, "isAcknowledged", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_ack", 1)) else 0)
    _add(BP, "getPurchaseToken", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("_token", "") if a and isinstance(a[0], JObject) else ""))
    _add(BP, "getOriginalJson", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("_json", "{}") if a and isinstance(a[0], JObject) else "{}"))
    _add(BP, "getProducts", "()Ljava/util/List;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": [JString(a[0].fields.get("_product_id", "pro_monthly"))] if a and isinstance(a[0], JObject) else []}))
    _add(BP, "getSkus", "()Ljava/util/ArrayList;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": [JString(a[0].fields.get("_product_id", "pro_monthly"))] if a and isinstance(a[0], JObject) else []}))

    _add(BC, "newBuilder", f"(Landroid/content/Context;){BCB}", lambda a: JObject(BCB, {}))
    _add(BCB, "setListener", f"(Lcom/android/billingclient/api/PurchasesUpdatedListener;){BCB}", lambda a: (a[0].fields.__setitem__("_listener", a[1]), a[0])[1])
    _add(BCB, "enablePendingPurchases", f"(){BCB}", lambda a: a[0] if a else None)
    _add(BCB, "build", f"(){BC}", lambda a: JObject(BC, {"_listener": a[0].fields.get("_listener") if a and isinstance(a[0], JObject) else None, "_ready": False}))
    _add(BC, "isReady", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_ready", True)) else 0)

    def _bc_start(a):
        if a and isinstance(a[0], JObject):
            a[0].fields["_ready"] = True
        listener = a[1] if len(a) > 1 else None
        vm_cur = getattr(stubs, "vm", None)
        if isinstance(listener, JObject) and vm_cur is not None and hasattr(vm_cur, "invoke_virtual"):
            try:
                vm_cur.invoke_virtual(listener, "onBillingSetupFinished", f"({BR})V", (listener, _cur_br()))
            except Exception:
                pass
        return None

    def _bc_query_purchases(a):
        st = _get_billing_state(stubs)
        plist = [_make_purchase_obj(pid) for pid in (st.get("entitled_products") or ())] if st.get("pro_override") else []
        jlist = JObject("Ljava/util/ArrayList;", {"__items": plist})
        cb = a[2] if len(a) > 2 else None
        vm_cur = getattr(stubs, "vm", None)
        if isinstance(cb, JObject) and vm_cur is not None and hasattr(vm_cur, "invoke_virtual"):
            try:
                vm_cur.invoke_virtual(cb, "onQueryPurchasesResponse", f"({BR}Ljava/util/List;)V", (cb, _cur_br(), jlist))
            except Exception:
                pass
        return None

    _add(BC, "startConnection", "(Lcom/android/billingclient/api/BillingClientStateListener;)V", _bc_start)
    _add(BC, "queryPurchasesAsync", "(Lcom/android/billingclient/api/QueryPurchasesParams;Lcom/android/billingclient/api/PurchasesResponseListener;)V", _bc_query_purchases)
    _add(BC, "queryPurchasesAsync", "(Ljava/lang/String;Lcom/android/billingclient/api/PurchasesResponseListener;)V", _bc_query_purchases)
    _add(BC, "acknowledgePurchase", "(Lcom/android/billingclient/api/AcknowledgePurchaseParams;Lcom/android/billingclient/api/AcknowledgePurchaseResponseListener;)V",
         lambda a: getattr(stubs, "vm", None).invoke_virtual(a[2], "onAcknowledgePurchaseResponse", f"({BR})V", (a[2], _cur_br())) if len(a) > 2 and isinstance(a[2], JObject) and getattr(stubs, "vm", None) else None)
    _add(BC, "launchBillingFlow", f"(Landroid/app/Activity;Lcom/android/billingclient/api/BillingFlowParams;){BR}",
         lambda a: _cur_br())

    # Play Integrity
    IM = "Lcom/google/android/play/core/integrity/IntegrityManager;"
    IMF = "Lcom/google/android/play/core/integrity/IntegrityManagerFactory;"
    _add(IMF, "create", f"(Landroid/content/Context;){IM}", lambda a: JObject(IM, {}))
    _add(IM, "requestIntegrityToken", "(Lcom/google/android/play/core/integrity/IntegrityTokenRequest;)Lcom/google/android/gms/tasks/Task;",
         lambda a: JObject("Lcom/google/android/gms/tasks/Task;", {"$done": True, "$res": JObject("Lcom/google/android/play/core/integrity/IntegrityTokenResponse;", {"token": JString("mock-play-integrity-jwt")})}))
    _add("Lcom/google/android/play/core/integrity/IntegrityTokenResponse;", "token", "()Ljava/lang/String;",
         lambda a: a[0].fields.get("token", JString("mock-play-integrity-jwt")) if a and isinstance(a[0], JObject) else JString("mock-play-integrity-jwt"))
