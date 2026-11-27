"""설정 기간의 영상을 인스타그램용 지역 폴더로 정리한다."""
import json
import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from marketing.channel_regions import channel_for_address
from marketing.main import load_env

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "output"
RESULT_DIR = ROOT / "test" / "results" / "instagram"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def filename_for(day, slot, place_id, place_name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", place_name).strip(" .")[:60]
    return f"{day}_{slot}_{place_id}_{name or 'place'}"


def caption_for(plan):
    title = plan.get("유튜브_제목")
    description = plan.get("유튜브_본문")
    tags = plan.get("유튜브_태그")
    if not isinstance(title, str) or not title.strip() or not isinstance(description, str) or not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("기획 파일의 제목, 설명 또는 태그 형식이 올바르지 않습니다")
    hashtags = []
    for tag in tags:
        name = "".join(char for char in tag.strip().lstrip("#") if char.isalnum() or char == "_")
        if name and "#" + name not in hashtags:
            hashtags.append("#" + name)
    return "[제목] " + title.strip() + "\n" + description.strip() + ("\n\n" + " ".join(hashtags) if hashtags else "") + "\n"


def main():
    load_env(ROOT / ".env")
    try:
        start = date.fromisoformat(os.environ.get("YOUTUBE_START_DATE", "").strip())
        end = date.fromisoformat(os.environ.get("YOUTUBE_END_DATE", "").strip())
    except ValueError as exc:
        raise ValueError(".env의 YOUTUBE_START_DATE와 YOUTUBE_END_DATE를 YYYY-MM-DD 형식으로 입력하세요") from exc
    if end < start:
        raise ValueError("종료 날짜는 시작 날짜보다 빠를 수 없습니다")

    run_folder = RESULT_DIR / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    captions = {}
    created = failed = 0
    day = start
    while day <= end:
        day_folder = SOURCE_DIR / day.isoformat()
        if day_folder.is_dir():
            for slot_folder in sorted(day_folder.iterdir()):
                if not slot_folder.is_dir() or not re.fullmatch(r"[0-9]{4}", slot_folder.name):
                    continue
                try:
                    datetime.strptime(slot_folder.name, "%H%M")
                except ValueError:
                    print(f"시간 폴더가 올바르지 않아 건너뜀: {slot_folder}", flush=True)
                    continue
                for place_folder in sorted(slot_folder.iterdir()):
                    if not place_folder.is_dir() or not place_folder.name.isdigit():
                        continue
                    video = place_folder / "video.mp4"
                    if not video.is_file() or video.stat().st_size == 0:
                        continue
                    try:
                        place = read_json(place_folder / "01_place.json")
                        plan = read_json(place_folder / "05_plan.json")
                        if str(place.get("place_id")) != place_folder.name:
                            raise ValueError("가게 ID가 폴더 이름과 일치하지 않습니다")
                        place_name = place.get("place_name")
                        if not isinstance(place_name, str) or not place_name.strip():
                            raise ValueError("가게 이름이 없습니다")
                        channel = channel_for_address(place.get("address_name") or place.get("road_address_name"))
                        if not channel:
                            raise ValueError("지역을 확인할 수 없습니다")
                        caption = caption_for(plan)
                        target_folder = run_folder / channel
                        target_folder.mkdir(parents=True, exist_ok=True)
                        basename = filename_for(day.isoformat(), slot_folder.name, place_folder.name, place_name)
                        target_video = target_folder / (basename + ".mp4")
                        if target_video.exists():
                            raise FileExistsError(f"영상 파일이 이미 있습니다: {target_video}")
                        shutil.copy2(video, target_video)
                        created += 1
                        captions.setdefault(channel, []).append((target_video.name, caption))
                        print(f"{channel}: {target_video.name}", flush=True)
                    except (OSError, ValueError, TypeError, AttributeError) as exc:
                        failed += 1
                        print(f"정리 실패: {place_folder} ({exc})", file=sys.stderr, flush=True)
        day += timedelta(days=1)
    if captions:
        for channel, entries in sorted(captions.items()):
            sections = []
            for name, caption in entries:
                sections.append(f"[영상] {name}\n{caption}")
            (run_folder / channel / "전체_설명.txt").write_text("\n".join(sections), encoding="utf-8-sig")
    print(f"정리 완료: 영상 {created}개, 실패 {failed}개", flush=True)
    print(f"결과 폴더: {run_folder}", flush=True)


if __name__ == "__main__":
    main()
