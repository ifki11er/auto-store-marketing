"""한국 시간 기준 채널별 공개 시각 계산."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from marketing.channel_regions import CHANNEL_ORDER


KOREA_TIME = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class UploadSchedule:
    start_date: date
    end_date: date
    daily_count: int
    publish_times: tuple[time, ...]

    @classmethod
    def from_environment(cls, env):
        raw_date = env.get("YOUTUBE_START_DATE", "").strip()
        try:
            start_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise ValueError("YOUTUBE_START_DATE에 시작 날짜를 YYYY-MM-DD로 입력하세요") from exc
        raw_end = env.get("YOUTUBE_END_DATE", "").strip()
        try:
            end_date = date.fromisoformat(raw_end)
        except ValueError as exc:
            raise ValueError("YOUTUBE_END_DATE에 종료 날짜를 YYYY-MM-DD로 입력하세요") from exc
        if end_date < start_date:
            raise ValueError("종료 날짜는 시작 날짜보다 빠를 수 없습니다")
        try:
            daily_count = int(env.get("YOUTUBE_DAILY_UPLOAD_COUNT", "4"))
        except ValueError as exc:
            raise ValueError("YOUTUBE_DAILY_UPLOAD_COUNT는 양의 정수여야 합니다") from exc
        if daily_count < 1:
            raise ValueError("YOUTUBE_DAILY_UPLOAD_COUNT는 양의 정수여야 합니다")
        raw_times = env.get("YOUTUBE_PUBLISH_TIMES", "00:00,06:00,12:00,18:00")
        try:
            times = tuple(time.fromisoformat(value.strip()) for value in raw_times.split(","))
        except ValueError as exc:
            raise ValueError("YOUTUBE_PUBLISH_TIMES는 쉼표로 구분한 HH:MM 형식이어야 합니다") from exc
        if any(t.tzinfo is not None or t.second or t.microsecond for t in times):
            raise ValueError("공개 시간은 한국 시간 기준 HH:MM 형식이어야 합니다")
        if len(times) != daily_count or len(set(times)) != len(times):
            raise ValueError("공개 시간은 중복 없이 하루 업로드 개수와 같아야 합니다")
        return cls(start_date, end_date, daily_count, tuple(sorted(times)))

    def next_publication(self, now, allocated):
        """한 채널의 기존 공개·예약 시각을 반영해 다음 (시각, 즉시 공개 여부)를 반환한다."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("현재 시각에는 시간대가 필요합니다")
        now = now.astimezone(KOREA_TIME)
        existing = []
        for value in allocated:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("기존 공개·예약 시각에는 시간대가 필요합니다")
            local = value.astimezone(KOREA_TIME)
            if self.start_date <= local.date() <= self.end_date:
                existing.append(local)
        if self.start_date <= now.date() <= self.end_date and not existing:
            return now, True
        day = max(self.start_date, now.date())
        while day <= self.end_date:
            daily = [value for value in existing if value.date() == day]
            if len(daily) < self.daily_count:
                occupied_slots = {value.replace(second=0, microsecond=0) for value in daily}
                for slot_time in self.publish_times:
                    slot = datetime.combine(day, slot_time, KOREA_TIME)
                    if slot > now and slot not in occupied_slots:
                        return slot, False
            day += timedelta(days=1)
        return None

    def next_planned_publication(self, allocated):
        """검수용 영상에 설정된 공개 시간대를 순서대로 배정한다."""
        existing = []
        for value in allocated:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("기존 공개·예약 시각에는 시간대가 필요합니다")
            local = value.astimezone(KOREA_TIME)
            if self.start_date <= local.date() <= self.end_date:
                existing.append(local)
        day = self.start_date
        while day <= self.end_date:
            daily = [value for value in existing if value.date() == day]
            if len(daily) < self.daily_count:
                occupied_slots = {value.replace(second=0, microsecond=0) for value in daily}
                for slot_time in self.publish_times:
                    slot = datetime.combine(day, slot_time, KOREA_TIME)
                    if slot not in occupied_slots:
                        return slot
            day += timedelta(days=1)
        return None


def iter_publication_rounds(schedule, now, allocations=None):
    """기본적으로 모든 채널이 비어 있다고 가정하고 공개 시각을 순회한다."""
    allocations = allocations or {}
    pending = {channel: list(allocations.get(channel, ())) for channel in CHANNEL_ORDER}
    while True:
        round_items = []
        for channel in CHANNEL_ORDER:
            result = schedule.next_publication(now, pending[channel])
            if result is None:
                continue
            publish_at, immediate = result
            round_items.append((channel, publish_at, immediate))
            pending[channel].append(publish_at)
        if not round_items:
            return
        yield round_items
