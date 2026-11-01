"""완성된 영상의 가게 ID를 출력 폴더와 별도로 보관한다."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path


class CompletedPlaces:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.exists():
            self.places = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(self.places, dict):
                raise ValueError("완료 이력 JSON은 가게 ID를 키로 하는 객체여야 합니다")
            changed = False
            for place_id, value in self.places.items():
                if isinstance(value, str):
                    self.places[place_id] = {"생성_시간": value, "업로드_시간": None}
                    changed = True
                elif not isinstance(value, dict):
                    raise ValueError(f"완료 이력의 형식이 잘못되었습니다: {place_id}")
            if changed:
                self.save()
        else:
            self.places = {}
            legacy = self.path.with_suffix(".db")
            if legacy.is_file():
                with closing(sqlite3.connect(legacy.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                    self.places.update((place_id, {"생성_시간": completed_at, "업로드_시간": None})
                                       for place_id, completed_at in db.execute("SELECT place_id, completed_at FROM completed_places"))
            self.save()

    def save(self):
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.places, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def contains(self, place_id):
        return place_id in self.places

    def add(self, place_id, channel=None):
        if place_id not in self.places:
            self.places[place_id] = {"생성_시간": datetime.now().astimezone().isoformat(), "업로드_시간": None, "지역": channel}
            self.save()

    def set_upload(self, place_id, publish_at, status, video_id=None):
        entry = self.places[place_id]
        entry.update({"업로드_시간": publish_at.isoformat(), "업로드_상태": status})
        if video_id:
            entry["유튜브_영상_ID"] = video_id
        self.save()

    def import_videos(self, directories):
        """기존 결과도 삭제 전에 이력에 등록한다."""
        changed = False
        for directory in set(map(Path, directories)):
            if not directory.is_dir():
                continue
            for video in directory.glob("*/video.mp4"):
                place_id = video.parent.name
                if place_id.isdigit() and place_id not in self.places and video.is_file() and video.stat().st_size > 0:
                    created = datetime.fromtimestamp(video.stat().st_mtime).astimezone().isoformat()
                    self.places[place_id] = {"생성_시간": created, "업로드_시간": None}
                    changed = True
        if changed:
            self.save()
