"""검수할 영상과 유튜브 업로드 결과를 임시 목록으로 기록한다."""
import json
from datetime import datetime
from pathlib import Path

from marketing.channel_regions import channel_for_address


PROCESS_FILE = Path(__file__).resolve().parent.parent / "process.json"


def record_process(place, video_path, publish_at=None, uploaded=False, generated_at=None):
    path = PROCESS_FILE
    items = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else []
    if not isinstance(items, list):
        raise ValueError("process.json은 영상 목록이어야 합니다")
    video = str(Path(video_path).resolve())
    item = next((entry for entry in reversed(items) if entry.get("영상_경로") == video), None) if uploaded else None
    if item is None:
        item = {
            "id": str(place.get("place_id", "")),
            "가게명": place.get("place_name"),
            "지역": channel_for_address(place.get("address_name") or place.get("road_address_name")),
            "생성_일자": generated_at or datetime.now().astimezone().isoformat(),
            "유튜브_업로드_일자": None,
            "업로드_상태": "미업로드",
            "영상_경로": video,
        }
        items.append(item)
    if publish_at is not None:
        item["유튜브_업로드_일자"] = publish_at.isoformat()
    if uploaded:
        item["업로드_상태"] = "완료"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
