"""검수된 패션 영상을 지정한 유튜브 채널에 업로드한다."""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fasion.main import load_env
from fasion.upload_files import scheduled_videos
from marketing.upload_schedule import KOREA_TIME
from fasion.youtube_upload import upload_video

LOG = ROOT / "fasion" / "output" / "youtube_uploads.json"


def save(log):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    temp = LOG.with_suffix(".tmp")
    temp.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(LOG)


def main():
    load_env()
    videos = list(scheduled_videos())
    auth_paths = {}
    for country in {data["country"] for _, _, data, _ in videos}:
        auth_path = ROOT / "res" / f"bundafit_{country}.pickle"
        if not auth_path.is_file():
            raise FileNotFoundError(f"{country} 인증 파일이 없습니다: {auth_path}")
        auth_paths[country] = auth_path
    log = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else {}
    for job, video, data, slot in videos:
        key = job.relative_to(ROOT).as_posix()
        if log.get(key, {}).get("status") in ("uploading", "completed"):
            print(f"업로드 이력 확인 필요 또는 완료: {job}", flush=True)
            continue
        log[key] = {"status": "uploading", "scheduled": slot.isoformat()}
        save(log)
        now = datetime.now(KOREA_TIME)
        immediate = slot <= now
        plan = {"유튜브_제목": data["youtube_title"], "유튜브_본문": data["description"], "유튜브_태그": data["tags"]}
        print(f"업로드: {job} ({'즉시 공개' if immediate else slot.isoformat()})", flush=True)
        video_id = upload_video(auth_paths[data["country"]], video, plan, now if immediate else slot, immediate, None)
        log[key].update({"status": "completed", "video_id": video_id})
        save(log)
        print(f"업로드 완료: {video_id}", flush=True)


if __name__ == "__main__":
    main()
