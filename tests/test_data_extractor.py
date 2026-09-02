import pytest
import json
import sys
import os

# Pfad-Fix: Root-Verzeichnis zum Python-Path hinzufügen, um lokale Module zu finden
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.data_extractor import DataExtractor


def test_extract_json_path():
    rule = {"source": "res_body", "ext_type": "json", "param1": "user.id"}
    res_body = json.dumps({"user": {"id": 1337, "name": "Test"}})

    result = DataExtractor.extract(rule, "GET", "/api/user", "", "", "", res_body)
    assert result == "1337"


def test_extract_regex():
    rule = {"source": "req_headers", "ext_type": "regex", "param1": r"Bearer\s([A-Za-z0-9]+)"}
    req_h = "Authorization: Bearer Token123\nAccept: application/json"

    result = DataExtractor.extract(rule, "GET", "/api/data", req_h, "", "", "")
    assert result == "Token123"


def test_extract_offset_string():
    rule = {"source": "req_body", "ext_type": "offset", "param1": "4", "param2": "5", "param3": "string"}
    req_b = "1234HELLO999"

    result = DataExtractor.extract(rule, "POST", "/api/bin", "", req_b, "", "")
    assert result == "HELLO"


def test_filter_rejection():
    rule = {"filter_method": "POST", "filter_url": "/api/v2"}

    res1 = DataExtractor.extract(rule, "GET", "/api/v2/test", "", "", "", "")
    assert res1 == ""

    res2 = DataExtractor.extract(rule, "POST", "/api/v1/test", "", "", "", "")
    assert res2 == ""