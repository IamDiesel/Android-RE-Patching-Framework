
### 🔧 RE-Patch-Report (PID-20260807-210617)
* **Name:** Ebene 3 - Null-Patch (Mutex intakt)
* **Testergebnis:** Crash
  * **Patch 1:** RAM: `0x00830b08` | Hex: `00 00 80 52 c0 03 5f d6`

**Beobachtung:**
Kippy App startet kurz und wird dann direkt wieder beendet. Man sieht das Kippy Logo für den Bruchteil einer Sekunde

---

### 🔧 RE-Patch-Report (PID-20260807-214535)
* **Name:** Ebene 1 - SSL Verify True Patch
* **Testergebnis:** No Internet
  * **Patch 1:** RAM: `0x00853d28` | Hex: `20 00 80 52 c0 03 5f d6`

**Beobachtung:**
Die App startet sauber auf. Wenn ich in das Feld für den Nutzernamen klicke und ihn eingebe wird sogar über google mein Passwort automatisch eingetragen. Das hatte ich bisher nur bei der original app. Dann allerdings wenn ich auf "anmelden" klicke erhalte ich die Meldung "Keine Internetverbindung". Im Unterschied zu bisherigen Versuchen kommt diese Meldung auch wirklich nur, wenn ich auf den "Anmelden" Button klicke. Bisher war es sonst so, dass diese Meldung in zyklischen Abständen alle 2-3 Sekunden angezeigt wurde.

---

### 🔧 RE-Patch-Report (PID-20260807-220428)
* **Name:** Ebene 1 - Epilog True Patch (Final)
* **Testergebnis:** No Internet
  * **Patch 1:** RAM: `0x00853eac` | Hex: `20 00 80 52`

**Beobachtung:**
Wenn http Toolkit nicht aktiv ist:
App funktioniert einwandfrei
Wenn http Toolkit aktiv ist:
Startscreen ok
Anmeldeseite erscheint
Google Passwort Autofill OK
Klick auf Button "Anmelden" NICHT OK - Es erscheint einmalig die Meldung "Keine Internetverbindung"
---
Wenn ich ich http toolkit nicht aktiv habe, mich anmelde, dann http toolkit aktiviere, dann bin ich eingeloggt, aber es erscheint zyklisch die Meldung "kein Internet"
Wenn ich dann die App schließe und wieder öffne erhalte ich einen "Add new pet" Screen (Scheinbar bin ich angemeldet) sprich kein LoginFenster. Es ploppt auch mehrmals die Info "Network Error" auf. Interessant dass sie dieses mal auf englisch und nicht auf deutsch ist.

---

### 🔧 RE-Patch-Report (PID-20260807-224030)
* **Name:** Ebene 1 - Epilog X509_V_OK (0)
* **Testergebnis:** Success
  * **Patch 1:** RAM: `0x00853eac` | Hex: `00 00 80 52`

**Beobachtung:**
toolkit aktiv:
App startet, Anmeldung google autofill wird durchgeführt
Bei klick auf Anmelden erscheint der Fehler "Keine Internetverbindung"

Http toolkit deaktivert (APP noch offen) Weiterhin keine INternetverbindung

Neustart Kippy: Gleiches Verhalten wie zuvor (Autofill ok aber Anmeldung nicht möglich->Meldung "Keine Internetverbindung")

---

### 🔧 RE-Patch-Report (PID-20260807-224917)
* **Name:** 
* **Testergebnis:** Success
  * **Patch 1:** RAM: `0x00853eac` | Hex: `00 00 80 52`

**Beobachtung:**
toolkit aktiv:
App startet, Anmeldung google autofill wird durchgeführt
Bei klick auf Anmelden erscheint der Fehler "Keine Internetverbindung"

Http toolkit deaktivert (APP noch offen) Weiterhin keine INternetverbindung

Neustart Kippy: Gleiches Verhalten wie zuvor (Autofill ok aber Anmeldung nicht möglich->Meldung "Keine Internetverbindung")

---

### 🔧 RE-Patch-Report (PID-20260807-230257)
* **Name:** Die Flutter-Callbacks (Double-Patch)
* **Testergebnis:** Success
  * **Patch 1:** RAM: `0x0099b738` | Hex: `20 00 80 52 c0 03 5f d6`
  * **Patch 2:** RAM: `0x0099ba40` | Hex: `00 00 80 52 c0 03 5f d6`

**Beobachtung:**
Anmeldung i.O. mit http tool aktiv
Tracking scheint zu funktionieren.
Restliche Funktionen lassen sich aufrufen

---

### 🔧 RE-Patch-Report (PID-20260815-005806)
* **App:** digifit.virtuagym.client.android (v12.4.2)
* **Name:** Lucky Try Virtuagym
* **Testergebnis:** No Internet

**Beobachtung:**
Installation i.O.
Ohne Tunnel: Alles i.O.
Mit Tunnel: Teilweise kein Internet - über Ich->Boxring Stuttgart > Mein Club Konto, wird eine Art inApp Browser geöffnet. Hier kommen Traces an.

---

