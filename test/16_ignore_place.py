"""지정한 가게를 자동 처리 대상에서 제외한다."""
import sqlite3
from pathlib import Path

PLACE_ID = "1004909972"  # 제외할 카카오 장소 ID
DB_PATH = Path(__file__).resolve().parent.parent / "kakao_places_filtered.db"

if not PLACE_ID.strip():
    raise SystemExit("PLACE_ID에 제외할 가게 ID를 입력하세요.")

with sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=rw", uri=True) as db:
    result = db.execute(
        "UPDATE places SET posible = ?, generated_at = NULL, publish_at = NULL, channel = NULL WHERE place_id = ?",
        ("no use", PLACE_ID.strip()),
    )
    if result.rowcount != 1:
        raise ValueError(f"DB에서 가게를 찾을 수 없습니다: {PLACE_ID.strip()}")
print(f"처리 대상에서 제외했습니다: {PLACE_ID.strip()}")
