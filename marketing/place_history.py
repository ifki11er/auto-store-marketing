"""places 테이블에서 영상 생성·업로드와 사진 상태를 관리한다."""
import sqlite3
from datetime import datetime
from pathlib import Path


class PlaceHistory:
    def __init__(self, path):
        self.db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=rw", uri=True)
        self.db.row_factory = sqlite3.Row

    def get(self, place_id):
        row = self.db.execute("SELECT generated_at, publish_at, channel, upload_status, youtube_video_id, posible FROM places WHERE place_id = ?", (place_id,)).fetchone()
        if row is None:
            return None
        return {"생성_시간": row["generated_at"], "업로드_시간": row["publish_at"],
                "지역": row["channel"], "업로드_상태": row["upload_status"],
                "유튜브_영상_ID": row["youtube_video_id"], "실패_사유": row["posible"]}

    def contains(self, place_id):
        row = self.db.execute("SELECT generated_at FROM places WHERE place_id = ?", (place_id,)).fetchone()
        return row is not None and row[0] is not None

    def add(self, place_id, channel=None, planned_publication=None, generated_at=None):
        with self.db:
            result = self.db.execute("UPDATE places SET generated_at = ?, publish_at = ?, channel = ? WHERE place_id = ? AND generated_at IS NULL",
                                     (generated_at or datetime.now().astimezone().isoformat(),
                                      planned_publication.isoformat() if planned_publication else None, channel, place_id))
            if result.rowcount == 0 and self.get(place_id) is None:
                raise ValueError(f"DB에서 가게를 찾을 수 없습니다: {place_id}")

    def set_upload(self, place_id, publish_at, status, video_id=None):
        with self.db:
            result = self.db.execute("UPDATE places SET publish_at = ?, upload_status = ?, youtube_video_id = COALESCE(?, youtube_video_id) WHERE place_id = ?",
                                     (publish_at.isoformat(), status, video_id, place_id))
            if result.rowcount != 1:
                raise ValueError(f"DB에서 가게를 찾을 수 없습니다: {place_id}")

    def set_failure(self, place_id, reason):
        with self.db:
            result = self.db.execute("UPDATE places SET posible = ? WHERE place_id = ?", (reason, place_id))
            if result.rowcount != 1:
                raise ValueError(f"DB에서 가게를 찾을 수 없습니다: {place_id}")

    def set_channel_if_missing(self, place_id, channel):
        with self.db:
            self.db.execute("UPDATE places SET channel = ? WHERE place_id = ? AND (channel IS NULL OR channel = '')", (channel, place_id))

    def allocations(self, channel):
        rows = self.db.execute("SELECT publish_at FROM places WHERE channel = ? AND publish_at IS NOT NULL", (channel,))
        return [datetime.fromisoformat(row[0]) for row in rows]

    def has_in_progress(self):
        return self.db.execute("SELECT 1 FROM places WHERE upload_status = ? LIMIT 1", ("진행중",)).fetchone() is not None