### 🔧 RE-Patch-Report (PID-20260815-011257)
* **App:** digifit.virtuagym.client.android (v)
* **Name:** lucky try#2 -virtuagym
* **Testergebnis:** Crash

  * **Smali Patch 1** in Datei: `smali_classes7/okhttp3/OkHttpClient.smali`
  ```smali
.method public constructor <init>(Lokhttp3/OkHttpClient$Builder;)V
    .locals 7
    return-void
    .param p1    # Lokhttp3/OkHttpClient$Builder;
        .annotation build Lorg/jetbrains/annotations/NotNull;
        .end annotation
    .end param

    .line 1
    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    .line 2
    iget-object v0, p1, Lokhttp3/OkHttpClient$Builder;->a:Lokhttp3/Dispatcher;

    .line 3
    iput-object v0, p0, Lokhttp3/OkHttpClient;->a:Lokhttp3/Dispatcher;

    .line 4
    iget-object v0, p1, Lokhttp3/OkHttpClient$Builder;->c:Ljava/util/ArrayList;

    .line 5
    invoke-static {v0}, Lokhttp3/internal/_UtilJvmKt;->j(Ljava/util/List;)Ljava/util/List;

    move-result-object v0

    iput-object v0, p0, Lokhttp3/OkHttpClient;->b:Ljava/util/List;

    .line 6
    iget-object v0, p1, Lokhttp3/OkHttpClient$Builder;->d:Ljava/util/ArrayList;

    .line 7
    invoke-static {v0}, Lokhttp3/internal/_UtilJvmKt;->j(Ljava/util/List;)Ljava/util/List;

    move-result-object v0

    iput-object v0, p0, Lokhttp3/OkHttpClient;->c:Ljava/util/List;

    .line 8
    iget-object v0, p1, Lokhttp3/OkHttpClient$Builder;->e:Lm5/b;

    .line 9
    iput-object v0, p0, Lokhttp3/OkHttpClient;->d:Lm5/b;

    .line 10
    iget-boolean v0, p1, Lokhttp3/OkHttpClient$Builder;->f:Z

    .line 11
    iput-boolean v0, p0, Lokhttp3/OkHttpClient;->e:Z

    .line 12
    iget-boolean v0, p1, Lokhttp3/OkHttpClient$Builder;->g:Z

    .line 13
    iput-boolean v0, p0, Lokhttp3/OkHttpClient;->f:Z

    .line 14
    iget-object v0, p1, Lokhttp3/OkHttpClient$Builder;->h:Lokhttp3/Authenticator;

    .line 15
    iput-object v0, p0, Lokhttp3/OkHttpClient;->g:Lokhttp3/Authenticator;

    .line 16
    iget-boolean v0, p1, Lokhttp3/OkHttpClient$Builder;->i:Z

    .line 17
    iput-boolean v0, p0, Lokhttp3/OkHttpClient;->h:Z

    .line 18
    iget-boolean v0, p1, Lokhttp3/OkHttpClient$Builder;->j:Z

    .line 19
    iput-boolean v0, p0, Lokhttp3/OkHttpClient;->i:Z

    .line 20
    iget-object v0, p1, Lokhttp3/OkHttpClient$Builder;->k:Lokhttp3/CookieJar;

    .line 21
    iput-object v0, p0, Lokhttp3/OkHttpClient;->j:Lokhttp3/CookieJar;

    .line 22
    iget-object v0, p1, Lokhttp3/OkHttpClient$Builder;->l:Lokhttp3/Cache;

    .line 23
    iput-object v0, p0, Lokhttp3/OkHttpClient;->k:Lokhttp3/Cache;

    .line 24
    iget-object v0, p1, Lokhttp3/OkHttpClient$Builder;->m:Lokhttp3/Dns;

    .line 25
    iput-object v0, p0, Lokhttp3/OkHttpClient;->l:Lokhttp3/Dns;

    .line 26
    iget-object v0, p1, Lokhttp3/OkHttpClient$Builder;->n:Ljava/net/ProxySelector;

    if-nez v0, :cond_0

    .line 27
    invoke-static {}, Ljava/net/ProxySelector;->getDefault()Ljava/net/ProxySelector;

    move-result-object v0

    if-nez v0, :cond_0

    sget-object v0, Lokhttp3/internal/proxy/NullProxySelector;->a:Lokhttp3/internal/proxy/NullProxySelector;

    .line 28
    :cond_0
    iput-object v0, p0, Lokhttp3/OkHttpClient;->m:Ljava/net/ProxySelector;

    .line 29
    iget-object v0, p1, Lokhttp3/OkHttpClient$Builder;->o:Lokhttp3/Authenticator;

    .line 30
    iput-object v0, p0, Lokhttp3/OkHttpClient;->n:Lokhttp3/Authenticator;

    .line 31
    iget-object v0, p1, Lokhttp3/OkHttpClient$Builder;->p:Ljavax/net/SocketFactory;

    .line 32
    iput-object v0, p0, Lokhttp3/OkHttpClient;->o:Ljavax/net/SocketFactory;

    .line 33
    iget-object v0, p1, Lokhttp3/OkHttpClient$Builder;->s:Ljava/util/List;

    .line 34
    iput-object v0, p0, Lokhttp3/OkHttpClient;->r:Ljava/util/List;

    .line 35
    iget-object v1, p1, Lokhttp3/OkHttpClient$Builder;->t:Ljava/util/List;

    .line 36
    iput-object v1, p0, Lokhttp3/OkHttpClient;->s:Ljava/util/List;

    .line 37
    iget-object v1, p1, Lokhttp3/OkHttpClient$Builder;->u:Ljavax/net/ssl/HostnameVerifier;

    .line 38
    iput-object v1, p0, Lokhttp3/OkHttpClient;->t:Ljavax/net/ssl/HostnameVerifier;

    .line 39
    iget v1, p1, Lokhttp3/OkHttpClient$Builder;->x:I

    .line 40
    iput v1, p0, Lokhttp3/OkHttpClient;->w:I

    .line 41
    iget v1, p1, Lokhttp3/OkHttpClient$Builder;->y:I

    .line 42
    iput v1, p0, Lokhttp3/OkHttpClient;->x:I

    .line 43
    iget v1, p1, Lokhttp3/OkHttpClient$Builder;->z:I

    .line 44
    iput v1, p0, Lokhttp3/OkHttpClient;->y:I

    .line 45
    iget v1, p1, Lokhttp3/OkHttpClient$Builder;->A:I

    .line 46
    iput v1, p0, Lokhttp3/OkHttpClient;->z:I

    .line 47
    iget-wide v1, p1, Lokhttp3/OkHttpClient$Builder;->B:J

    .line 48
    iput-wide v1, p0, Lokhttp3/OkHttpClient;->A:J

    .line 49
    iget-object v1, p1, Lokhttp3/OkHttpClient$Builder;->C:Lokhttp3/internal/connection/RouteDatabase;

    if-nez v1, :cond_1

    .line 50
    new-instance v1, Lokhttp3/internal/connection/RouteDatabase;

    invoke-direct {v1}, Lokhttp3/internal/connection/RouteDatabase;-><init>()V

    :cond_1
    iput-object v1, p0, Lokhttp3/OkHttpClient;->B:Lokhttp3/internal/connection/RouteDatabase;

    .line 51
    iget-object v1, p1, Lokhttp3/OkHttpClient$Builder;->D:Lokhttp3/internal/concurrent/TaskRunner;

    if-nez v1, :cond_2

    .line 52
    sget-object v1, Lokhttp3/internal/concurrent/TaskRunner;->I:Lokhttp3/internal/concurrent/TaskRunner;

    :cond_2
    iput-object v1, p0, Lokhttp3/OkHttpClient;->C:Lokhttp3/internal/concurrent/TaskRunner;

    .line 53
    iget-object v1, p1, Lokhttp3/OkHttpClient$Builder;->b:Lokhttp3/ConnectionPool;

    if-nez v1, :cond_3

    .line 54
    new-instance v1, Lokhttp3/ConnectionPool;

    invoke-direct {v1}, Lokhttp3/ConnectionPool;-><init>()V

    .line 55
    iput-object v1, p1, Lokhttp3/OkHttpClient$Builder;->b:Lokhttp3/ConnectionPool;

    .line 56
    :cond_3
    iput-object v1, p0, Lokhttp3/OkHttpClient;->D:Lokhttp3/ConnectionPool;

    const/4 v1, 0x0

    if-eqz v0, :cond_4

    .line 57
    invoke-interface {v0}, Ljava/util/Collection;->isEmpty()Z

    move-result v2

    if-eqz v2, :cond_4

    goto/16 :goto_2

    .line 58
    :cond_4
    invoke-interface {v0}, Ljava/lang/Iterable;->iterator()Ljava/util/Iterator;

    move-result-object v0

    :cond_5
    invoke-interface {v0}, Ljava/util/Iterator;->hasNext()Z

    move-result v2

    if-eqz v2, :cond_a

    invoke-interface {v0}, Ljava/util/Iterator;->next()Ljava/lang/Object;

    move-result-object v2

    check-cast v2, Lokhttp3/ConnectionSpec;

    .line 59
    iget-boolean v2, v2, Lokhttp3/ConnectionSpec;->a:Z

    if-eqz v2, :cond_5

    .line 60
    iget-object v0, p1, Lokhttp3/OkHttpClient$Builder;->q:Ljavax/net/ssl/SSLSocketFactory;

    if-eqz v0, :cond_7

    .line 61
    iput-object v0, p0, Lokhttp3/OkHttpClient;->p:Ljavax/net/ssl/SSLSocketFactory;

    .line 62
    iget-object v0, p1, Lokhttp3/OkHttpClient$Builder;->w:Lokhttp3/internal/tls/CertificateChainCleaner;

    .line 63
    invoke-static {v0}, Lkotlin/jvm/internal/Intrinsics;->d(Ljava/lang/Object;)V

    iput-object v0, p0, Lokhttp3/OkHttpClient;->v:Lokhttp3/internal/tls/CertificateChainCleaner;

    .line 64
    iget-object v2, p1, Lokhttp3/OkHttpClient$Builder;->r:Ljavax/net/ssl/X509TrustManager;

    .line 65
    invoke-static {v2}, Lkotlin/jvm/internal/Intrinsics;->d(Ljava/lang/Object;)V

    iput-object v2, p0, Lokhttp3/OkHttpClient;->q:Ljavax/net/ssl/X509TrustManager;

    .line 66
    iget-object p1, p1, Lokhttp3/OkHttpClient$Builder;->v:Lokhttp3/CertificatePinner;

    .line 67
    invoke-virtual {p1}, Ljava/lang/Object;->getClass()Ljava/lang/Class;

    .line 68
    iget-object v2, p1, Lokhttp3/CertificatePinner;->b:Lokhttp3/internal/tls/CertificateChainCleaner;

    invoke-static {v2, v0}, Lkotlin/jvm/internal/Intrinsics;->b(Ljava/lang/Object;Ljava/lang/Object;)Z

    move-result v2

    if-eqz v2, :cond_6

    goto :goto_0

    .line 69
    :cond_6
    new-instance v2, Lokhttp3/CertificatePinner;

    iget-object p1, p1, Lokhttp3/CertificatePinner;->a:Ljava/util/Set;

    invoke-direct {v2, p1, v0}, Lokhttp3/CertificatePinner;-><init>(Ljava/util/Set;Lokhttp3/internal/tls/CertificateChainCleaner;)V

    move-object p1, v2

    .line 70
    :goto_0
    iput-object p1, p0, Lokhttp3/OkHttpClient;->u:Lokhttp3/CertificatePinner;

    goto/16 :goto_3

    .line 71
    :cond_7
    sget-object v0, Lokhttp3/internal/platform/Platform;->a:Lokhttp3/internal/platform/Platform$Companion;

    invoke-virtual {v0}, Ljava/lang/Object;->getClass()Ljava/lang/Class;

    .line 72
    sget-object v2, Lokhttp3/internal/platform/Platform;->b:Lokhttp3/internal/platform/Platform;

    .line 73
    invoke-virtual {v2}, Ljava/lang/Object;->getClass()Ljava/lang/Class;

    .line 74
    invoke-static {}, Ljavax/net/ssl/TrustManagerFactory;->getDefaultAlgorithm()Ljava/lang/String;

    move-result-object v2

    .line 75
    invoke-static {v2}, Ljavax/net/ssl/TrustManagerFactory;->getInstance(Ljava/lang/String;)Ljavax/net/ssl/TrustManagerFactory;

    move-result-object v2

    .line 76
    invoke-virtual {v2, v1}, Ljavax/net/ssl/TrustManagerFactory;->init(Ljava/security/KeyStore;)V

    .line 77
    invoke-virtual {v2}, Ljavax/net/ssl/TrustManagerFactory;->getTrustManagers()[Ljavax/net/ssl/TrustManager;

    move-result-object v2

    invoke-static {v2}, Lkotlin/jvm/internal/Intrinsics;->d(Ljava/lang/Object;)V

    .line 78
    array-length v3, v2

    const/4 v4, 0x1

    if-ne v3, v4, :cond_9

    const/4 v3, 0x0

    aget-object v5, v2, v3

    instance-of v6, v5, Ljavax/net/ssl/X509TrustManager;

    if-eqz v6, :cond_9

    .line 79
    check-cast v5, Ljavax/net/ssl/X509TrustManager;

    .line 80
    iput-object v5, p0, Lokhttp3/OkHttpClient;->q:Ljavax/net/ssl/X509TrustManager;

    .line 81
    sget-object v2, Lokhttp3/internal/platform/Platform;->b:Lokhttp3/internal/platform/Platform;

    .line 82
    invoke-virtual {v2}, Ljava/lang/Object;->getClass()Ljava/lang/Class;

    .line 83
    :try_start_0
    invoke-virtual {v2}, Lokhttp3/internal/platform/Platform;->l()Ljavax/net/ssl/SSLContext;

    move-result-object v2

    .line 84
    new-array v4, v4, [Ljavax/net/ssl/TrustManager;

    aput-object v5, v4, v3

    invoke-virtual {v2, v1, v4, v1}, Ljavax/net/ssl/SSLContext;->init([Ljavax/net/ssl/KeyManager;[Ljavax/net/ssl/TrustManager;Ljava/security/SecureRandom;)V

    .line 85
    invoke-virtual {v2}, Ljavax/net/ssl/SSLContext;->getSocketFactory()Ljavax/net/ssl/SSLSocketFactory;

    move-result-object v2

    const-string v3, "getSocketFactory(...)"

    invoke-static {v2, v3}, Lkotlin/jvm/internal/Intrinsics;->f(Ljava/lang/Object;Ljava/lang/String;)V
    :try_end_0
    .catch Ljava/security/GeneralSecurityException; {:try_start_0 .. :try_end_0} :catch_0

    .line 86
    iput-object v2, p0, Lokhttp3/OkHttpClient;->p:Ljavax/net/ssl/SSLSocketFactory;

    .line 87
    sget-object v2, Lokhttp3/internal/tls/CertificateChainCleaner;->a:Lokhttp3/internal/tls/CertificateChainCleaner$Companion;

    invoke-virtual {v2}, Ljava/lang/Object;->getClass()Ljava/lang/Class;

    .line 88
    invoke-virtual {v0}, Ljava/lang/Object;->getClass()Ljava/lang/Class;

    .line 89
    sget-object v0, Lokhttp3/internal/platform/Platform;->b:Lokhttp3/internal/platform/Platform;

    .line 90
    invoke-virtual {v0, v5}, Lokhttp3/internal/platform/Platform;->c(Ljavax/net/ssl/X509TrustManager;)Lokhttp3/internal/tls/CertificateChainCleaner;

    move-result-object v0

    .line 91
    iput-object v0, p0, Lokhttp3/OkHttpClient;->v:Lokhttp3/internal/tls/CertificateChainCleaner;

    .line 92
    iget-object p1, p1, Lokhttp3/OkHttpClient$Builder;->v:Lokhttp3/CertificatePinner;

    .line 93
    invoke-virtual {p1}, Ljava/lang/Object;->getClass()Ljava/lang/Class;

    .line 94
    iget-object v2, p1, Lokhttp3/CertificatePinner;->b:Lokhttp3/internal/tls/CertificateChainCleaner;

    invoke-static {v2, v0}, Lkotlin/jvm/internal/Intrinsics;->b(Ljava/lang/Object;Ljava/lang/Object;)Z

    move-result v2

    if-eqz v2, :cond_8

    goto :goto_1

    .line 95
    :cond_8
    new-instance v2, Lokhttp3/CertificatePinner;

    iget-object p1, p1, Lokhttp3/CertificatePinner;->a:Ljava/util/Set;

    invoke-direct {v2, p1, v0}, Lokhttp3/CertificatePinner;-><init>(Ljava/util/Set;Lokhttp3/internal/tls/CertificateChainCleaner;)V

    move-object p1, v2

    .line 96
    :goto_1
    iput-object p1, p0, Lokhttp3/OkHttpClient;->u:Lokhttp3/CertificatePinner;

    goto :goto_3

    :catch_0
    move-exception p1

    .line 97
    new-instance v0, Ljava/lang/AssertionError;

    new-instance v1, Ljava/lang/StringBuilder;

    const-string v2, "No System TLS: "

    invoke-direct {v1, v2}, Ljava/lang/StringBuilder;-><init>(Ljava/lang/String;)V

    invoke-virtual {v1, p1}, Ljava/lang/StringBuilder;->append(Ljava/lang/Object;)Ljava/lang/StringBuilder;

    invoke-virtual {v1}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object v1

    invoke-direct {v0, v1, p1}, Ljava/lang/AssertionError;-><init>(Ljava/lang/String;Ljava/lang/Throwable;)V

    throw v0

    .line 98
    :cond_9
    invoke-static {v2}, Ljava/util/Arrays;->toString([Ljava/lang/Object;)Ljava/lang/String;

    move-result-object p1

    const-string v0, "toString(...)"

    invoke-static {p1, v0}, Lkotlin/jvm/internal/Intrinsics;->f(Ljava/lang/Object;Ljava/lang/String;)V

    const-string v0, "Unexpected default trust managers: "

    invoke-virtual {v0, p1}, Ljava/lang/String;->concat(Ljava/lang/String;)Ljava/lang/String;

    move-result-object p1

    .line 99
    new-instance v0, Ljava/lang/IllegalStateException;

    invoke-virtual {p1}, Ljava/lang/Object;->toString()Ljava/lang/String;

    move-result-object p1

    invoke-direct {v0, p1}, Ljava/lang/IllegalStateException;-><init>(Ljava/lang/String;)V

    throw v0

    .line 100
    :cond_a
    :goto_2
    iput-object v1, p0, Lokhttp3/OkHttpClient;->p:Ljavax/net/ssl/SSLSocketFactory;

    .line 101
    iput-object v1, p0, Lokhttp3/OkHttpClient;->v:Lokhttp3/internal/tls/CertificateChainCleaner;

    .line 102
    iput-object v1, p0, Lokhttp3/OkHttpClient;->q:Ljavax/net/ssl/X509TrustManager;

    .line 103
    sget-object p1, Lokhttp3/CertificatePinner;->d:Lokhttp3/CertificatePinner;

    iput-object p1, p0, Lokhttp3/OkHttpClient;->u:Lokhttp3/CertificatePinner;

    .line 104
    :goto_3
    iget-object p1, p0, Lokhttp3/OkHttpClient;->q:Ljavax/net/ssl/X509TrustManager;

    iget-object v0, p0, Lokhttp3/OkHttpClient;->v:Lokhttp3/internal/tls/CertificateChainCleaner;

    iget-object v2, p0, Lokhttp3/OkHttpClient;->p:Ljavax/net/ssl/SSLSocketFactory;

    iget-object v3, p0, Lokhttp3/OkHttpClient;->c:Ljava/util/List;

    iget-object v4, p0, Lokhttp3/OkHttpClient;->b:Ljava/util/List;

    const-string v5, "null cannot be cast to non-null type kotlin.collections.List<okhttp3.Interceptor?>"

    invoke-static {v4, v5}, Lkotlin/jvm/internal/Intrinsics;->e(Ljava/lang/Object;Ljava/lang/String;)V

    invoke-interface {v4, v1}, Ljava/util/List;->contains(Ljava/lang/Object;)Z

    move-result v6

    if-nez v6, :cond_16

    .line 105
    invoke-static {v3, v5}, Lkotlin/jvm/internal/Intrinsics;->e(Ljava/lang/Object;Ljava/lang/String;)V

    invoke-interface {v3, v1}, Ljava/util/List;->contains(Ljava/lang/Object;)Z

    move-result v1

    if-nez v1, :cond_15

    .line 106
    iget-object v1, p0, Lokhttp3/OkHttpClient;->r:Ljava/util/List;

    if-eqz v1, :cond_b

    .line 107
    invoke-interface {v1}, Ljava/util/Collection;->isEmpty()Z

    move-result v3

    if-eqz v3, :cond_b

    goto :goto_4

    .line 108
    :cond_b
    invoke-interface {v1}, Ljava/lang/Iterable;->iterator()Ljava/util/Iterator;

    move-result-object v1

    :cond_c
    invoke-interface {v1}, Ljava/util/Iterator;->hasNext()Z

    move-result v3

    if-eqz v3, :cond_10

    invoke-interface {v1}, Ljava/util/Iterator;->next()Ljava/lang/Object;

    move-result-object v3

    check-cast v3, Lokhttp3/ConnectionSpec;

    .line 109
    iget-boolean v3, v3, Lokhttp3/ConnectionSpec;->a:Z

    if-eqz v3, :cond_c

    if-eqz v2, :cond_f

    if-eqz v0, :cond_e

    if-eqz p1, :cond_d

    return-void

    .line 110
    :cond_d
    new-instance p1, Ljava/lang/IllegalStateException;

    const-string v0, "x509TrustManager == null"

    invoke-direct {p1, v0}, Ljava/lang/IllegalStateException;-><init>(Ljava/lang/String;)V

    throw p1

    .line 111
    :cond_e
    new-instance p1, Ljava/lang/IllegalStateException;

    const-string v0, "certificateChainCleaner == null"

    invoke-direct {p1, v0}, Ljava/lang/IllegalStateException;-><init>(Ljava/lang/String;)V

    throw p1

    .line 112
    :cond_f
    new-instance p1, Ljava/lang/IllegalStateException;

    const-string v0, "sslSocketFactory == null"

    invoke-direct {p1, v0}, Ljava/lang/IllegalStateException;-><init>(Ljava/lang/String;)V

    throw p1

    .line 113
    :cond_10
    :goto_4
    const-string v1, "Check failed."

    if-nez v2, :cond_14

    if-nez v0, :cond_13

    if-nez p1, :cond_12

    .line 114
    iget-object p1, p0, Lokhttp3/OkHttpClient;->u:Lokhttp3/CertificatePinner;

    sget-object v0, Lokhttp3/CertificatePinner;->d:Lokhttp3/CertificatePinner;

    invoke-static {p1, v0}, Lkotlin/jvm/internal/Intrinsics;->b(Ljava/lang/Object;Ljava/lang/Object;)Z

    move-result p1

    if-eqz p1, :cond_11

    sget-object p1, Lkotlin/Unit;->a:Lkotlin/Unit;

    return-void

    :cond_11
    new-instance p1, Ljava/lang/IllegalStateException;

    invoke-direct {p1, v1}, Ljava/lang/IllegalStateException;-><init>(Ljava/lang/String;)V

    throw p1

    .line 115
    :cond_12
    new-instance p1, Ljava/lang/IllegalStateException;

    invoke-direct {p1, v1}, Ljava/lang/IllegalStateException;-><init>(Ljava/lang/String;)V

    throw p1

    .line 116
    :cond_13
    new-instance p1, Ljava/lang/IllegalStateException;

    invoke-direct {p1, v1}, Ljava/lang/IllegalStateException;-><init>(Ljava/lang/String;)V

    throw p1

    .line 117
    :cond_14
    new-instance p1, Ljava/lang/IllegalStateException;

    invoke-direct {p1, v1}, Ljava/lang/IllegalStateException;-><init>(Ljava/lang/String;)V

    throw p1

    .line 118
    :cond_15
    new-instance p1, Ljava/lang/StringBuilder;

    const-string v0, "Null network interceptor: "

    invoke-direct {p1, v0}, Ljava/lang/StringBuilder;-><init>(Ljava/lang/String;)V

    invoke-virtual {p1, v3}, Ljava/lang/StringBuilder;->append(Ljava/lang/Object;)Ljava/lang/StringBuilder;

    invoke-virtual {p1}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object p1

    .line 119
    new-instance v0, Ljava/lang/IllegalStateException;

    invoke-virtual {p1}, Ljava/lang/Object;->toString()Ljava/lang/String;

    move-result-object p1

    invoke-direct {v0, p1}, Ljava/lang/IllegalStateException;-><init>(Ljava/lang/String;)V

    throw v0

    .line 120
    :cond_16
    new-instance p1, Ljava/lang/StringBuilder;

    const-string v0, "Null interceptor: "

    invoke-direct {p1, v0}, Ljava/lang/StringBuilder;-><init>(Ljava/lang/String;)V

    invoke-virtual {p1, v4}, Ljava/lang/StringBuilder;->append(Ljava/lang/Object;)Ljava/lang/StringBuilder;

    invoke-virtual {p1}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object p1

    .line 121
    new-instance v0, Ljava/lang/IllegalStateException;

    invoke-virtual {p1}, Ljava/lang/Object;->toString()Ljava/lang/String;

    move-result-object p1

    invoke-direct {v0, p1}, Ljava/lang/IllegalStateException;-><init>(Ljava/lang/String;)V

    throw v0
.end method
  ```

