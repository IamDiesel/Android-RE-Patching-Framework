
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

### 🔧 RE-Patch-Report (PID-20260819-002705)
* **App:** com.arjonasoftware.babycam (v1.0.0)
* **Name:** com.arjonasoftware.babycam
* **Testergebnis:** WORKING

  * **Smali Patch 1** in Datei: `smali/classes2/okhttp3/internal/platform/AndroidPlatform$AndroidCertificateChainCleaner.smali`
  ```smali
.method public clean(Ljava/util/List;Ljava/lang/String;)Ljava/util/List;
    .locals 4
    .annotation system Ldalvik/annotation/Signature;
        value = {
            "(",
            "Ljava/util/List<",
            "Ljava/security/cert/Certificate;",
            ">;",
            "Ljava/lang/String;",
            ")",
            "Ljava/util/List<",
            "Ljava/security/cert/Certificate;",
            ">;"
        }
    .end annotation
    .annotation system Ldalvik/annotation/Throws;
        value = {
            Ljavax/net/ssl/SSLPeerUnverifiedException;
        }
    .end annotation
    return-object p1

    .line 1
    :try_start_0
    invoke-interface {p1}, Ljava/util/List;->size()I

    .line 4
    move-result v0

    .line 5
    new-array v0, v0, [Ljava/security/cert/X509Certificate;

    .line 7
    invoke-interface {p1, v0}, Ljava/util/List;->toArray([Ljava/lang/Object;)[Ljava/lang/Object;

    .line 10
    move-result-object p1

    .line 11
    check-cast p1, [Ljava/security/cert/X509Certificate;

    .line 13
    iget-object v0, p0, Lokhttp3/internal/platform/AndroidPlatform$AndroidCertificateChainCleaner;->checkServerTrusted:Ljava/lang/reflect/Method;

    .line 15
    iget-object v1, p0, Lokhttp3/internal/platform/AndroidPlatform$AndroidCertificateChainCleaner;->x509TrustManagerExtensions:Ljava/lang/Object;

    .line 17
    const/4 v2, 0x3

    .line 18
    new-array v2, v2, [Ljava/lang/Object;

    .line 20
    const/4 v3, 0x0

    .line 21
    aput-object p1, v2, v3

    .line 23
    const-string p1, "RSA"

    .line 25
    const/4 v3, 0x1

    .line 26
    aput-object p1, v2, v3

    .line 28
    const/4 p1, 0x2

    .line 29
    aput-object p2, v2, p1

    .line 31
    invoke-virtual {v0, v1, v2}, Ljava/lang/reflect/Method;->invoke(Ljava/lang/Object;[Ljava/lang/Object;)Ljava/lang/Object;

    .line 34
    move-result-object p1

    .line 35
    check-cast p1, Ljava/util/List;

    .line 37
    :try_end_0
    .catch Ljava/lang/reflect/InvocationTargetException; {:try_start_0 .. :try_end_0} :catch_1
    .catch Ljava/lang/IllegalAccessException; {:try_start_0 .. :try_end_0} :catch_0

    return-object p1

    .line 38
    :catch_0
    move-exception p1

    .line 39
    goto :goto_0

    .line 40
    :catch_1
    move-exception p1

    .line 41
    goto :goto_1

    .line 42
    :goto_0
    invoke-static {p1}, Lcom/google/zxing/qrcode/a;->f(Ljava/lang/Object;)V

    .line 45
    const/4 p1, 0x0

    .line 46
    return-object p1

    .line 47
    :goto_1
    new-instance p2, Ljavax/net/ssl/SSLPeerUnverifiedException;

    .line 49
    invoke-virtual {p1}, Ljava/lang/Throwable;->getMessage()Ljava/lang/String;

    .line 52
    move-result-object v0

    .line 53
    invoke-direct {p2, v0}, Ljavax/net/ssl/SSLPeerUnverifiedException;-><init>(Ljava/lang/String;)V

    .line 56
    invoke-virtual {p2, p1}, Ljava/lang/Throwable;->initCause(Ljava/lang/Throwable;)Ljava/lang/Throwable;

    .line 59
    throw p2
.end method
  ```

  * **Smali Patch 2** in Datei: `smali\classes\okhttp3\internal\tls\OkHostnameVerifier.smali`
  ```smali
.method public verify(Ljava/lang/String;Ljavax/net/ssl/SSLSession;)Z
    .locals 1

    const/4 v0, 0x1
    return v0
.end method
  ```

  * **Smali Patch 3** in Datei: `smali\classes\com\applovin\shadow\okhttp3\internal\platform\android\AndroidCertificateChainCleaner.smali`
  ```smali
.method public clean(Ljava/util/List;Ljava/lang/String;)Ljava/util/List;
    .locals 2
    .annotation system Ldalvik/annotation/Signature;
        value = {
            "(",
            "Ljava/util/List<",
            "+",
            "Ljava/security/cert/Certificate;",
            ">;",
            "Ljava/lang/String;",
            ")",
            "Ljava/util/List<",
            "Ljava/security/cert/Certificate;",
            ">;"
        }
    .end annotation
    .annotation system Ldalvik/annotation/Throws;
        value = {
            Ljavax/net/ssl/SSLPeerUnverifiedException;
        }
    .end annotation
    return-object p1

    .line 1
    invoke-virtual {p1}, Ljava/lang/Object;->getClass()Ljava/lang/Class;

    .line 4
    invoke-virtual {p2}, Ljava/lang/Object;->getClass()Ljava/lang/Class;

    .line 7
    const/4 v0, 0x0

    .line 8
    new-array v0, v0, [Ljava/security/cert/X509Certificate;

    .line 10
    invoke-interface {p1, v0}, Ljava/util/Collection;->toArray([Ljava/lang/Object;)[Ljava/lang/Object;

    .line 13
    move-result-object p1

    .line 14
    check-cast p1, [Ljava/security/cert/X509Certificate;

    .line 16
    :try_start_0
    iget-object v0, p0, Lcom/applovin/shadow/okhttp3/internal/platform/android/AndroidCertificateChainCleaner;->x509TrustManagerExtensions:Landroid/net/http/X509TrustManagerExtensions;

    .line 18
    const-string v1, "RSA"

    .line 20
    invoke-virtual {v0, p1, v1, p2}, Landroid/net/http/X509TrustManagerExtensions;->checkServerTrusted([Ljava/security/cert/X509Certificate;Ljava/lang/String;Ljava/lang/String;)Ljava/util/List;

    .line 23
    move-result-object p1

    .line 24
    invoke-virtual {p1}, Ljava/lang/Object;->getClass()Ljava/lang/Class;

    .line 27
    :try_end_0
    .catch Ljava/security/cert/CertificateException; {:try_start_0 .. :try_end_0} :catch_0

    return-object p1

    .line 28
    :catch_0
    move-exception p1

    .line 29
    new-instance p2, Ljavax/net/ssl/SSLPeerUnverifiedException;

    .line 31
    invoke-virtual {p1}, Ljava/lang/Throwable;->getMessage()Ljava/lang/String;

    .line 34
    move-result-object v0

    .line 35
    invoke-direct {p2, v0}, Ljavax/net/ssl/SSLPeerUnverifiedException;-><init>(Ljava/lang/String;)V

    .line 38
    invoke-virtual {p2, p1}, Ljava/lang/Throwable;->initCause(Ljava/lang/Throwable;)Ljava/lang/Throwable;

    .line 41
    throw p2
.end method
  ```

