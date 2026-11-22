# -*- coding: utf-8 -*-
"""core.java.manifest — generischer AndroidManifest-Parser (apktool-dekodiertes XML).

Zweck (MS-4a, generisch): Einstiegspunkte einer BELIEBIGEN App aus dem Manifest bestimmen, statt
Launcher/Application/Paketname fest zu verdrahten. Keine Target-Spezifik.

Rueckgabe von `parse_manifest`:
  AppManifest(package, application, launchers, activities)
    - package:     str (z. B. "com.example.app")
    - application: Deskriptor der Application-Klasse oder None (z. B. "Lcom/app/MainApplication;")
    - launchers:   Liste von Activity-Deskriptoren mit MAIN/LAUNCHER-Intent-Filter (Reihenfolge wie im Manifest)
    - activities:  alle Activity-Deskriptoren
Namen werden zu JVM-Deskriptoren normalisiert ("com.app.X" -> "Lcom/app/X;").
"""
from __future__ import annotations

import os
from xml.etree import ElementTree as ET

ANDROID_NS = "http://schemas.android.com/apk/res/android"
_A = "{%s}" % ANDROID_NS


def to_descriptor(dotted):
    """"com.app.MainActivity" -> "Lcom/app/MainActivity;". Schon-Deskriptoren bleiben unveraendert."""
    if not dotted:
        return None
    s = str(dotted).strip()
    if s.startswith("L") and s.endswith(";"):
        return s
    return "L" + s.replace(".", "/") + ";"


class AppManifest:
    def __init__(self, package=None, application=None, launchers=None, activities=None, *,
                 receivers=None, services=None, providers=None, deep_links=None, meta_data=None,
                 launch_modes=None):
        self.package = package
        self.application = application
        self.launchers = launchers or []
        self.activities = activities or []
        self.receivers = receivers or []
        self.services = services or []
        self.providers = providers or []
        self.deep_links = deep_links or []
        self.meta_data = meta_data or {}
        self.launch_modes = dict(launch_modes or {})

    def entry(self):
        """Bevorzugter App-Einstieg: erste Launcher-Activity, sonst erste Activity, sonst None."""
        if self.launchers:
            return self.launchers[0]
        if self.activities:
            return self.activities[0]
        return None

    def to_dict(self):
        return {
            "package": self.package,
            "application": self.application,
            "launchers": list(self.launchers),
            "activities": list(self.activities),
            "receivers": list(self.receivers),
            "services": list(self.services),
            "providers": list(self.providers),
            "deep_links": list(self.deep_links),
            "meta_data": dict(self.meta_data),
            "launch_modes": dict(self.launch_modes),
        }


def _name_attr(el):
    return el.get(_A + "name") or el.get("name")


def _attr(el, name, default=""):
    return el.get(_A + name) or el.get(name) or default


def parse_manifest_text(xml_text):
    root = ET.fromstring(xml_text)
    package = root.get("package")

    def _qualify(nm):
        # Relative Namen (".Foo" oder "Foo") am Paket aufhaengen (Android-Regel).
        if not nm:
            return None
        if nm.startswith("."):
            return (package or "") + nm
        if "." not in nm and package:
            return package + "." + nm
        return nm

    def _extract_actions(comp_el):
        acts = []
        for itf in comp_el.findall("intent-filter"):
            for a in itf.findall("action"):
                an = _name_attr(a)
                if an and an not in acts:
                    acts.append(an)
        return acts

    def _extract_meta(parent_el):
        out = {}
        for md in parent_el.findall("meta-data"):
            mk = _name_attr(md)
            if mk:
                mv = md.get(_A + "value") or md.get("value") or md.get(_A + "resource") or md.get("resource") or ""
                out[mk] = mv
        return out

    app_el = root.find("application")
    application = None
    launchers = []
    activities = []
    receivers = []
    services = []
    providers = []
    deep_links = []
    meta_data = {}
    launch_modes = {}
    if app_el is not None:
        app_name = _name_attr(app_el)
        if app_name and app_name not in ("androidx.core.app.CoreComponentFactory",):
            application = to_descriptor(_qualify(app_name))
        meta_data.update(_extract_meta(app_el))
        for act in app_el.findall("activity") + app_el.findall("activity-alias"):
            nm = _name_attr(act)
            # activity-alias: Ziel ist targetActivity
            tgt = act.get(_A + "targetActivity") or act.get("targetActivity")
            cls = _qualify(tgt) if (act.tag == "activity-alias" and tgt) else _qualify(nm)
            desc = to_descriptor(cls)
            if desc and desc not in activities:
                activities.append(desc)
            lm = (_attr(act, "launchMode", "") or "").strip()
            if desc and lm:
                launch_modes[desc] = lm
            is_launcher = False
            for itf in act.findall("intent-filter"):
                has_main = any(_name_attr(a) == "android.intent.action.MAIN"
                               for a in itf.findall("action"))
                has_launch = any(_name_attr(c) == "android.intent.category.LAUNCHER"
                                 for c in itf.findall("category"))
                if has_main and has_launch:
                    is_launcher = True
                for dt in itf.findall("data"):
                    scheme = _attr(dt, "scheme")
                    host = _attr(dt, "host")
                    path_pref = _attr(dt, "pathPrefix") or _attr(dt, "path") or _attr(dt, "pathPattern")
                    if scheme and desc:
                        deep_links.append({
                            "activity": desc,
                            "scheme": scheme,
                            "host": host,
                            "path_prefix": path_pref,
                        })
            if is_launcher and desc and desc not in launchers:
                launchers.append(desc)
        for rec in app_el.findall("receiver"):
            rdesc = to_descriptor(_qualify(_name_attr(rec)))
            if rdesc:
                receivers.append({"cls": rdesc, "actions": _extract_actions(rec)})
        for svc in app_el.findall("service"):
            sdesc = to_descriptor(_qualify(_name_attr(svc)))
            if sdesc:
                services.append({
                    "cls": sdesc,
                    "actions": _extract_actions(svc),
                    "meta_data": _extract_meta(svc),
                })
        for prv in app_el.findall("provider"):
            pdesc = to_descriptor(_qualify(_name_attr(prv)))
            if pdesc:
                auth = _attr(prv, "authorities")
                init_raw = _attr(prv, "initOrder", "0")
                try:
                    init_order = int(str(init_raw).strip(), 0)
                except Exception:
                    init_order = 0
                en_raw = (_attr(prv, "enabled", "true") or "true").strip().lower()
                exp_raw = (_attr(prv, "exported", "false") or "false").strip().lower()
                providers.append({
                    "cls": pdesc,
                    "authorities": auth,
                    "init_order": init_order,
                    "enabled": (en_raw not in ("false", "0")),
                    "exported": (exp_raw in ("true", "1")),
                    "meta_data": _extract_meta(prv),
                })
    return AppManifest(
        package, application, launchers, activities,
        receivers=receivers, services=services, providers=providers,
        deep_links=deep_links, meta_data=meta_data,
        launch_modes=launch_modes,
    )


def parse_manifest(path_or_root):
    """Pfad zur AndroidManifest.xml ODER zu einem apktool-Wurzelordner (dann /AndroidManifest.xml)."""
    p = path_or_root
    if os.path.isdir(p):
        p = os.path.join(p, "AndroidManifest.xml")
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return parse_manifest_text(f.read())
