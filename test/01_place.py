"""DB에서 장소 한 건을 읽는다."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sqlite3
from test.common import folder, save_json

PLACE_ID = "1843998815"  # 테스트할 카카오 장소 ID
RESULTS_DIR = Path(__file__).resolve().parent / "results"
DB_PATH = Path(__file__).resolve().parent.parent / "kakao_places_filtered.db"

args = SimpleNamespace(place_id=PLACE_ID, results=RESULTS_DIR, db=DB_PATH)
with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as db:
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM places WHERE place_id = ?", (args.place_id,)).fetchone()
if row is None:
    raise SystemExit(f"장소를 찾지 못했습니다: {args.place_id}")
save_json(folder(args) / "place.json", dict(row))
