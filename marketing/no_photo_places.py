"""사진이 없어 건너뛴 가게를 기록한다."""
import json
from datetime import datetime
from pathlib import Path


class NoPhotoPlaces:
    def __init__(self, path):
        self.path = Path(path)
        self.places = json.loads(self.path.read_text(encoding="utf-8-sig")) if self.path.exists() else {}
        if not isinstance(self.places, dict):
            raise ValueError("사진 없는 가게 이력은 가게 ID를 키로 하는 JSON 객체여야 합니다")

    def contains(self, place_id):
        return place_id in self.places

    def add(self, place_id, place):
        if place_id in self.places:
            return
        self.places[place_id] = {
            "가게명": place.get("place_name"),
            "place_url": place.get("place_url"),
            "확인_시간": datetime.now().astimezone().isoformat(),
        }
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.places, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