**Beobachtung:**
API visible

---

### 🔧 RE-Patch-Report (PID-20260819-012334)
* **App:** com.arjonasoftware.babycam (v1.0.0)
* **Name:** com.arjonasoftware.babycam
* **Testergebnis:** WORKING

  * **Smali Patch 1** in Datei: `smali\classes\okhttp3\internal\tls\OkHostnameVerifier.smali`
  ```smali
.method public verify(Ljava/lang/String;Ljavax/net/ssl/SSLSession;)Z
    .locals 1

    const/4 v0, 0x1
    return v0
.end method
  ```

  * **Smali Patch 2** in Datei: `smali\classes\com\applovin\shadow\okhttp3\internal\platform\android\AndroidCertificateChainCleaner.smali`
  ```smali
.method public clean(Ljava/util/List;Ljava/lang/String;)Ljava/util/List;
    .locals 2
    .annotation system Ldalvik/annotation/Signature;
        value = {
            "(",
            "Ljava/util/List<",
            "+",
            "Ljava/security/cert/Certificate;",
            ">;",
            "Ljava/lang/String;",
            ")",
            "Ljava/util/List<",
            "Ljava/security/cert/Certificate;",
            ">;"
        }
    .end annotation
    .annotation system Ldalvik/annotation/Throws;
        value = {
            Ljavax/net/ssl/SSLPeerUnverifiedException;
        }
    .end annotation
    return-object p1

    .line 1
    invoke-virtual {p1}, Ljava/lang/Object;->getClass()Ljava/lang/Class;

    .line 4
    invoke-virtual {p2}, Ljava/lang/Object;->getClass()Ljava/lang/Class;

    .line 7
    const/4 v0, 0x0

    .line 8
    new-array v0, v0, [Ljava/security/cert/X509Certificate;

    .line 10
    invoke-interface {p1, v0}, Ljava/util/Collection;->toArray([Ljava/lang/Object;)[Ljava/lang/Object;

    .line 13
    move-result-object p1

    .line 14
    check-cast p1, [Ljava/security/cert/X509Certificate;

    .line 16
    :try_start_0
    iget-object v0, p0, Lcom/applovin/shadow/okhttp3/internal/platform/android/AndroidCertificateChainCleaner;->x509TrustManagerExtensions:Landroid/net/http/X509TrustManagerExtensions;

    .line 18
    const-string v1, "RSA"

    .line 20
    invoke-virtual {v0, p1, v1, p2}, Landroid/net/http/X509TrustManagerExtensions;->checkServerTrusted([Ljava/security/cert/X509Certificate;Ljava/lang/String;Ljava/lang/String;)Ljava/util/List;

    .line 23
    move-result-object p1

    .line 24
    invoke-virtual {p1}, Ljava/lang/Object;->getClass()Ljava/lang/Class;

    .line 27
    :try_end_0
    .catch Ljava/security/cert/CertificateException; {:try_start_0 .. :try_end_0} :catch_0

    return-object p1

    .line 28
    :catch_0
    move-exception p1

    .line 29
    new-instance p2, Ljavax/net/ssl/SSLPeerUnverifiedException;

    .line 31
    invoke-virtual {p1}, Ljava/lang/Throwable;->getMessage()Ljava/lang/String;

    .line 34
    move-result-object v0

    .line 35
    invoke-direct {p2, v0}, Ljavax/net/ssl/SSLPeerUnverifiedException;-><init>(Ljava/lang/String;)V

    .line 38
    invoke-virtual {p2, p1}, Ljava/lang/Throwable;->initCause(Ljava/lang/Throwable;)Ljava/lang/Throwable;

    .line 41
    throw p2
.end method
  ```

  * **Smali Patch 3** in Datei: `smali\classes2\okhttp3\internal\platform\AndroidPlatform$AndroidCertificateChainCleaner.smali`
  ```smali
.method public clean(Ljava/util/List;Ljava/lang/String;)Ljava/util/List;
    .locals 4
    .annotation system Ldalvik/annotation/Signature;
        value = {
            "(",
            "Ljava/util/List<",
            "Ljava/security/cert/Certificate;",
            ">;",
            "Ljava/lang/String;",
            ")",
            "Ljava/util/List<",
            "Ljava/security/cert/Certificate;",
            ">;"
        }
    .end annotation
    .annotation system Ldalvik/annotation/Throws;
        value = {
            Ljavax/net/ssl/SSLPeerUnverifiedException;
        }
    .end annotation
    return-object p1

    .line 1
    :try_start_0
    invoke-interface {p1}, Ljava/util/List;->size()I

    .line 4
    move-result v0

    .line 5
    new-array v0, v0, [Ljava/security/cert/X509Certificate;

    .line 7
    invoke-interface {p1, v0}, Ljava/util/List;->toArray([Ljava/lang/Object;)[Ljava/lang/Object;

    .line 10
    move-result-object p1

    .line 11
    check-cast p1, [Ljava/security/cert/X509Certificate;

    .line 13
    iget-object v0, p0, Lokhttp3/internal/platform/AndroidPlatform$AndroidCertificateChainCleaner;->checkServerTrusted:Ljava/lang/reflect/Method;

    .line 15
    iget-object v1, p0, Lokhttp3/internal/platform/AndroidPlatform$AndroidCertificateChainCleaner;->x509TrustManagerExtensions:Ljava/lang/Object;

    .line 17
    const/4 v2, 0x3

    .line 18
    new-array v2, v2, [Ljava/lang/Object;

    .line 20
    const/4 v3, 0x0

    .line 21
    aput-object p1, v2, v3

    .line 23
    const-string p1, "RSA"

    .line 25
    const/4 v3, 0x1

    .line 26
    aput-object p1, v2, v3

    .line 28
    const/4 p1, 0x2

    .line 29
    aput-object p2, v2, p1

    .line 31
    invoke-virtual {v0, v1, v2}, Ljava/lang/reflect/Method;->invoke(Ljava/lang/Object;[Ljava/lang/Object;)Ljava/lang/Object;

    .line 34
    move-result-object p1

    .line 35
    check-cast p1, Ljava/util/List;

    .line 37
    :try_end_0
    .catch Ljava/lang/reflect/InvocationTargetException; {:try_start_0 .. :try_end_0} :catch_1
    .catch Ljava/lang/IllegalAccessException; {:try_start_0 .. :try_end_0} :catch_0

    return-object p1

    .line 38
    :catch_0
    move-exception p1

    .line 39
    goto :goto_0

    .line 40
    :catch_1
    move-exception p1

    .line 41
    goto :goto_1

    .line 42
    :goto_0
    invoke-static {p1}, Lcom/google/zxing/qrcode/a;->f(Ljava/lang/Object;)V

    .line 45
    const/4 p1, 0x0

    .line 46
    return-object p1

    .line 47
    :goto_1
    new-instance p2, Ljavax/net/ssl/SSLPeerUnverifiedException;

    .line 49
    invoke-virtual {p1}, Ljava/lang/Throwable;->getMessage()Ljava/lang/String;

    .line 52
    move-result-object v0

    .line 53
    invoke-direct {p2, v0}, Ljavax/net/ssl/SSLPeerUnverifiedException;-><init>(Ljava/lang/String;)V

    .line 56
    invoke-virtual {p2, p1}, Ljava/lang/Throwable;->initCause(Ljava/lang/Throwable;)Ljava/lang/Throwable;

    .line 59
    throw p2
.end method
  ```