**Beobachtung:**
App Crasht (Kein UNterschied ob prxy aktiv oder nicht)

---

### 🔧 RE-Patch-Report (PID-20260816-002911)
* **App:** digifit.virtuagym.client.android (v)
* **Name:** EXT TRUST MANIFEST
* **Testergebnis:** No Internet

**Beobachtung:**
Anmeldung erfolgreich
Laden des App Dashboards erfolgreich
Laden des Kalenders erfolgreich
Laden einzelner Termie war nicht erfolgreich, die App wurde resettet
Netzwerktraffic konnte aber aufgezeichnet werden

---

### 🔧 RE-Patch-Report (PID-20260817-030833)
* **App:** digifit.virtuagym.client.android (v12.4.2)
* **Name:** Success VIrtuagym
* **Testergebnis:** Success

**Beobachtung:**
Smali Patch mit APKEditor Native.
Patches:
{
        "name": "OKHostnameVerifier + CertificateChainCleaner VS",
        "comment": "Patch mit apkeditor erstellt",
        "date": "2026-08-17 02:57:01",
        "patches": [
            {
                "type": "smali",
                "file": "smali/classes7/okhttp3/internal/tls/OkHostnameVerifier.smali",
                "orig": ".method public final verify(Ljava/lang/String;Ljavax/net/ssl/SSLSession;)Z\n    .locals 2\n    .param p1    # Ljava/lang/String;\n        .annotation build Lorg/jetbrains/annotations/NotNull;\n        .end annotation\n    .end param\n    .param p2    # Ljavax/net/ssl/SSLSession;\n        .annotation build Lorg/jetbrains/annotations/NotNull;\n        .end annotation\n    .end param\n\n    const-string v0, \"host\"\n\n    invoke-static {p1, v0}, Lkotlin/jvm/internal/Intrinsics;->g(Ljava/lang/Object;Ljava/lang/String;)V\n\n    const-string v0, \"session\"\n\n    invoke-static {p2, v0}, Lkotlin/jvm/internal/Intrinsics;->g(Ljava/lang/Object;Ljava/lang/String;)V\n\n    invoke-static {p1}, Lokhttp3/internal/tls/OkHostnameVerifier;->b(Ljava/lang/String;)Z\n\n    move-result v0\n\n    const/4 v1, 0x0\n\n    if-nez v0, :cond_0\n\n    goto :goto_0\n\n    :cond_0\n    :try_start_0\n    invoke-interface {p2}, Ljavax/net/ssl/SSLSession;->getPeerCertificates()[Ljava/security/cert/Certificate;\n\n    move-result-object p2\n\n    aget-object p2, p2, v1\n\n    const-string v0, \"null cannot be cast to non-null type java.security.cert.X509Certificate\"\n\n    invoke-static {p2, v0}, Lkotlin/jvm/internal/Intrinsics;->e(Ljava/lang/Object;Ljava/lang/String;)V\n\n    check-cast p2, Ljava/security/cert/X509Certificate;\n\n    invoke-static {p1, p2}, Lokhttp3/internal/tls/OkHostnameVerifier;->c(Ljava/lang/String;Ljava/security/cert/X509Certificate;)Z\n\n    move-result p1\n\n    :try_end_0\n    .catch Ljavax/net/ssl/SSLException; {:try_start_0 .. :try_end_0} :catch_0\n\n    return p1\n\n    :catch_0\n    :goto_0\n    return v1\n.end method",
                "edit": ".method public final verify(Ljava/lang/String;Ljavax/net/ssl/SSLSession;)Z\n    .locals 2\n\n    const/4 v0, 0x1\n    return v0\n.end method"
            },
            {
                "type": "smali",
                "file": "smali/classes7/okhttp3/internal/platform/android/AndroidCertificateChainCleaner.smali",
                "orig": ".method public final a(Ljava/lang/String;Ljava/util/List;)Ljava/util/List;\n    .locals 2\n    .param p1    # Ljava/lang/String;\n        .annotation build Lorg/jetbrains/annotations/NotNull;\n        .end annotation\n    .end param\n    .param p2    # Ljava/util/List;\n        .annotation build Lorg/jetbrains/annotations/NotNull;\n        .end annotation\n    .end param\n    .annotation build Lokhttp3/internal/SuppressSignatureCheck;\n    .end annotation\n    .annotation build Lorg/jetbrains/annotations/NotNull;\n    .end annotation\n\n    const-string v0, \"chain\"\n\n    invoke-static {p2, v0}, Lkotlin/jvm/internal/Intrinsics;->g(Ljava/lang/Object;Ljava/lang/String;)V\n\n    const-string v0, \"hostname\"\n\n    invoke-static {p1, v0}, Lkotlin/jvm/internal/Intrinsics;->g(Ljava/lang/Object;Ljava/lang/String;)V\n\n    const/4 v0, 0x0\n\n    new-array v0, v0, [Ljava/security/cert/X509Certificate;\n\n    invoke-interface {p2, v0}, Ljava/util/Collection;->toArray([Ljava/lang/Object;)[Ljava/lang/Object;\n\n    move-result-object p2\n\n    check-cast p2, [Ljava/security/cert/X509Certificate;\n\n    :try_start_0\n    iget-object v0, p0, Lokhttp3/internal/platform/android/AndroidCertificateChainCleaner;->c:Landroid/net/http/X509TrustManagerExtensions;\n\n    const-string v1, \"RSA\"\n\n    invoke-virtual {v0, p2, v1, p1}, Landroid/net/http/X509TrustManagerExtensions;->checkServerTrusted([Ljava/security/cert/X509Certificate;Ljava/lang/String;Ljava/lang/String;)Ljava/util/List;\n\n    move-result-object p1\n\n    const-string p2, \"checkServerTrusted(...)\"\n\n    invoke-static {p1, p2}, Lkotlin/jvm/internal/Intrinsics;->f(Ljava/lang/Object;Ljava/lang/String;)V\n\n    :try_end_0\n    .catch Ljava/security/cert/CertificateException; {:try_start_0 .. :try_end_0} :catch_0\n\n    return-object p1\n\n    :catch_0\n    move-exception p1\n\n    new-instance p2, Ljavax/net/ssl/SSLPeerUnverifiedException;\n\n    invoke-virtual {p1}, Ljava/lang/Throwable;->getMessage()Ljava/lang/String;\n\n    move-result-object v0\n\n    invoke-direct {p2, v0}, Ljavax/net/ssl/SSLPeerUnverifiedException;-><init>(Ljava/lang/String;)V\n\n    invoke-virtual {p2, p1}, Ljava/lang/Throwable;->initCause(Ljava/lang/Throwable;)Ljava/lang/Throwable;\n\n    throw p2\n.end method",
                "edit": ".method public final a(Ljava/lang/String;Ljava/util/List;)Ljava/util/List;\n    .locals 2\n    .param p1    # Ljava/lang/String;\n        .annotation build Lorg/jetbrains/annotations/NotNull;\n        .end annotation\n    .end param\n    .param p2    # Ljava/util/List;\n        .annotation build Lorg/jetbrains/annotations/NotNull;\n        .end annotation\n    .end param\n    .annotation build Lokhttp3/internal/SuppressSignatureCheck;\n    .end annotation\n    .annotation build Lorg/jetbrains/annotations/NotNull;\n    .end annotation\n\n    return-object p2\n\n    const-string v0, \"chain\"\n\n    invoke-static {p2, v0}, Lkotlin/jvm/internal/Intrinsics;->g(Ljava/lang/Object;Ljava/lang/String;)V\n\n    const-string v0, \"hostname\"\n\n    invoke-static {p1, v0}, Lkotlin/jvm/internal/Intrinsics;->g(Ljava/lang/Object;Ljava/lang/String;)V\n\n    const/4 v0, 0x0\n\n    new-array v0, v0, [Ljava/security/cert/X509Certificate;\n\n    invoke-interface {p2, v0}, Ljava/util/Collection;->toArray([Ljava/lang/Object;)[Ljava/lang/Object;\n\n    move-result-object p2\n\n    check-cast p2, [Ljava/security/cert/X509Certificate;\n\n    :try_start_0\n    iget-object v0, p0, Lokhttp3/internal/platform/android/AndroidCertificateChainCleaner;->c:Landroid/net/http/X509TrustManagerExtensions;\n\n    const-string v1, \"RSA\"\n\n    invoke-virtual {v0, p2, v1, p1}, Landroid/net/http/X509TrustManagerExtensions;->checkServerTrusted([Ljava/security/cert/X509Certificate;Ljava/lang/String;Ljava/lang/String;)Ljava/util/List;\n\n    move-result-object p1\n\n    const-string p2, \"checkServerTrusted(...)\"\n\n    invoke-static {p1, p2}, Lkotlin/jvm/internal/Intrinsics;->f(Ljava/lang/Object;Ljava/lang/String;)V\n\n    :try_end_0\n    .catch Ljava/security/cert/CertificateException; {:try_start_0 .. :try_end_0} :catch_0\n\n    return-object p1\n\n    :catch_0\n    move-exception p1\n\n    new-instance p2, Ljavax/net/ssl/SSLPeerUnverifiedException;\n\n    invoke-virtual {p1}, Ljava/lang/Throwable;->getMessage()Ljava/lang/String;\n\n    move-result-object v0\n\n    invoke-direct {p2, v0}, Ljavax/net/ssl/SSLPeerUnverifiedException;-><init>(Ljava/lang/String;)V\n\n    invoke-virtual {p2, p1}, Ljava/lang/Throwable;->initCause(Ljava/lang/Throwable;)Ljava/lang/Throwable;\n\n    throw p2\n.end method"
            }
        ]
    }

