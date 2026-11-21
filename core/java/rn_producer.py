# -*- coding: utf-8 -*-
"""core.java.rn_producer — Render-Produzent Familie A fuer React Native (New Architecture / Fabric).

Fabric faehrt Reconciler + Yoga-Layout in C++ und schickt einen Strom von Mount-Instruktionen nach
Java, die je EINE Methode auf `SurfaceMountingManager` rufen (createView/addViewAt/removeViewAt/
deleteView/updateProps/updateLayout). Dieser Strom IST der View-Baum: Typ (componentName) + Props +
Parent/Child + fertig berechnete Layout-Box. Wir fangen genau diese Grenze ab und bauen einen
`ViewTree` (kein eigenes Yoga noetig - Layout kommt in updateLayout). IP-neutral; Grundlage:
[[recherche-rn-fabric-mount-grenze-2026-11-12]].

S3a: nachgespielte Mount-Sequenz (Test/Bootstrap). S3b: echte libhermes/libreactnative via Fusion
laufen lassen - die Mount-Calls landen dann hier (ueber `install_rn_mount_stubs`)."""
from __future__ import annotations

from core.java.view_tree import ViewTree, ViewNode
from core.java.objects import JString
from core.java.surface import SceneGraphSurface, ProducerRegistry

# componentName (RN) -> ViewNode-Klasse (unser View-Baum)
_COMPONENT_MAP = {
    "View": "View", "RCTView": "View", "RCTRootView": "LinearLayout",
    "RCTScrollView": "ScrollView", "ScrollView": "ScrollView",
    "Paragraph": "TextView", "RCTText": "TextView", "Text": "TextView", "RCTRawText": "TextView",
    "AndroidTextInput": "EditText", "RCTTextInput": "EditText", "TextInput": "EditText",
    "RCTSinglelineTextInputView": "EditText", "RCTMultilineTextInputView": "EditText",
    "RCTImageView": "ImageView", "Image": "ImageView", "RCTImage": "ImageView",
    "AndroidSwitch": "Switch", "Switch": "Switch", "RCTSwitch": "Switch",
    "ActivityIndicatorView": "ProgressBar", "RCTActivityIndicatorView": "ProgressBar",
}


def _s(v):
    return v.text if isinstance(v, JString) else ("" if v is None else str(v))


def map_component(name):
    return _COMPONENT_MAP.get(name, "View")


class RnMountRecorder:
    """Fabric-SurfaceMountingManager-Shim: Mount-Instruktionen -> ViewTree."""

    def __init__(self, width=380, height=800):
        self.nodes = {}          # tag -> ViewNode
        self.root_tag = None
        self.tree = ViewTree(width, height)
        self.events = []

    # --- Mount-Operationen (SurfaceMountingManager-Semantik) ---
    def create_view(self, tag, component, props=None):
        n = ViewNode(cls=map_component(_s(component)))
        n._rn_component = _s(component)
        self._apply_props(n, props or {})
        self.nodes[int(tag)] = n
        return n

    def set_root(self, tag):
        self.root_tag = int(tag)
        if int(tag) not in self.nodes:
            self.nodes[int(tag)] = ViewNode(cls="LinearLayout")

    def add_view_at(self, parent_tag, tag, index=0):
        p = self.nodes.get(int(parent_tag)); c = self.nodes.get(int(tag))
        if p is not None and c is not None:
            idx = max(0, min(int(index), len(p.children)))
            p.children.insert(idx, c)
            setattr(c, "_parent", p)

    def remove_view_at(self, parent_tag, index):
        p = self.nodes.get(int(parent_tag))
        if p is not None and 0 <= int(index) < len(p.children):
            p.children.pop(int(index))

    def delete_view(self, tag):
        self.nodes.pop(int(tag), None)

    def update_props(self, tag, props):
        n = self.nodes.get(int(tag))
        if n is not None:
            self._apply_props(n, props or {})

    def update_layout(self, tag, x, y, w, h):
        n = self.nodes.get(int(tag))
        if n is not None:
            n.bounds = (float(x), float(y), float(w), float(h))

    # --- Props -> ViewNode ---
    def _apply_props(self, n, props):
        if not isinstance(props, dict):
            return
        if "text" in props:
            n.text = _s(props["text"])
        if "placeholder" in props:
            n.props["hint"] = _s(props["placeholder"])
        if "value" in props and n.cls == "EditText":
            n.text = _s(props["value"])
        if n.cls == "EditText":
            n.focusable = True
            n.clickable = True
            cb_py = props.get("onChangeText")
            def _on_rn_text_change(val, _nd=n, _cb=cb_py):
                txt_v = _s(val)
                _nd.text = txt_v
                self.events.append({
                    "type": "changeText",
                    "tag": getattr(_nd, "_rn_tag", None),
                    "text": txt_v,
                })
                if callable(_cb):
                    _cb(txt_v)
            n.props["$on_change_fn"] = _on_rn_text_change
        if "style" in props and props["style"]:
            try:
                from core.java.rn_yoga_seams import apply_rn_visual_style, flatten_rn_style
                flat = flatten_rn_style(props["style"])
                if flat:
                    apply_rn_visual_style(n, flat, props=props)
            except Exception:
                pass
        # Klickbar: expliziter onPress/Button ODER echtes RN-Responder-/Click-System
        # (Pressable aus react-native setzt onStartShouldSetResponder/onResponderRelease/onClick,
        # nicht onPress direkt).
        if (props.get("onPress") or props.get("accessibilityRole") == "button"
                or props.get("onClick") or props.get("onStartShouldSetResponder")
                or props.get("onResponderRelease")):
            n.clickable = True
        src_val = props.get("source") or props.get("src")
        if isinstance(src_val, dict):
            src_val = src_val.get("uri") or src_val.get("url") or src_val.get("name") or ""
        if src_val:
            n.props["img"] = _s(src_val)

    # --- Ergebnis ---
    def surface(self, relayout=False):
        """relayout=False: Boxen kommen aus update_layout (Fabric-nativ). relayout=True: unser
        Layout-Engine positioniert (Yoga-2D-Flexbox bei rn_style, sonst vertikaler Stack)."""
        root = self.nodes.get(self.root_tag) if self.root_tag is not None else None
        if root is None and self.nodes:
            root = next(iter(self.nodes.values()))
        if root is not None:
            if relayout:
                has_rn_style = any(bool(nd.props.get("rn_style")) for nd in root.walk())
                if has_rn_style:
                    try:
                        from core.java.rn_yoga_seams import layout_rn_flexbox
                        self.tree._custom_layout_fn = layout_rn_flexbox
                    except Exception:
                        self.tree._custom_layout_fn = None
                else:
                    self.tree._custom_layout_fn = None
                self.tree.set_root(root)     # ViewTree-Layout (Yoga 2D oder vertikaler Stack)
            else:
                self.tree._custom_layout_fn = None
                self.tree.root = root
                self.tree.version += 1
        return SceneGraphSurface(self.tree)


