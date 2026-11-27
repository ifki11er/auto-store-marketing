"""패션 영상의 예약 폴더와 메타데이터를 읽는다."""
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from marketing.upload_schedule import KOREA_TIME
from fasion.locale_settings import selected_locales

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "fasion" / "output"


def scheduled_videos():
    try:
        start = date.fromisoformat(os.environ.get("YOUTUBE_START_DATE", "").strip())
        end = date.fromisoformat(os.environ.get("YOUTUBE_END_DATE", "").strip())
    except ValueError as exc:
        raise ValueError("YOUTUBE_START_DATE와 YOUTUBE_END_DATE를 확인하세요") from exc
    if end < start:
        raise ValueError("종료 날짜가 시작 날짜보다 빠릅니다")
    selected = {country for country, _ in selected_locales()}
    day = start
    while day <= end:
      for folder in [*(OUTPUT / country / day.isoformat() for country in sorted(selected)), OUTPUT / day.isoformat()]:
        for slot_dir in sorted(folder.iterdir()) if folder.is_dir() else []:
            try:
                if not slot_dir.is_dir() or len(slot_dir.name) != 4 or not slot_dir.name.isascii() or not slot_dir.name.isdigit():
                    continue
                slot = datetime.strptime(slot_dir.name, "%H%M").time()
            except ValueError:
                continue
            for job in sorted(slot_dir.iterdir()):
                video = job / "07_final.mp4"
                metadata = job / "03_metadata.json"
                if not video.exists():
                    video = job / "final.mp4"
                if not metadata.exists():
                    metadata = job / "metadata.json"
                if not job.is_dir() or not video.is_file() or not video.stat().st_size:
                    continue
                if not metadata.is_file():
                    raise FileNotFoundError(f"영상 메타데이터가 없습니다: {job}")
                data = json.loads(metadata.read_text(encoding="utf-8-sig"))
                country = data.get("country", "kr")
                if country not in selected:
                    continue
                if folder.parent != OUTPUT and folder.parent.name != country:
                    raise ValueError(f"영상 폴더의 언어와 메타데이터가 다릅니다: {job}")
                data["country"] = country
                if not isinstance(data.get("youtube_title"), str) or not data["youtube_title"].strip() or not isinstance(data.get("description"), str) or not isinstance(data.get("tags"), list) or any(not isinstance(tag, str) for tag in data["tags"]):
                    raise ValueError(f"유튜브 메타데이터가 올바르지 않습니다: {metadata}")
                yield job, video, data, datetime.combine(day, slot, KOREA_TIME)
      day += timedelta(days=1)
