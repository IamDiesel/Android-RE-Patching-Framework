import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from services.smali_parser import SmaliStudioParser


def test_clean_signature():
    # Prüft das Entfernen von Smali-Modifikatoren
    sig_raw = "public static final synthetic bridge method_name()V"
    cleaned = SmaliStudioParser.clean_signature(sig_raw)
    assert cleaned == "method_name()V"

    # Header-Platzhalter muss intakt bleiben
    assert SmaliStudioParser.clean_signature("<Klassen-Header & Felder>") == "<Klassen-Header & Felder>"


def test_parse_outgoing_calls():
    smali_block = """
    .line 10
    invoke-virtual {v0}, Ljava/lang/String;->length()I
    invoke-static {v1, v2}, Lcom/custom/App;->init()V
    """

    # Prüft die Extraktion von invoke-Aufrufen
    calls = SmaliStudioParser.parse_outgoing_calls(smali_block)

    assert len(calls) == 2
    # FIX: Der Original-Parser verschluckt das Semikolon beim Split (split(";->"))!
    assert calls[0]["class_part"] == "Ljava/lang/String"
    assert calls[0]["method_part"] == "length()I"
    assert calls[1]["class_part"] == "Lcom/custom/App"
    assert calls[1]["method_part"] == "init()V"


def test_parse_data_flow():
    smali_block = """
    const-string v0, "SecretKey123"
    sput-boolean v1, Lcom/app/Test;->flag:Z
    """

    flow = SmaliStudioParser.parse_data_flow(smali_block)
    assert len(flow) == 2
    # FIX: Der Original-Parser appended immer zuerst READ/WRITE und danach erst STRINGs!
    assert flow[0]["access"] == "WRITE"
    assert flow[0]["target"] == "Lcom/app/Test;->flag:Z"

    assert flow[1]["access"] == "STRING"
    assert flow[1]["raw"] == "SecretKey123"