def install_rn_mount_stubs(stubs, rec=None):
    """S3b-Haken: registriert SurfaceMountingManager-Methodennamen -> Recorder, damit echte
    RN-Fabric-Mount-Calls (ueber die JNI-Fassade) hier landen. Rueckgabe: der RnMountRecorder."""
    rec = rec if rec is not None else RnMountRecorder()
    stubs.rn_mount_recorder = rec
    bn = stubs.register_by_name
    bn("createView", "(Ljava/lang/String;ILjava/lang/Object;)V",
       lambda a: rec.create_view(a[2], a[1], a[3] if len(a) > 3 else None) if len(a) > 2 else None)
    bn("addViewAt", "(III)V", lambda a: rec.add_view_at(a[1], a[2], a[3]) if len(a) > 3 else None)
    bn("removeViewAt", "(II)V", lambda a: rec.remove_view_at(a[1], a[2]) if len(a) > 2 else None)
    bn("deleteView", "(I)V", lambda a: rec.delete_view(a[1]) if len(a) > 1 else None)
    bn("updateProps", "(ILjava/lang/Object;)V",
       lambda a: rec.update_props(a[1], a[2] if len(a) > 2 else {}) if len(a) > 1 else None)
    bn("updateLayout", "(IFFFF)V",
       lambda a: rec.update_layout(a[1], a[2], a[3], a[4], a[5]) if len(a) > 5 else None)
    try:
        from core.java.rn_yoga_seams import install_rn_native_seams
        install_rn_native_seams(stubs, rec=rec)
    except Exception:
        pass
    return rec


def rn_producer(ctx):
    """ctx: dict mit optional 'stubs'. Rueckgabe: RnMountRecorder (nachher .surface())."""
    stubs = ctx.get("stubs") if isinstance(ctx, dict) else None
    if stubs is not None:
        return install_rn_mount_stubs(stubs)
    return RnMountRecorder()


# Registrierung zentral in core.java.producer_wiring.ensure_default_producers (kein Import-Seiteneffekt).


def demo():
    """Nachgespielte Fabric-Mount-Sequenz (Login) fuer die Studio-Anzeige -> SceneGraphSurface."""
    r = RnMountRecorder(width=380, height=800)
    r.set_root(1)
    r.create_view(2, "Paragraph", {"text": "Anmelden (React Native)"})
    r.create_view(3, "AndroidTextInput", {"placeholder": "Benutzername"})
    r.create_view(4, "AndroidTextInput", {"placeholder": "Passwort"})
    r.create_view(5, "View", {"accessibilityRole": "button", "onPress": True})
    r.create_view(6, "Paragraph", {"text": "Login"})
    r.add_view_at(5, 6, 0)
    for i, tag in enumerate((2, 3, 4, 5)):
        r.add_view_at(1, tag, i)
    r.update_layout(1, 0, 0, 380, 600)
    r.update_layout(2, 16, 20, 348, 32)
    r.update_layout(3, 16, 70, 348, 40)
    r.update_layout(4, 16, 120, 348, 40)
    r.update_layout(5, 16, 180, 348, 46)
    r.update_layout(6, 150, 192, 80, 22)
    return r
