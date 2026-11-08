"""단계별 수동 테스트의 공통 파일 처리."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "test" / "results"


def folder(args):
    if not args.place_id.isdigit():
        raise ValueError("장소 ID는 숫자여야 합니다")
    path = args.results / args.place_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {path}")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def photos(path):
    images = sorted(p for p in path.glob("photo_*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    if not images:
        raise FileNotFoundError(f"사진이 없습니다: {path}")
    return images
