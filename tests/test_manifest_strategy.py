import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.pipeline.steps.apk_steps import SmaliOnlyStrategy, ApkEditorStrategy
import core.pipeline.steps.apk_steps as apk_steps


class MockConfig:
    def __init__(self):
        self.paths = {"DEST_DIR": "mock_dest_dir"}


class MockEngine:
    """Aktualisierter Mock der PipelineEngine für Unit-Tests."""
    def __init__(self):
        self.cmd_history = []
        self.nsc_injected = False
        self.cfg = MockConfig()

    def log(self, msg):
        pass

    def get_unpacked_dir_name(self):
        return "base_unpacked"

    def format_cmd(self, text, extra_vars=None):
        # Simuliert das Formatieren und ersetzt Variablen für den Test
        return text.replace("{APKEDITOR_JAR}", "tools/APKEditor.jar")


def test_smali_only_strategy(monkeypatch):
    engine = MockEngine()
    strategy = SmaliOnlyStrategy()

    # Monkeypatch fängt Systembefehle ab und schreibt stattdessen in die History
    monkeypatch.setattr(apk_steps, "inject_nsc", lambda ctx: setattr(ctx, 'nsc_injected', True) or True)
    monkeypatch.setattr(apk_steps, "run_build_cmd", lambda cmd, ctx: ctx.cmd_history.append(ctx.format_cmd(cmd)) or True)

    strategy.patch_manifest(engine)
    assert engine.nsc_injected is False

    strategy.build(engine)
    assert "apktool b base_unpacked -o base.apk" in engine.cmd_history[0]


def test_apkeditor_strategy(monkeypatch):
    engine = MockEngine()
    strategy = ApkEditorStrategy()

    monkeypatch.setattr(apk_steps, "inject_nsc", lambda ctx: setattr(ctx, 'nsc_injected', True) or True)
    monkeypatch.setattr(apk_steps, "run_build_cmd", lambda cmd, ctx: ctx.cmd_history.append(ctx.format_cmd(cmd)) or True)

    strategy.patch_manifest(engine)
    assert engine.nsc_injected is True

    strategy.build(engine)
    assert 'java -jar "tools/APKEditor.jar" b -f -i base_unpacked -o base.apk' in engine.cmd_history[0]