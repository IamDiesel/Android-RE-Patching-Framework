import os
import urllib.request
import lzma
import zipfile
import shutil
import json
import glob
import threading
from core.application.event_bus import EventBus


class ToolManager:
    """Lädt externe Abhängigkeiten herunter und injiziert sie in den System-PATH."""

    TOOLS_JARS = {
        "uber-apk-signer.jar": "https://github.com/patrickfav/uber-apk-signer/releases/download/v1.3.0/uber-apk-signer-1.3.0.jar",
        "APKEditor.jar": "https://github.com/REAndroid/APKEditor/releases/download/V1.4.2/APKEditor-1.4.2.jar",
        "lspatch.jar": "https://github.com/LSPosed/LSPatch/releases/download/v0.6/jar-v0.6-398-release.jar",
        "TrustMeAlready.apk": "https://github.com/ViRb3/TrustMeAlready/releases/download/v1.11/TrustMeAlready-v1.11-release.apk"
    }

    FRIDA_URL = "https://github.com/frida/frida/releases/download/17.17.0/frida-gadget-17.17.0-android-arm64.so.xz"

    WIN_APKTOOL_JAR = "https://bitbucket.org/iBotPeaches/apktool/downloads/apktool_2.9.3.jar"
    WIN_APKTOOL_BAT = "https://raw.githubusercontent.com/iBotPeaches/Apktool/master/scripts/windows/apktool.bat"
    WIN_PLATFORM_TOOLS = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
    WIN_BUILD_TOOLS = "https://dl.google.com/android/repository/build-tools_r34-windows.zip"
    WIN_NODE_JS = "https://nodejs.org/dist/v20.11.1/node-v20.11.1-win-x64.zip"

    # NEU: Korrekte URL für das portable ZIP-Archiv von Graphviz
    WIN_GRAPHVIZ = "https://gitlab.com/api/v4/projects/4207231/packages/generic/graphviz-releases/10.0.1/windows_10_cmake_Release_Graphviz-10.0.1-win64.zip"

    @classmethod
    def _download_file(cls, url: str, target_path: str) -> None:
        """Atomarer Download: erst nach .part laden, Groesse gegen Content-Length pruefen,
        dann umbenennen. Verhindert, dass ein abgebrochener Download als 'fertige' Datei
        liegen bleibt (sonst wird z. B. eine abgeschnittene ZIP spaeter als gueltig behandelt)."""
        req = urllib.request.Request(url, headers={'User-Agent': 'Kippy-RE-Framework-Downloader'})
        tmp = target_path + ".part"
        with urllib.request.urlopen(req, timeout=120) as response:
            total = int(response.headers.get("Content-Length") or 0)
            show = total > 20 * (1 << 20)     # Fortschritt nur bei grossen Downloads
            done = last = 0
            chunk = 1 << 20
            with open(tmp, 'wb') as out_file:
                while True:
                    buf = response.read(chunk)
                    if not buf:
                        break
                    out_file.write(buf)
                    done += len(buf)
                    if show and done - last >= 25 * (1 << 20):
                        last = done
                        mb_total = (str(total // (1 << 20)) if total else "?")
                        pct = (f" ({done * 100 // total}%)" if total else "")
                        try:
                            print(f"    … {done // (1 << 20)}/{mb_total} MB{pct}", flush=True)
                        except Exception:
                            pass
        got = os.path.getsize(tmp)
        if total and got < total:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise IOError(f"unvollstaendiger Download {got}/{total} B: {url}")
        os.replace(tmp, target_path)

    @classmethod
    def setup_tools(cls, base_dir: str) -> None:
        tools_dir = os.path.join(base_dir, "tools")
        os.makedirs(tools_dir, exist_ok=True)
        is_win = os.name == 'nt'

        for tool_name, url in cls.TOOLS_JARS.items():
            target_path = os.path.join(tools_dir, tool_name)
            if not os.path.exists(target_path):
                EventBus.publish("LOG_INFO", f"[*] Lade Tool herunter: {tool_name} ...")
                try:
                    cls._download_file(url, target_path)
                    EventBus.publish("LOG_INFO", f"[+] {tool_name} erfolgreich installiert!")
                except Exception as e:
                    EventBus.publish("LOG_INFO", f"[!] Download fehlgeschlagen für {tool_name}: {e}")

        frida_target = os.path.join(tools_dir, "libfrida-gadget.so")
        if not os.path.exists(frida_target):
            EventBus.publish("LOG_INFO", "[*] Lade Frida 17.17.0 Gadget herunter...")
            xz_path = os.path.join(tools_dir, "frida.xz")
            try:
                cls._download_file(cls.FRIDA_URL, xz_path)
                with lzma.open(xz_path) as f_in, open(frida_target, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                os.remove(xz_path)
                EventBus.publish("LOG_INFO", "[+] libfrida-gadget.so (17.17.0) erfolgreich installiert!")
            except Exception as e:
                EventBus.publish("LOG_INFO", f"[!] Fehler bei Frida-Download: {e}")

        if is_win:
            cls._setup_windows_binaries(tools_dir)
            cls._setup_node_js(tools_dir)
        else:
            EventBus.publish("LOG_INFO",
                             "[*] Nicht-Windows-System. Bitte installiere adb, apktool, zipalign und node.js manuell.")

        cls._inject_into_path(tools_dir, is_win)
        cls._create_manual_instructions(tools_dir)
        # Ghidra beim Start bereitstellen wie die uebrigen Tools (laedt Ghidra + ggf. JDK 21,
        # einmalig; idempotent). Meldungen landen in der Haupt-Konsole wie bei den anderen Tools.
        try:
            EventBus.publish("LOG_INFO", "[*] Pruefe Ghidra (C-Decompiler) ...")
            cls.resolve_ghidra({"BASE_DIR": base_dir}, allow_download=True)
        except Exception as e:
            EventBus.publish("LOG_INFO", f"[!] Ghidra-Setup uebersprungen: {e!r}")
        EventBus.publish("LOG_INFO", "[+] Auto-Setup abgeschlossen. Alle Tool-Abhängigkeiten sind bereit.")

    @classmethod
    def _setup_windows_binaries(cls, tools_dir: str):
        apktool_bat = os.path.join(tools_dir, "apktool.bat")
        apktool_jar = os.path.join(tools_dir, "apktool.jar")
        if not os.path.exists(apktool_bat) or not os.path.exists(apktool_jar):
            try:
                cls._download_file(cls.WIN_APKTOOL_BAT, apktool_bat)
                cls._download_file(cls.WIN_APKTOOL_JAR, apktool_jar)
            except Exception:
                pass

        pt_dir = os.path.join(tools_dir, "platform-tools")
        if not os.path.exists(pt_dir) or not os.path.exists(os.path.join(pt_dir, "adb.exe")):
            zip_path = os.path.join(tools_dir, "pt.zip")
            try:
                cls._download_file(cls.WIN_PLATFORM_TOOLS, zip_path)
                with zipfile.ZipFile(zip_path, 'r') as z:
                    z.extractall(tools_dir)
                os.remove(zip_path)
            except Exception:
                pass

        bt_dir = os.path.join(tools_dir, "android-14")
        if not os.path.exists(bt_dir) or not os.path.exists(os.path.join(bt_dir, "zipalign.exe")):
            zip_path = os.path.join(tools_dir, "bt.zip")
            try:
                cls._download_file(cls.WIN_BUILD_TOOLS, zip_path)
                with zipfile.ZipFile(zip_path, 'r') as z:
                    z.extractall(tools_dir)
                os.remove(zip_path)
            except Exception:
                pass

        # NEU: Graphviz Setup (Portable)
        gv_dir = os.path.join(tools_dir, "graphviz")
        dot_exists = False
        if os.path.exists(gv_dir):
            for root, _, files in os.walk(gv_dir):
                if "dot.exe" in files:
                    dot_exists = True
                    break

        if not dot_exists:
            EventBus.publish("LOG_INFO", "[*] Lade portable Graphviz Umgebung herunter (ZIP ca. 5MB)...")
            zip_path = os.path.join(tools_dir, "graphviz.zip")
            try:
                # 1. Herunterladen
                cls._download_file(cls.WIN_GRAPHVIZ, zip_path)

                # 2. Ordner sicherstellen
                os.makedirs(gv_dir, exist_ok=True)

                # 3. Entpacken
                with zipfile.ZipFile(zip_path, 'r') as z:
                    z.extractall(gv_dir)

                # 4. Aufräumen
                os.remove(zip_path)
                EventBus.publish("LOG_INFO", "[+] Graphviz erfolgreich portabel eingerichtet!")
            except Exception as e:
                EventBus.publish("LOG_INFO", f"[!] Fehler bei Graphviz Download: {e}")

    @classmethod
    def _setup_node_js(cls, tools_dir: str):
        # Prüfen, ob node.js bereits im globalen System existiert
        if shutil.which("node") is not None:
            return

        node_dir = os.path.join(tools_dir, "node-v20.11.1-win-x64")
        node_exe = os.path.join(node_dir, "node.exe")

        if not os.path.exists(node_exe):
            EventBus.publish("LOG_INFO", "[*] Lade portable Node.js Umgebung herunter (für frida-compile)...")
            zip_path = os.path.join(tools_dir, "node.zip")
            try:
                cls._download_file(cls.WIN_NODE_JS, zip_path)
                with zipfile.ZipFile(zip_path, 'r') as z:
                    z.extractall(tools_dir)
                os.remove(zip_path)
                EventBus.publish("LOG_INFO", "[+] Node.js erfolgreich eingerichtet!")
            except Exception as e:
                EventBus.publish("LOG_INFO", f"[!] Fehler bei Node.js Download: {e}")

    @classmethod
    def _inject_into_path(cls, tools_dir: str, is_win: bool):
        paths_to_add = [os.path.abspath(tools_dir)]
        if is_win:
            pt_dir = os.path.join(tools_dir, "platform-tools")
            bt_dir = os.path.join(tools_dir, "android-14")
            node_dir = os.path.join(tools_dir, "node-v20.11.1-win-x64")

            if os.path.exists(pt_dir): paths_to_add.append(os.path.abspath(pt_dir))
            if os.path.exists(bt_dir): paths_to_add.append(os.path.abspath(bt_dir))
            if os.path.exists(node_dir): paths_to_add.append(os.path.abspath(node_dir))

            # NEU: Graphviz Bin-Ordner dynamisch finden und injizieren
            gv_dir = os.path.join(tools_dir, "graphviz")
            if os.path.exists(gv_dir):
                for root, _, files in os.walk(gv_dir):
                    if "dot.exe" in files:
                        paths_to_add.append(os.path.abspath(root))
                        break

        current_path = os.environ.get("PATH", "")
        new_path_elements = [p for p in paths_to_add if p not in current_path.split(os.pathsep)]

        if new_path_elements:
            os.environ["PATH"] = os.pathsep.join(new_path_elements) + os.pathsep + current_path

    @classmethod
    def _create_manual_instructions(cls, tools_dir: str) -> None:
        readme_path = os.path.join(tools_dir, "README_MANUAL_TOOLS.txt")
        if not os.path.exists(readme_path):
            content = (
                "Falls der automatische Download fehlschlägt, lade diese Dateien manuell herunter und lege sie hier ab:\n\n"
                "1. libfrida-gadget.so (Entpackt aus der .xz von Frida Releases, v17+)\n"
                "2. APKEditor.jar\n"
                "3. uber-apk-signer.jar\n"
                "4. lspatch.jar\n"
                "5. TrustMeAlready.apk\n\n"
                "Für Windows:\n"
                "- apktool.jar und apktool.bat direkt in diesen Ordner\n"
                "- platform-tools hier entpacken\n"
                "- build-tools als 'android-14' entpacken\n"
                "- Node.js portable (ZIP) als 'node-vX.Y.Z-win-x64' entpacken\n"
                "- Graphviz portable (ZIP) als 'graphviz' entpacken (sodass dot.exe in einem Unterordner liegt)\n"
                "- Ghidra (ZIP von github.com/NationalSecurityAgency/ghidra/releases) als 'ghidra/ghidra_<ver>_PUBLIC' entpacken\n"
                "- JDK 21 (Temurin, github.com/adoptium/temurin21-binaries) als 'jdk/jdk-<ver>' entpacken (nur falls kein JDK>=21 im System)\n"
            )
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(content)
    # ================= Ghidra (headless C-Decompile) =================
    GHIDRA_JDK_MAJOR = 21
    _ghidra_lock = threading.Lock()   # serialisiert Download/Entpacken (Start-Setup vs. 🧊-C-Klick)

    @classmethod
    def _emit(cls, log, msg):
        # Primär ins Python-Terminal (stdout) — dort sieht der Nutzer Tool-Installationen,
        # wie die pip/PATH-Meldungen aus main.py. Zusätzlich in die EventBus-Hauptkonsole.
        # NICHT in die Analyse-Konsole (kein log-Passthrough beim Ghidra-Setup).
        try:
            print(msg, flush=True)
        except Exception:
            pass
        EventBus.publish("LOG_INFO", msg)
        if log:
            try:
                log(msg)
            except Exception:
                pass

    @classmethod
    def _headless_exe(cls) -> str:
        return "analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless"

    @classmethod
    def _ghidra_in(cls, root: str) -> str:
        if not root or not os.path.isdir(root):
            return ""
        exe = cls._headless_exe()
        direct = os.path.join(root, "support", exe)
        if os.path.isfile(direct):
            return direct
        hits = glob.glob(os.path.join(root, "ghidra_*", "support", exe))
        return hits[0] if hits else ""

    @classmethod
    def _ghidra_search_roots(cls, base_dir: str) -> list:
        roots = [os.path.join(base_dir, "tools", "ghidra")]
        for env in ("GHIDRA_INSTALL_DIR", "GHIDRA_HOME"):
            v = os.environ.get(env)
            if v:
                roots.append(v)
        if os.name == "nt":
            for pat in ("C:\\ghidra*", "C:\\Program Files\\ghidra*",
                        "C:\\tools\\ghidra*", os.path.expanduser("~\\ghidra*")):
                roots += glob.glob(pat)
        else:
            for pat in ("/opt/ghidra*", os.path.expanduser("~/ghidra*"), "/usr/local/ghidra*"):
                roots += glob.glob(pat)
        return roots

    @classmethod
    def _gh_latest_asset(cls, repo: str, predicate) -> tuple:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(url, headers={'User-Agent': 'Kippy-RE-Framework-Downloader'})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
        for a in data.get("assets", []):
            if predicate(a["name"]):
                return a["name"], a["browser_download_url"]
        return "", ""

    @classmethod
    def resolve_ghidra(cls, config: dict, allow_download: bool = True, log=None) -> str:
        """Findet analyzeHeadless (Override GHIDRA_HEADLESS -> ENV -> tools/ghidra -> gaengige Orte),
        laedt bei Bedarf Ghidra (+ JDK 21) und verdrahtet GHIDRA_INSTALL_DIR/JAVA_HOME/PATH.
        Muster wie resolve_clang/resolve_ndk. `log` spiegelt Meldungen zusaetzlich in die GUI."""
        config = config or {}
        base_dir = config.get("BASE_DIR", os.getcwd())
        override = str(config.get("GHIDRA_HEADLESS", "") or "").strip()
        if override and os.path.isfile(override):
            cls._wire_ghidra(base_dir, override, config, log)
            return override
        for root in cls._ghidra_search_roots(base_dir):
            h = cls._ghidra_in(root)
            if h:
                cls._emit(log, f"[+] Ghidra gefunden: {h}")
                cls._wire_ghidra(base_dir, h, config, log)
                return h
        if not allow_download:
            return ""
        h = cls.download_ghidra(config, log)
        if h:
            cls._wire_ghidra(base_dir, h, config, log)
        return h

    @classmethod
    def download_ghidra(cls, config: dict, log=None) -> str:
        config = config or {}
        base_dir = config.get("BASE_DIR", os.getcwd())
        gdir = os.path.join(base_dir, "tools", "ghidra")
        os.makedirs(gdir, exist_ok=True)
        # Download serialisieren: laeuft das Start-Setup schon, wartet der 🧊-C-Klick hier
        # und findet danach das fertige Ghidra (Re-Check) -> kein Doppel-Download/-Entpacken.
        if not cls._ghidra_lock.acquire(blocking=False):
            cls._emit(log, "[*] Ghidra wird bereits im Hintergrund geladen — warte, bis es fertig ist …")
            cls._ghidra_lock.acquire()
        try:
            existing = cls._ghidra_in(gdir)
            if existing:
                cls._emit(log, "[+] Ghidra bereits eingerichtet.")
                return existing
            cls._emit(log, "[*] Frage GitHub nach aktuellem Ghidra-Release ...")
            try:
                name, url = cls._gh_latest_asset("NationalSecurityAgency/ghidra",
                                                 lambda n: n.endswith(".zip") and "PUBLIC" in n)
            except Exception as e:
                cls._emit(log, f"[!] Ghidra-Release-Abfrage fehlgeschlagen: {e!r} "
                               f"(Proxy/Offline? Dann Ghidra manuell nach tools/ghidra/ entpacken "
                               f"oder GHIDRA_HEADLESS in config.json setzen.)")
                return ""
            if not url:
                cls._emit(log, "[!] Kein Ghidra-Release-Asset gefunden.")
                return ""
            zip_path = os.path.join(gdir, name)
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except OSError:
                    pass
            cls._emit(log, f"[*] Lade Ghidra herunter ({name}, ~570 MB, einmalig)...")
            import tempfile as _tf, shutil as _sh
            incoming = None
            try:
                cls._download_file(url, zip_path)
                cls._emit(log, "[*] Entpacke Ghidra (kann etwas dauern)...")
                for stale in glob.glob(os.path.join(gdir, ".incoming*")):
                    _sh.rmtree(stale, ignore_errors=True)
                incoming = _tf.mkdtemp(prefix=".incoming", dir=gdir)
                with zipfile.ZipFile(zip_path, "r") as z:
                    z.extractall(incoming)
                subs = glob.glob(os.path.join(incoming, "ghidra_*"))
                if not subs:
                    raise IOError("Ghidra-ZIP ohne erwartetes ghidra_*-Verzeichnis")
                final = os.path.join(gdir, os.path.basename(subs[0]))
                if os.path.exists(final):
                    _sh.rmtree(final, ignore_errors=True)
                os.replace(subs[0], final)          # erst jetzt gilt Ghidra als installiert
                _sh.rmtree(incoming, ignore_errors=True); incoming = None
                os.remove(zip_path)
            except Exception as e:
                try:
                    if incoming:
                        _sh.rmtree(incoming, ignore_errors=True)
                    if os.path.exists(zip_path):
                        os.remove(zip_path)
                except OSError:
                    pass
                cls._emit(log, f"[!] Ghidra-Download/Entpacken fehlgeschlagen: {e!r}")
                return ""
            h = cls._ghidra_in(gdir)
            if h:
                cls._emit(log, "[+] Ghidra erfolgreich eingerichtet.")
            return h
        finally:
            cls._ghidra_lock.release()

    @classmethod
    def _wire_ghidra(cls, base_dir: str, headless: str, config: dict, log=None) -> None:
        os.environ["GHIDRA_INSTALL_DIR"] = os.path.dirname(os.path.dirname(headless))
        try:
            jh = cls.resolve_jdk(config, allow_download=True, log=log)
        except Exception as e:
            cls._emit(log, f"[!] JDK-Bereitstellung uebersprungen: {e!r}")
            jh = None
        if jh:
            os.environ["JAVA_HOME"] = jh
            bin_dir = os.path.join(jh, "bin")
            cur = os.environ.get("PATH", "")
            if bin_dir not in cur.split(os.pathsep):
                os.environ["PATH"] = bin_dir + os.pathsep + cur
            cls._emit(log, f"[+] JAVA_HOME gesetzt: {jh}")

    @classmethod
    def _java_major(cls, java_exe: str) -> int:
        import re, subprocess
        try:
            out = subprocess.run([java_exe, "-version"], capture_output=True, text=True, timeout=15)
            m = re.search(r'version "(\d+)', (out.stderr or "") + (out.stdout or ""))
            return int(m.group(1)) if m else 0
        except Exception:
            return 0

    @classmethod
    def resolve_jdk(cls, config: dict, allow_download: bool = True, log=None):
        config = config or {}
        base_dir = config.get("BASE_DIR", os.getcwd())
        exe = "java.exe" if os.name == "nt" else "java"
        for j in glob.glob(os.path.join(base_dir, "tools", "jdk", "jdk*", "bin", exe)):
            if cls._java_major(j) >= cls.GHIDRA_JDK_MAJOR:
                return os.path.dirname(os.path.dirname(j))
        jh = os.environ.get("JAVA_HOME")
        if jh and cls._java_major(os.path.join(jh, "bin", exe)) >= cls.GHIDRA_JDK_MAJOR:
            return jh
        sys_java = shutil.which(exe)
        if sys_java and cls._java_major(sys_java) >= cls.GHIDRA_JDK_MAJOR:
            return None
        if not allow_download:
            return None
        return cls.download_jdk(config, log)

    @classmethod
    def download_jdk(cls, config: dict, log=None):
        import sys as _sys
        import platform as _plat
        config = config or {}
        base_dir = config.get("BASE_DIR", os.getcwd())
        exe = "java.exe" if os.name == "nt" else "java"
        jdk_root = os.path.join(base_dir, "tools", "jdk")
        os.makedirs(jdk_root, exist_ok=True)
        if not cls._ghidra_lock.acquire(blocking=False):
            cls._emit(log, "[*] JDK/Ghidra-Download laeuft bereits — warte …")
            cls._ghidra_lock.acquire()
        try:
            hits = glob.glob(os.path.join(jdk_root, "jdk*", "bin", exe))
            if hits:
                return os.path.dirname(os.path.dirname(hits[0]))
            osn = "windows" if os.name == "nt" else ("mac" if _sys.platform == "darwin" else "linux")
            arch = "x64" if _plat.machine().lower() in ("amd64", "x86_64", "x64") else "aarch64"
            want = ".zip" if osn == "windows" else ".tar.gz"
            maj = cls.GHIDRA_JDK_MAJOR
            cls._emit(log, f"[*] Frage GitHub nach Temurin JDK {maj} ...")
            try:
                name, url = cls._gh_latest_asset(
                    f"adoptium/temurin{maj}-binaries",
                    lambda n: n.startswith(f"OpenJDK{maj}U-jdk_") and f"_{arch}_" in n and osn in n and "alpine" not in n and n.endswith(want))
            except Exception as e:
                cls._emit(log, f"[!] JDK-Release-Abfrage fehlgeschlagen: {e!r}")
                return None
            if not url:
                cls._emit(log, f"[!] Kein Temurin-JDK-{maj}-Asset gefunden.")
                return None
            arc = os.path.join(jdk_root, name)
            if os.path.exists(arc):
                try:
                    os.remove(arc)
                except OSError:
                    pass
            cls._emit(log, f"[*] Lade Temurin JDK {maj} herunter ({name}, einmalig fuer Ghidra)...")
            import tempfile as _tf, shutil as _sh
            incoming = None
            try:
                cls._download_file(url, arc)
                for stale in glob.glob(os.path.join(jdk_root, ".incoming*")):
                    _sh.rmtree(stale, ignore_errors=True)
                incoming = _tf.mkdtemp(prefix=".incoming", dir=jdk_root)
                if arc.endswith(".zip"):
                    with zipfile.ZipFile(arc) as z:
                        z.extractall(incoming)
                else:
                    import tarfile
                    with tarfile.open(arc) as t:
                        try:
                            t.extractall(incoming, filter="data")
                        except TypeError:
                            t.extractall(incoming)
                subs = glob.glob(os.path.join(incoming, "jdk*"))
                if not subs:
                    raise IOError("JDK-Archiv ohne erwartetes jdk*-Verzeichnis")
                final = os.path.join(jdk_root, os.path.basename(subs[0]))
                if os.path.exists(final):
                    _sh.rmtree(final, ignore_errors=True)
                os.replace(subs[0], final)
                _sh.rmtree(incoming, ignore_errors=True); incoming = None
                os.remove(arc)
            except Exception as e:
                try:
                    if incoming:
                        _sh.rmtree(incoming, ignore_errors=True)
                    if os.path.exists(arc):
                        os.remove(arc)
                except OSError:
                    pass
                cls._emit(log, f"[!] JDK-Download fehlgeschlagen: {e!r}")
                return None
            hits = glob.glob(os.path.join(jdk_root, "jdk*", "bin", exe))
            if hits:
                cls._emit(log, "[+] JDK 21 erfolgreich eingerichtet.")
                return os.path.dirname(os.path.dirname(hits[0]))
            return None
        finally:
            cls._ghidra_lock.release()

    # ================= NDK / clang (LibForge) =================
    NDK_FALLBACK_VERSION = "r27c"

    @classmethod
    def _host_tag(cls) -> str:
        import sys
        if os.name == "nt":
            return "windows-x86_64"
        if sys.platform == "darwin":
            return "darwin-x86_64"
        return "linux-x86_64"

    @classmethod
    def _clang_in_ndk(cls, ndk_dir: str) -> str:
        if not ndk_dir:
            return ""
        exe = "clang.exe" if os.name == "nt" else "clang"
        p = os.path.join(ndk_dir, "toolchains", "llvm", "prebuilt", cls._host_tag(), "bin", exe)
        return p if os.path.exists(p) else ""

    @classmethod
    def _newest_child(cls, root: str) -> str:
        try:
            subs = [os.path.join(root, d) for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))]
            subs.sort(key=lambda p: os.path.basename(p), reverse=True)
            return subs[0] if subs else ""
        except Exception:
            return ""

    @classmethod
    def _ndk_search_roots(cls, base_dir: str) -> list:
        roots = [os.path.join(base_dir, "tools", "ndk")]
        la = os.environ.get("LOCALAPPDATA")
        if la:
            roots.append(os.path.join(la, "Android", "Sdk", "ndk"))
        up = os.environ.get("USERPROFILE")
        if up:
            roots.append(os.path.join(up, "AppData", "Local", "Android", "Sdk", "ndk"))
        for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
            v = os.environ.get(env)
            if v:
                roots.append(os.path.join(v, "ndk"))
        home = os.path.expanduser("~")
        roots.append(os.path.join(home, "Android", "Sdk", "ndk"))
        roots.append(os.path.join(home, "Library", "Android", "sdk", "ndk"))
        return roots

    @classmethod
    def resolve_ndk(cls, config: dict) -> str:
        """Findet ein installiertes NDK (Override -> SDK -> env -> tools/ndk). Gibt Pfad oder '' zurueck."""
        config = config or {}
        base_dir = config.get("BASE_DIR", os.getcwd())
        override = str(config.get("NDK_DIR", "") or "").strip()
        if override and cls._clang_in_ndk(override):
            return override
        for root in cls._ndk_search_roots(base_dir):
            if not os.path.isdir(root):
                continue
            if cls._clang_in_ndk(root):
                return root
            newest = cls._newest_child(root)
            if newest and cls._clang_in_ndk(newest):
                return newest
        return ""

    @classmethod
    def download_ndk(cls, config: dict) -> str:
        """Laedt das gepinnte NDK-ZIP und entpackt es nach tools/ndk/. Gibt NDK-Pfad oder '' zurueck."""
        import sys
        config = config or {}
        base_dir = config.get("BASE_DIR", os.getcwd())
        version = str(config.get("NDK_FALLBACK_VERSION", cls.NDK_FALLBACK_VERSION))
        host = "windows" if os.name == "nt" else ("darwin" if sys.platform == "darwin" else "linux")
        ndk_root = os.path.join(base_dir, "tools", "ndk")
        os.makedirs(ndk_root, exist_ok=True)
        url = f"https://dl.google.com/android/repository/android-ndk-{version}-{host}.zip"
        zip_path = os.path.join(ndk_root, f"android-ndk-{version}.zip")
        EventBus.publish("LOG_INFO", f"[*] Kein NDK gefunden. Lade NDK {version} herunter (grosses Archiv, einmalig)...")
        try:
            cls._download_file(url, zip_path)
            EventBus.publish("LOG_INFO", "[*] Entpacke NDK (kann etwas dauern)...")
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(ndk_root)
            os.remove(zip_path)
        except Exception as e:
            EventBus.publish("LOG_INFO", f"[!] NDK-Download fehlgeschlagen: {e}")
            return ""
        cand = os.path.join(ndk_root, f"android-ndk-{version}")
        if cls._clang_in_ndk(cand):
            EventBus.publish("LOG_INFO", "[+] NDK erfolgreich eingerichtet.")
            return cand
        newest = cls._newest_child(ndk_root)
        return newest if cls._clang_in_ndk(newest) else ""

    @classmethod
    def _verify_clang(cls, clang_path: str) -> bool:
        try:
            from core.infrastructure.command_runner import CommandRunner
            res = CommandRunner.run_blocking(f'"{clang_path}" --version', cwd=os.path.dirname(clang_path))
            return getattr(res, "returncode", 1) == 0
        except Exception:
            return False

    @classmethod
    def resolve_clang(cls, config: dict, allow_download: bool = True) -> str:
        """Liefert den clang-Pfad (oder '') und injiziert das Toolchain-bin dynamisch in den PATH."""
        ndk = cls.resolve_ndk(config)
        if not ndk and allow_download:
            ndk = cls.download_ndk(config)
        if not ndk:
            EventBus.publish("LOG_INFO",
                             "[!] NDK/clang nicht gefunden. In Android Studio (SDK Tools > NDK) installieren "
                             "oder NDK-Pfad in den Einstellungen setzen.")
            return ""
        clang = cls._clang_in_ndk(ndk)
        if not clang:
            EventBus.publish("LOG_INFO", f"[!] clang im NDK nicht gefunden: {ndk}")
            return ""
        bin_dir = os.path.dirname(clang)
        cur = os.environ.get("PATH", "")
        if bin_dir not in cur.split(os.pathsep):
            os.environ["PATH"] = bin_dir + os.pathsep + cur
        if not cls._verify_clang(clang):
            EventBus.publish("LOG_INFO", f"[!] clang gefunden, aber '--version' schlug fehl: {clang}")
            return ""
        EventBus.publish("LOG_INFO", f"[+] NDK/clang bereit: {ndk}")
        return clang
