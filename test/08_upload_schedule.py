""".env의 설정으로 지역별 공개 일정을 계산한다. 실제 업로드는 하지 않는다."""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from marketing.upload_schedule import KOREA_TIME, UploadSchedule, iter_publication_rounds


def schedule_settings():
    keys = ("YOUTUBE_START_DATE", "YOUTUBE_END_DATE", "YOUTUBE_DAILY_UPLOAD_COUNT", "YOUTUBE_PUBLISH_TIMES")
    values = {}
    env_path = ROOT / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.strip().partition("=")
            if separator and key.strip() in keys:
                values[key.strip()] = value.strip().strip("\"'")
    values.update({key: os.environ[key] for key in keys if key in os.environ})
    return values


if __name__ == "__main__":
    schedule = UploadSchedule.from_environment(schedule_settings())
    now = datetime.now(KOREA_TIME)
    results = []
    for number, items in enumerate(iter_publication_rounds(schedule, now), 1):
        for channel, publish_at, immediate in items:
            entry = {"순회": number, "지역": channel, "공개시각": publish_at.isoformat(),
                     "공개방식": "즉시 공개" if immediate else "예약 공개"}
            results.append(entry)
            print(f"{number}회차 | {channel} | {publish_at:%Y-%m-%d %H:%M:%S %z} | {entry['공개방식']}")
    output = ROOT / "test" / "results" / "08_upload_schedule.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"총 {len(results)}개 일정 저장: {output}")
