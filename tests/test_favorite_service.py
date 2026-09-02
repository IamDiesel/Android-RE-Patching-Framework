import os
import json
import tempfile
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.favorite_service import FavoriteService


def test_favorite_service():
    with tempfile.TemporaryDirectory() as tmpdir:
        service = FavoriteService(tmpdir)
        assert len(service.favs) == 0

        # Hinzufügen
        service.add_favorite({"name": "Test1", "patches": []})
        assert len(service.favs) == 1

        # Test Save/Load
        service2 = FavoriteService(tmpdir)
        assert len(service2.favs) == 1
        assert service2.favs[0]["name"] == "Test1"

        # Löschen
        service2.delete_favorite(0)
        assert len(service2.favs) == 0