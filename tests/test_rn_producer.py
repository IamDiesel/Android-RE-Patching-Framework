# -*- coding: utf-8 -*-
"""S3a: RN-Fabric-Mount-Shim -> ViewTree. Nachgespielte SurfaceMountingManager-Sequenz baut einen
Login-aehnlichen Baum; Layout kommt aus update_layout (kein eigenes Yoga). Headless, IP-neutral."""
from core.java.rn_producer import RnMountRecorder, map_component, install_rn_mount_stubs
from core.java.surface import SceneGraphSurface, snapshot
from core.java.dex_loader import AndroidStubs
from core.java.objects import JString


def test_component_mapping():
    assert map_component("AndroidTextInput") == "EditText"
    assert map_component("Paragraph") == "TextView"
    assert map_component("RCTImageView") == "ImageView"
    assert map_component("UnknownThing") == "View"


def _login_mount():
    r = RnMountRecorder(width=380, height=800)
    r.set_root(1)
    r.create_view(2, "Paragraph", {"text": "Anmelden"})
    r.create_view(3, "AndroidTextInput", {"placeholder": "Benutzername"})
    r.create_view(4, "AndroidTextInput", {"placeholder": "Passwort"})
    r.create_view(5, "View", {"accessibilityRole": "button", "onPress": True})
    r.create_view(6, "Paragraph", {"text": "Login"})
    r.add_view_at(5, 6, 0)
    for i, tag in enumerate((2, 3, 4, 5)):
        r.add_view_at(1, tag, i)
    r.update_layout(1, 0, 0, 380, 600)
    r.update_layout(2, 10, 10, 360, 30)
    r.update_layout(3, 10, 50, 360, 40)
    r.update_layout(4, 10, 100, 360, 40)
    r.update_layout(5, 10, 150, 360, 44)
    r.update_layout(6, 20, 160, 100, 24)
    return r


def test_login_mount_builds_tree():
    surf = _login_mount().surface()
    assert isinstance(surf, SceneGraphSurface)
    snap = snapshot(surf)
    classes = [n["cls"] for n in snap["nodes"]]
    assert classes.count("EditText") == 2
    assert "TextView" in classes and "View" in classes
    assert "Anmelden" in snap["text"] and "Login" in snap["text"]


def test_layout_boxes_from_update_layout():
    surf = _login_mount().surface()
    nodes = {tuple(n["bounds"]): n for n in snapshot(surf)["nodes"]}
    # das Passwort-EditText hat die in update_layout gesetzte Box
    assert (10.0, 100.0, 360.0, 40.0) in nodes
    assert nodes[(10.0, 100.0, 360.0, 40.0)]["cls"] == "EditText"


def test_button_is_hit_testable():
    surf = _login_mount().surface()
    node = surf.hit(100, 165)          # innerhalb der Button-View (10,150,360,44)
    assert node is not None and node.clickable is True


def test_mount_stub_hook_resolves():
    """S3b-Haken: SurfaceMountingManager-Methodennamen landen im Recorder (ueber stubs.resolve)."""
    s = AndroidStubs()
    rec = install_rn_mount_stubs(s)
    rec.set_root(1)
    s.resolve("Lcom/facebook/react/fabric/mounting/SurfaceMountingManager;", "createView",
              "(Ljava/lang/String;ILjava/lang/Object;)V",
              (object(), JString("Paragraph"), 2, {"text": "Hallo"}), False)
    s.resolve("X", "addViewAt", "(III)V", (object(), 1, 2, 0), False)
    s.resolve("X", "updateLayout", "(IFFFF)V", (object(), 2, 5, 5, 100, 20), False)
    snap = snapshot(rec.surface())
    assert "Hallo" in snap["text"]
    assert (5.0, 5.0, 100.0, 20.0) in [tuple(n["bounds"]) for n in snap["nodes"]]
