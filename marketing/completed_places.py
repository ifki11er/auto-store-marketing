"""완성된 영상의 가게 ID를 출력 폴더와 별도로 보관한다."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from marketing.channel_regions import channel_for_address
from marketing.upload_schedule import KOREA_TIME


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

    def add(self, place_id, channel=None, planned_publication=None):
        if place_id not in self.places:
            self.places[place_id] = {"생성_시간": datetime.now().astimezone().isoformat(),
                                     "업로드_시간": planned_publication.isoformat() if planned_publication is not None else None,
                                     "지역": channel}
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
            videos = [(video, None) for video in directory.glob("*/video.mp4")]
            for video in directory.glob("*/*/*/video.mp4"):
                try:
                    day = datetime.strptime(video.parent.parent.parent.name, "%Y-%m-%d").date()
                    slot = video.parent.parent.name
                    planned = None
                    if slot not in {"즉시공개", "업로드없음"}:
                        try:
                            if len(slot) == 4 and slot.isascii() and slot.isdigit():
                                parsed = datetime.strptime(slot, "%H%M").time()
                            else:
                                parsed = datetime.strptime(slot, "%H").time()
                        except ValueError:
                            try:
                                parsed = datetime.strptime(slot, "%H-%M").time()
                            except ValueError:
                                datetime.strptime(slot, "%H-%M-%S")
                                parsed = None  # 이전 처리 시작 시각 폴더는 예약 시간이 아니다.
                        if parsed is not None:
                            planned = datetime.combine(day, parsed, KOREA_TIME)
                except ValueError:
                    continue
                videos.append((video, planned))
            for video, planned in videos:
                place_id = video.parent.name
                if place_id.isdigit() and place_id not in self.places and video.is_file() and video.stat().st_size > 0:
                    created = datetime.fromtimestamp(video.stat().st_mtime).astimezone().isoformat()
                    self.places[place_id] = {"생성_시간": created, "업로드_시간": None}
                    place_file = video.parent / "01_place.json"
                    if planned is not None and place_file.is_file():
                        try:
                            place = json.loads(place_file.read_text(encoding="utf-8-sig"))
                            channel = channel_for_address(place.get("address_name") or place.get("road_address_name")) if str(place.get("place_id")) == place_id else None
                        except (ValueError, OSError, AttributeError):
                            channel = None
                        if channel:
                            self.places[place_id].update({"지역": channel, "업로드_시간": planned.isoformat()})
                    changed = True
        if changed:
            self.save()
