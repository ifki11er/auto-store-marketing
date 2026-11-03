"""완성된 영상의 날짜·시간 폴더를 공개 일정으로 읽는다."""
import json
from datetime import datetime, timedelta

from marketing.channel_regions import CHANNEL_ORDER, channel_for_address
from marketing.upload_schedule import KOREA_TIME


def folder_allocations(output, schedule):
    allocated = {channel: [] for channel in CHANNEL_ORDER}
    day = schedule.start_date
    while day <= schedule.end_date:
        for clock in schedule.publish_times:
            slot = datetime.combine(day, clock, KOREA_TIME)
            folder = output / day.isoformat() / clock.strftime("%H%M")
            if not folder.is_dir():
                continue
            for place_dir in folder.iterdir():
                if not place_dir.is_dir() or not place_dir.name.isdigit():
                    continue
                video = place_dir / "video.mp4"
                if not video.is_file() or video.stat().st_size == 0:
                    continue
                place_file = place_dir / "01_place.json"
                if not place_file.is_file():
                    raise ValueError(f"영상의 가게 정보가 없습니다: {place_dir}")
                place = json.loads(place_file.read_text(encoding="utf-8-sig"))
                channel = channel_for_address(place.get("address_name") or place.get("road_address_name"))
                if str(place.get("place_id")) != place_dir.name or channel is None:
                    raise ValueError(f"영상의 가게 ID 또는 지역을 확인할 수 없습니다: {place_dir}")
                allocated[channel].append(slot)
        day += timedelta(days=1)
    return allocated