**Beobachtung:**
API Sichtbar

---

### 🔧 RE-Patch-Report (PID-20260820-013442)
* **App:** org.nativescript.LibreLinkUp (v1.0.0)
* **Name:** org.nativescript.LibreLinkUp
* **Testergebnis:** CRASH

  * **Smali Patch 1** in Datei: `smali\classes\okhttp3\internal\tls\OkHostnameVerifier.smali`
  ```smali
.method public verify(Ljava/lang/String;Ljavax/net/ssl/SSLSession;)Z
    .locals 2

    const/4 v1, 0x1
    return v1
.end method
  ```

  * **Smali Patch 2** in Datei: `smali\classes\okhttp3\internal\platform\android\AndroidCertificateChainCleaner.smali`
  ```smali
.method public clean(Ljava/util/List;Ljava/lang/String;)Ljava/util/List;
    .locals 1
    .annotation system Ldalvik/annotation/Signature;
        value = {
            "(",
            "Ljava/util/List<",
            "+",
            "Ljava/security/cert/Certificate;",
            ">;",
            "Ljava/lang/String;",
            ")",
            "Ljava/util/List<",
            "Ljava/security/cert/Certificate;",
            ">;"
        }
    .end annotation
    .annotation system Ldalvik/annotation/Throws;
        value = {
            Ljavax/net/ssl/SSLPeerUnverifiedException;
        }
    .end annotation

    return-object p0

    const-string v0, "chain"

    invoke-static {p1, v0}, Lkotlin/jvm/internal/Intrinsics;->checkNotNullParameter(Ljava/lang/Object;Ljava/lang/String;)V

    const-string v0, "hostname"

    invoke-static {p2, v0}, Lkotlin/jvm/internal/Intrinsics;->checkNotNullParameter(Ljava/lang/Object;Ljava/lang/String;)V

    .line 43
    check-cast p1, Ljava/util/Collection;

    const/4 v0, 0x0

    .line 76
    new-array v0, v0, [Ljava/security/cert/X509Certificate;

    invoke-interface {p1, v0}, Ljava/util/Collection;->toArray([Ljava/lang/Object;)[Ljava/lang/Object;

    move-result-object p1

    .line 43
    check-cast p1, [Ljava/security/cert/X509Certificate;

    .line 45
    :try_start_0
    iget-object p0, p0, Lokhttp3/internal/platform/android/AndroidCertificateChainCleaner;->x509TrustManagerExtensions:Landroid/net/http/X509TrustManagerExtensions;

    const-string v0, "RSA"

    invoke-virtual {p0, p1, v0, p2}, Landroid/net/http/X509TrustManagerExtensions;->checkServerTrusted([Ljava/security/cert/X509Certificate;Ljava/lang/String;Ljava/lang/String;)Ljava/util/List;

    move-result-object p0

    const-string/jumbo p1, "x509TrustManagerExtensio\u2026ficates, \"RSA\", hostname)"

    invoke-static {p0, p1}, Lkotlin/jvm/internal/Intrinsics;->checkNotNullExpressionValue(Ljava/lang/Object;Ljava/lang/String;)V

    :try_end_0
    .catch Ljava/security/cert/CertificateException; {:try_start_0 .. :try_end_0} :catch_0

    return-object p0

    :catch_0
    move-exception p0

    .line 47
    new-instance p1, Ljavax/net/ssl/SSLPeerUnverifiedException;

    invoke-virtual {p0}, Ljava/security/cert/CertificateException;->getMessage()Ljava/lang/String;

    move-result-object p2

    invoke-direct {p1, p2}, Ljavax/net/ssl/SSLPeerUnverifiedException;-><init>(Ljava/lang/String;)V

    check-cast p0, Ljava/lang/Throwable;

    invoke-virtual {p1, p0}, Ljavax/net/ssl/SSLPeerUnverifiedException;->initCause(Ljava/lang/Throwable;)Ljava/lang/Throwable;

    check-cast p1, Ljava/lang/Throwable;

    throw p1
.end method
  ```