---

### 🔧 RE-Patch-Report (PID-20260817-033224)
* **App:** digifit.virtuagym.client.android (v12.4.2)
* **Name:** Success Documentation_VirtuaGym_Success
* **Testergebnis:** Success

  * **Smali Patch 1** in Datei: `smali/classes7/okhttp3/internal/tls/OkHostnameVerifier.smali`
  ```smali
.method public final verify(Ljava/lang/String;Ljavax/net/ssl/SSLSession;)Z
    .locals 2

    const/4 v0, 0x1
    return v0
.end method
  ```

  * **Smali Patch 2** in Datei: `smali/classes7/okhttp3/internal/platform/android/AndroidCertificateChainCleaner.smali`
  ```smali
.method public final a(Ljava/lang/String;Ljava/util/List;)Ljava/util/List;
    .locals 2
    .param p1    # Ljava/lang/String;
        .annotation build Lorg/jetbrains/annotations/NotNull;
        .end annotation
    .end param
    .param p2    # Ljava/util/List;
        .annotation build Lorg/jetbrains/annotations/NotNull;
        .end annotation
    .end param
    .annotation build Lokhttp3/internal/SuppressSignatureCheck;
    .end annotation
    .annotation build Lorg/jetbrains/annotations/NotNull;
    .end annotation

    return-object p2

    const-string v0, "chain"

    invoke-static {p2, v0}, Lkotlin/jvm/internal/Intrinsics;->g(Ljava/lang/Object;Ljava/lang/String;)V

    const-string v0, "hostname"

    invoke-static {p1, v0}, Lkotlin/jvm/internal/Intrinsics;->g(Ljava/lang/Object;Ljava/lang/String;)V

    const/4 v0, 0x0

    new-array v0, v0, [Ljava/security/cert/X509Certificate;

    invoke-interface {p2, v0}, Ljava/util/Collection;->toArray([Ljava/lang/Object;)[Ljava/lang/Object;

    move-result-object p2

    check-cast p2, [Ljava/security/cert/X509Certificate;

    :try_start_0
    iget-object v0, p0, Lokhttp3/internal/platform/android/AndroidCertificateChainCleaner;->c:Landroid/net/http/X509TrustManagerExtensions;

    const-string v1, "RSA"

    invoke-virtual {v0, p2, v1, p1}, Landroid/net/http/X509TrustManagerExtensions;->checkServerTrusted([Ljava/security/cert/X509Certificate;Ljava/lang/String;Ljava/lang/String;)Ljava/util/List;

    move-result-object p1

    const-string p2, "checkServerTrusted(...)"

    invoke-static {p1, p2}, Lkotlin/jvm/internal/Intrinsics;->f(Ljava/lang/Object;Ljava/lang/String;)V

    :try_end_0
    .catch Ljava/security/cert/CertificateException; {:try_start_0 .. :try_end_0} :catch_0

    return-object p1

    :catch_0
    move-exception p1

    new-instance p2, Ljavax/net/ssl/SSLPeerUnverifiedException;

    invoke-virtual {p1}, Ljava/lang/Throwable;->getMessage()Ljava/lang/String;

    move-result-object v0

    invoke-direct {p2, v0}, Ljavax/net/ssl/SSLPeerUnverifiedException;-><init>(Ljava/lang/String;)V

    invoke-virtual {p2, p1}, Ljava/lang/Throwable;->initCause(Ljava/lang/Throwable;)Ljava/lang/Throwable;

    throw p2
.end method
  ```

**Beobachtung:**
Erfolgreich angemeldet
Termine angezeigt
Termin gebucht & Storniert

---
