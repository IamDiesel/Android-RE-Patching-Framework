import os
import json

class FavoriteService:
    def __init__(self, base_dir: str):
        data_dir = os.path.join(base_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        self.fav_file = os.path.join(data_dir, "favorite_patches.json")
        self.favs = self.load_favs()

    def load_favs(self):
        if os.path.exists(self.fav_file):
            try:
                with open(self.fav_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                pass
        return []

    def save_favs(self):
        with open(self.fav_file, "w", encoding="utf-8") as f:
            json.dump(self.favs, f, indent=4)

    def add_favorite(self, fav_dict):
        self.favs.append(fav_dict)
        self.save_favs()

    def delete_favorite(self, idx):
        if 0 <= idx < len(self.favs):
            del self.favs[idx]
            self.save_favs()