**Beobachtung:**
App crasht direkt beim Start

---

### 🔧 RE-Patch-Report (PID-20260820-013442)
* **App:** org.nativescript.LibreLinkUp (v1.0.0)
* **Name:** org.nativescript.LibreLinkUp
* **Testergebnis:** CRASH

**Beobachtung:**
App crasht direkt beim Start

---

### 🔧 RE-Patch-Report (PID-20260820-013442)
* **App:** org.nativescript.LibreLinkUp (v1.0.0)
* **Name:** org.nativescript.LibreLinkUp
* **Testergebnis:** CRASH

  * **Smali Patch 1** in Datei: `smali\okhttp3\internal\tls\OkHostnameVerifier.smali`
  ```smali
.method public verify(Ljava/lang/String;Ljavax/net/ssl/SSLSession;)Z
    .locals 2

    const/4 v1, 0x1
    return v1
.end method
  ```

  * **Smali Patch 2** in Datei: `smali\okhttp3\internal\platform\android\AndroidCertificateChainCleaner.smali`
  ```smali
.method public clean(Ljava/util/List;Ljava/lang/String;)Ljava/util/List;
    .locals 1
    .annotation system Ldalvik/annotation/Signature;
        value = {
            "(",
            "Ljava/util/List<",
            "+",
            "Ljava/security/cert/Certificate;",
            ">;",
            "Ljava/lang/String;",
            ")",
            "Ljava/util/List<",
            "Ljava/security/cert/Certificate;",
            ">;"
        }
    .end annotation
    .annotation system Ldalvik/annotation/Throws;
        value = {
            Ljavax/net/ssl/SSLPeerUnverifiedException;
        }
    .end annotation

    return-object p0

    const-string v0, "chain"

    invoke-static {p1, v0}, Lkotlin/jvm/internal/Intrinsics;->checkNotNullParameter(Ljava/lang/Object;Ljava/lang/String;)V

    const-string v0, "hostname"

    invoke-static {p2, v0}, Lkotlin/jvm/internal/Intrinsics;->checkNotNullParameter(Ljava/lang/Object;Ljava/lang/String;)V

    .line 43
    check-cast p1, Ljava/util/Collection;

    const/4 v0, 0x0

    .line 76
    new-array v0, v0, [Ljava/security/cert/X509Certificate;

    invoke-interface {p1, v0}, Ljava/util/Collection;->toArray([Ljava/lang/Object;)[Ljava/lang/Object;

    move-result-object p1

    .line 43
    check-cast p1, [Ljava/security/cert/X509Certificate;

    .line 45
    :try_start_0
    iget-object p0, p0, Lokhttp3/internal/platform/android/AndroidCertificateChainCleaner;->x509TrustManagerExtensions:Landroid/net/http/X509TrustManagerExtensions;

    const-string v0, "RSA"

    invoke-virtual {p0, p1, v0, p2}, Landroid/net/http/X509TrustManagerExtensions;->checkServerTrusted([Ljava/security/cert/X509Certificate;Ljava/lang/String;Ljava/lang/String;)Ljava/util/List;

    move-result-object p0

    const-string/jumbo p1, "x509TrustManagerExtensio\u2026ficates, \"RSA\", hostname)"

    invoke-static {p0, p1}, Lkotlin/jvm/internal/Intrinsics;->checkNotNullExpressionValue(Ljava/lang/Object;Ljava/lang/String;)V

    :try_end_0
    .catch Ljava/security/cert/CertificateException; {:try_start_0 .. :try_end_0} :catch_0

    return-object p0

    :catch_0
    move-exception p0

    .line 47
    new-instance p1, Ljavax/net/ssl/SSLPeerUnverifiedException;

    invoke-virtual {p0}, Ljava/security/cert/CertificateException;->getMessage()Ljava/lang/String;

    move-result-object p2

    invoke-direct {p1, p2}, Ljavax/net/ssl/SSLPeerUnverifiedException;-><init>(Ljava/lang/String;)V

    check-cast p0, Ljava/lang/Throwable;

    invoke-virtual {p1, p0}, Ljavax/net/ssl/SSLPeerUnverifiedException;->initCause(Ljava/lang/Throwable;)Ljava/lang/Throwable;

    check-cast p1, Ljava/lang/Throwable;

    throw p1
.end method
  ```

