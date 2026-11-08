"""지정한 기간에 생성하고 검수한 영상을 지역별 유튜브 채널에 올린다."""
import json
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from marketing.channel_regions import CHANNEL_ORDER, channel_for_address
from marketing.place_history import PlaceHistory
from marketing.main import load_env
from marketing.upload_schedule import KOREA_TIME
from marketing.youtube_upload import upload_video

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
DB_PATH = ROOT / "kakao_places_filtered.db"
AUTH_DIR = ROOT / "res"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def slot_time(name):
    if len(name) == 4 and name.isascii() and name.isdigit():
        try:
            return time(int(name[:2]), int(name[2:]))
        except ValueError:
            return None
    parts = name.split("-")
    if len(parts) not in (1, 2, 3) or any(not p.isascii() or not p.isdigit() or len(p) != 2 for p in parts):
        return None
    values = [int(part) for part in parts] + [0] * (3 - len(parts))
    try:
        return time(*values)
    except ValueError:
        return None


def main():
    load_env(ROOT / ".env")
    try:
        source_start = date.fromisoformat(os.environ.get("YOUTUBE_START_DATE", "").strip())
        source_end = date.fromisoformat(os.environ.get("YOUTUBE_END_DATE", "").strip())
    except ValueError as exc:
        raise ValueError("YOUTUBE_START_DATE와 YOUTUBE_END_DATE를 YYYY-MM-DD 형식으로 입력하세요") from exc
    if source_end < source_start:
        raise ValueError("영상 생성 종료 날짜는 시작 날짜보다 빠를 수 없습니다")
    sources = []
    day = source_start
    while day <= source_end:
        day_folder = OUTPUT_DIR / day.isoformat()
        if day_folder.is_dir():
            for source in sorted(day_folder.iterdir()):
                if not source.is_dir():
                    continue
                parsed = slot_time(source.name)
                if parsed is not None:
                    sources.append((source, datetime.combine(day, parsed, KOREA_TIME)))
        day += timedelta(days=1)
    if not sources:
        print(f"{source_start} ~ {source_end} 기간에 해당하는 시간 폴더가 없습니다.", flush=True)
        return
    history = PlaceHistory(DB_PATH)
    try:
        if history.has_in_progress():
            raise ValueError("업로드 결과가 확인되지 않은 이력이 있습니다. 유튜브에서 확인한 뒤 DB의 upload_status를 수정하세요")

        groups = {channel: [] for channel in CHANNEL_ORDER}
        seen_places = set()
        for folder, slot in ((folder, slot) for source, slot in sources for folder in sorted(source.iterdir())):
            if not folder.is_dir() or not folder.name.isdigit():
                continue
            video = folder / "video.mp4"
            if not video.is_file() or video.stat().st_size == 0:
                print(f"완성 영상 없음: {folder.name} 건너뜀", flush=True)
                continue
            entry = history.get(folder.name)
            if entry is None:
                raise ValueError(f"DB에서 가게를 찾을 수 없습니다: {folder.name}")
            if entry.get("업로드_상태") == "완료":
                print(f"이미 업로드됨: {folder.name} 건너뜀", flush=True)
                continue
            if entry.get("업로드_상태") not in (None, ""):
                raise ValueError(f"확인할 수 없는 업로드 이력: {folder.name} ({entry['업로드_상태']})")
            if folder.name in seen_places:
                raise ValueError(f"선택한 기간에 같은 가게의 미업로드 영상이 여러 개 있습니다: {folder.name}. 업로드할 영상 하나만 남겨 주세요.")
            seen_places.add(folder.name)
            place_path, plan_path = folder / "01_place.json", folder / "05_plan.json"
            if not place_path.is_file() or not plan_path.is_file():
                raise FileNotFoundError(f"가게 정보 또는 기획 파일이 없습니다: {folder}")
            place, plan = read_json(place_path), read_json(plan_path)
            if str(place.get("place_id")) != folder.name:
                raise ValueError(f"가게 ID가 폴더와 일치하지 않습니다: {folder}")
            channel = channel_for_address(place.get("address_name") or place.get("road_address_name"))
            if not channel:
                raise ValueError(f"지역 채널을 확인할 수 없습니다: {folder}")
            if entry.get("지역") and entry["지역"] != channel:
                raise ValueError(f"완료 이력의 지역과 가게 주소가 다릅니다: {folder.name}")
            title, description, tags = (plan.get(key) for key in ("유튜브_제목", "유튜브_본문", "유튜브_태그"))
            if not isinstance(title, str) or not title.strip() or not isinstance(description, str) or not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                raise ValueError(f"유튜브 제목·본문·태그가 올바르지 않습니다: {folder}")
            groups[channel].append((folder.name, place, plan, video, slot))

        missing = [c for c, items in groups.items() if items and not (AUTH_DIR / f"{c}.pickle").is_file()]
        if missing:
            raise FileNotFoundError(f"유튜브 인증 파일이 없습니다: {', '.join(missing)}")
        total = sum(len(items) for items in groups.values())
        if not total:
            print("업로드할 영상이 없습니다.", flush=True)
            return
        print(f"{source_start} ~ {source_end} 검수 영상 {total}개 업로드 시작", flush=True)
        completed = 0
        while any(groups.values()):
            for channel in CHANNEL_ORDER:
                if not groups[channel]:
                    continue
                place_id, place, plan, video, slot = groups[channel].pop(0)
                now = datetime.now(KOREA_TIME)
                publication = (now, True) if slot <= now else (slot, False)
                publish_at, immediate = publication
                if not history.contains(place_id):
                    history.add(place_id, channel, generated_at=datetime.fromtimestamp(video.stat().st_mtime).astimezone().isoformat())
                else:
                    history.set_channel_if_missing(place_id, channel)
                history.set_upload(place_id, publish_at, "진행중")
                print(f"업로드: {channel} {place_id} | {'즉시 공개' if immediate else '예약 공개'} {publish_at:%Y-%m-%d %H:%M:%S %z}", flush=True)
                video_id = upload_video(AUTH_DIR / f"{channel}.pickle", video, plan, publish_at, immediate, place.get("place_name"))
                history.set_upload(place_id, publish_at, "완료", video_id)
                completed += 1
                print(f"업로드 완료: {place_id} ({video_id})", flush=True)
        print(f"업로드 종료: {completed}/{total}개 완료", flush=True)

    finally:
        history.db.close()

if __name__ == "__main__":
    main()
