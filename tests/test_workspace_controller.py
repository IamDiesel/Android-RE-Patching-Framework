import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ui.controllers.workspace_controller import WorkspaceController


class MockApp:
    def __init__(self):
        self.current_id = "TEST-SESSION-ID"

        class MockCfg:
            config = {"APP_PACKAGE": "com.test", "BASE_DIR": "."}

            def save(self): pass

        self.cfg = MockCfg()

        class MockHistory:
            def __init__(self): self.records = []

            def add_record(self, r): self.records.append(r)

        self.history = MockHistory()


def test_save_session_result():
    app = MockApp()
    controller = WorkspaceController(None, app)

    # Testen des Auslagerns aus der UI
    controller.save_session_result("MyPatch", "1.0", "WORKING", "Lookin good", [{"type": "hex"}])

    assert len(app.history.records) == 1
    record = app.history.records[0]

    assert record["name"] == "MyPatch"
    assert record["result"] == "WORKING"
    assert record["id"] == "TEST-SESSION-ID"
    assert record["app_package"] == "com.test"
    assert record["patches"][0]["type"] == "hex"