**Beobachtung:**
App crasht direkt beim Start

---

### 🔧 RE-Patch-Report (PID-20260902-000221)
* **App:** org.nativescript.LibreLinkUp (v1.0.0)
* **Name:** org.nativescript.LibreLinkUp
* **Testergebnis:** WORKING_PARTIAL

  * **Smali Patch 1** in Datei: `smali\com\app\MainApplication.smali`
  ```smali
.method static constructor <clinit>()V
    .locals 2

    # --- FRIDA GADGET INJECTION START ---
    const-string v0, "frida-gadget"
    invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V
    :try_start_sleep
    const-wide/16 v0, 0x7d0
    invoke-static {v0, v1}, Ljava/lang/Thread;->sleep(J)V
    :try_end_sleep
    .catch Ljava/lang/Exception; {:try_start_sleep .. :try_end_sleep} :catch_sleep
    :catch_sleep    
    # --- FRIDA GADGET INJECTION END ---

    const/16 v0, 0x5f

    new-array v0, v0, [B

    fill-array-data v0, :array_0

    sput-object v0, Lcom/app/MainApplication;->$$a:[B

    const/16 v0, 0x27

    sput v0, Lcom/app/MainApplication;->$$b:I

    const/4 v0, 0x0

    sput v0, Lcom/app/MainApplication;->ArtificialStackFrames:I

    const/4 v0, 0x1

    sput v0, Lcom/app/MainApplication;->coroutineCreation:I

    invoke-static {}, Lcom/app/MainApplication;->CoroutineDebuggingKt()V

    new-instance v0, Lcom/app/MainApplication$Companion;

    const/4 v1, 0x0

    invoke-direct {v0, v1}, Lcom/app/MainApplication$Companion;-><init>(Lkotlin/jvm/internal/DefaultConstructorMarker;)V

    sput-object v0, Lcom/app/MainApplication;->Companion:Lcom/app/MainApplication$Companion;

    return-void

    nop

    :array_0
    .array-data 1
        0x24t
        -0x3dt
        0x1et
        -0x61t
        -0x3t
        -0x5t
        -0x3t
        0x9t
        -0x5t
        -0x17t
        0xct
        -0x3t
        -0x10t
        -0x8t
        -0x2t
        -0xbt
        0x1t
        -0xdt
        0x6t
        -0x2bt
        0x27t
        -0x16t
        0x7t
        -0xdt
        0x2ct
        -0x3t
        -0x10t
        -0x8t
        -0x2t
        -0xbt
        0x1t
        -0xdt
        0x6t
        -0x1et
        0x1ct
        -0x18t
        -0x3t
        0x3t
        -0x2at
        0x27t
        -0x16t
        0x7t
        -0xdt
        0x9t
        0x7t
        -0x2t
        -0x8t
        0x1t
        -0x6t
        -0x10t
        0x0t
        -0xet
        -0x27t
        0x2at
        -0x12t
        -0x9t
        0xet
        -0x10t
        0x1t
        -0x6t
        0x7t
        -0x2t
        -0x8t
        0x1t
        -0x6t
        -0x10t
        0x0t
        -0xet
        -0x28t
        0x28t
        0x1t
        -0xct
        -0xft
        -0x8t
        0xct
        0x2t
        0x27t
        -0x2t
        -0x8t
        0x1t
        -0x6t
        -0x10t
        0x0t
        -0xet
        -0x26t
        0x1ct
        -0x8t
        0xet
        -0x13t
        -0x5t
        -0x3t
        0x0t
        -0xct
        -0x21t
        0x24t
    .end array-data
.end method
  ```

**Beobachtung:**
Partial Load Success for LibreLinkUp:
09-02 00:04:38.256 ... I RemoteConfig: init() {android.attachments.maxCount=32, ...
09-02 00:04:38.453 ... D AppStartup: [init] sqlcipher-init: 118, signal-store: 58, logging: 35...

=== PIPELINE FLASH ERFOLGREICH ===

[*] Verbinde mit Frida Gadget über USB...
[*] USB Gerät gefunden. Suche Gadget...
[Frida] [*] Native RASP Hunter & PHANTOM-BLOCKER gestartet...
[Frida] [+] Phantom-Spoofer scharfgestellt. Bereit für alles!
[+] Skript erfolgreich in den RAM injiziert! App wird fortgesetzt (Resume)...
[Frida] [🔥 LOAD-BLOCKER] RASP Lade-Versuch blockiert!
[Frida] [🔥 LOAD-BLOCKER] Harmlose Phantom-Bibliothek geladen!
[Frida] [🔥 DLSYM] Gebe Universal-Dummy für 'Java_o_MediaBrowserCompatMediaBrowserImplBase6_stackFrames' zurück!
[Frida] [🔥 JNI-SPOOFER] Native RASP-Prüfung aus Java neutralisiert!
[Frida] [🔥 DLSYM] Gebe Universal-Dummy für 'Java_o_onResult_read' zurück!
[Frida] [🔥 JNI-SPOOFER] Native RASP-Prüfung aus Java neutralisiert!

---

### 🔧 RE-Patch-Report (PID-20260902-225148)
* **App:** org.nativescript.LibreLinkUp (v1.0.0)
* **Name:** org.nativescript.LibreLinkUp
* **Testergebnis:** WORKING_PARTIAL

  * **Smali Patch 1** in Datei: `smali\okhttp3\internal\tls\OkHostnameVerifier.smali`
  ```smali
.method public verify(Ljava/lang/String;Ljavax/net/ssl/SSLSession;)Z
    .locals 2

    const/4 v1, 0x1
    return v1
.end method
  ```

  * **Smali Patch 2** in Datei: `smali\okhttp3\internal\platform\android\AndroidCertificateChainCleaner.smali`
  ```smali
.method public clean(Ljava/util/List;Ljava/lang/String;)Ljava/util/List;
    .locals 1
    .annotation system Ldalvik/annotation/Signature;
        value = {
            "(",
            "Ljava/util/List<",
            "+",
            "Ljava/security/cert/Certificate;",
            ">;",
            "Ljava/lang/String;",
            ")",
            "Ljava/util/List<",
            "Ljava/security/cert/Certificate;",
            ">;"
        }
    .end annotation
    .annotation system Ldalvik/annotation/Throws;
        value = {
            Ljavax/net/ssl/SSLPeerUnverifiedException;
        }
    .end annotation

    return-object p0

    const-string v0, "chain"

    invoke-static {p1, v0}, Lkotlin/jvm/internal/Intrinsics;->checkNotNullParameter(Ljava/lang/Object;Ljava/lang/String;)V

    const-string v0, "hostname"

    invoke-static {p2, v0}, Lkotlin/jvm/internal/Intrinsics;->checkNotNullParameter(Ljava/lang/Object;Ljava/lang/String;)V

    .line 43
    check-cast p1, Ljava/util/Collection;

    const/4 v0, 0x0

    .line 76
    new-array v0, v0, [Ljava/security/cert/X509Certificate;

    invoke-interface {p1, v0}, Ljava/util/Collection;->toArray([Ljava/lang/Object;)[Ljava/lang/Object;

    move-result-object p1

    .line 43
    check-cast p1, [Ljava/security/cert/X509Certificate;

    .line 45
    :try_start_0
    iget-object p0, p0, Lokhttp3/internal/platform/android/AndroidCertificateChainCleaner;->x509TrustManagerExtensions:Landroid/net/http/X509TrustManagerExtensions;

    const-string v0, "RSA"

    invoke-virtual {p0, p1, v0, p2}, Landroid/net/http/X509TrustManagerExtensions;->checkServerTrusted([Ljava/security/cert/X509Certificate;Ljava/lang/String;Ljava/lang/String;)Ljava/util/List;

    move-result-object p0

    const-string/jumbo p1, "x509TrustManagerExtensio\u2026ficates, \"RSA\", hostname)"

    invoke-static {p0, p1}, Lkotlin/jvm/internal/Intrinsics;->checkNotNullExpressionValue(Ljava/lang/Object;Ljava/lang/String;)V

    :try_end_0
    .catch Ljava/security/cert/CertificateException; {:try_start_0 .. :try_end_0} :catch_0

    return-object p0

    :catch_0
    move-exception p0

    .line 47
    new-instance p1, Ljavax/net/ssl/SSLPeerUnverifiedException;

    invoke-virtual {p0}, Ljava/security/cert/CertificateException;->getMessage()Ljava/lang/String;

    move-result-object p2

    invoke-direct {p1, p2}, Ljavax/net/ssl/SSLPeerUnverifiedException;-><init>(Ljava/lang/String;)V

    check-cast p0, Ljava/lang/Throwable;

    invoke-virtual {p1, p0}, Ljavax/net/ssl/SSLPeerUnverifiedException;->initCause(Ljava/lang/Throwable;)Ljava/lang/Throwable;

    check-cast p1, Ljava/lang/Throwable;

    throw p1
.end method
  ```

  * **Smali Patch 3** in Datei: `smali\com\app\MainApplication.smali`
  ```smali
.method public attachBaseContext(Landroid/content/Context;)V
    .locals 0
    invoke-super {p0, p1}, Landroid/app/Application;->attachBaseContext(Landroid/content/Context;)V
    return-void
.end method
  ```

  * **Smali Patch 4** in Datei: `smali\com\facebook\react\ReactActivity.smali`
  ```smali
.method public onStart()V
    .locals 0
    invoke-super {p0}, Landroidx/appcompat/app/AppCompatActivity;->onStart()V
    return-void
.end method
  ```

**Beobachtung:**
API Kommunikation ist verschlüsselt aber kann über mehrere Minuten über MITM Proxy mitgelesen werden.
Danach gibt es eine silent penalty durch RASP:

1. Der Wechsel in den Recovery-Modus (Network Logs)
Wenn du den aktuellen Traffic mit dem aus Phase 4 vergleichst, fällt ein massiver Unterschied im Polling-Verhalten auf:

Vorher: Die App hat alle 60 Sekunden den leichtgewichtigen Endpunkt .../latest-reading abgefragt. Das ist das normale Verhalten für Echtzeit-Updates.

Jetzt: Die App feuert alle 60 Sekunden auf den schweren Endpunkt .../graph (historische Daten).

Die Ursache: Die Entschlüsselung der Payload schlägt intern fehl. Das JavaScript-Frontend erhält vom nativen secure-api-Modul plötzlich keine gültigen JSON-Blutzuckerwerte mehr (sondern Datenmüll oder null). Die React Native Logik geht davon aus, dass ein Verbindungabbruch oder ein Sensorfehler vorliegt (Data Gap). Als Fallback-Strategie versucht die App verzweifelt, den kompletten Graphen neu zu laden, um sich zu synchronisieren – scheitert aber auch hier bei jeder Antwort an der Entschlüsselung. Die UI friert auf dem letzten gültigen Stand ein.

2. Der native Watchdog (Logcat Analyse)
Der Grund für das Entschlüsselungsversagen liegt im Logcat. Der Thread-7 ist der native Background-Watchdog des RASP. Er feuert extrem aggressiv und asynchron:

path="/sys/kernel/tracing/trace_marker": Der RASP versucht hier, Tracing-Artefakte von Frida oder dem Kernel auszulesen, um Debugging zu erkennen.

path="anon_inode:[userfaultfd]": DexGuard nutzt userfaultfd (User-Space Page Fault Handling), um den Speicherbereich der Krypto-Schlüssel zu überwachen oder "Ghost Code" on-the-fly zu entschlüsseln.

SELinux (avc: denied) blockiert diese Zugriffe. Da der Watchdog seine Fallen nicht aufbauen kann (oder durch die Blockade merkt, dass er in einer feindlichen Umgebung läuft), triggert er die Silent Penalty.

---
