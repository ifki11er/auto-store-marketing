"""지정한 가게들의 영상 생성 과정을 순서대로 실행한다."""
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from openai import OpenAI
from playwright.sync_api import sync_playwright
from marketing.main import (ROOT, NoUsableReferencePhotos, add_subtitles, analyze, failure_reason, generate_qwen_batch, generate_video_without_subtitles, narration_reference, qwen_tts_enabled,
                            photo_manifest, photos_for, place_availability, plan, recorded_client,
                            reviews_for, selected_photos, write_json)
from marketing.place_history import PlaceHistory
from marketing.process_log import record_process

PLACE_IDS = [
    '100144306',
    # '1002561615',
    # '1003487151',
    # '1005068736',
    # '10039855',
    # '100350274',
    # '1001485955',
    # '1008994805',
    # '1009039871',
    # '100563063',
    # '1001502265',
    # '1002063421',
    # '1002637486',
    # '1009076133'
    
]
DB_PATH = ROOT / "kakao_places_filtered.db"
OUTPUT_DIR = ROOT / "test" / "results" / "07_one_place"
COMFY_URL = "http://127.0.0.1:8188"
MAX_PHOTOS = 10


def record_failure(place_id, reason):
    history = PlaceHistory(DB_PATH)
    try:
        history.set_failure(place_id, reason)
    finally:
        history.db.close()


def process_place(place_id):
    with sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM places WHERE place_id = ?", (place_id,)).fetchone()
        if row is None:
            raise ValueError(f"가게를 찾을 수 없습니다: {place_id}")
        place = dict(row)
        for key in ("generated_at", "publish_at", "uploaded_at", "channel", "posible", "upload_status", "youtube_video_id"):
            place.pop(key, None)

    print(f"가게 처리: {place_id} {place['place_name']}", flush=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            availability = place_availability(browser, place["place_url"])
            with tempfile.TemporaryDirectory() as temp_dir:
                review_photos = []
                reviews = reviews_for(browser, place["place_url"], availability,
                                      Path(temp_dir) / "review_photos", review_photos)
                photos = photos_for(browser, place["place_url"], Path(temp_dir) / "photos", MAX_PHOTOS, availability)
                if len(photos) + len(review_photos) < 2:
                    record_failure(place_id, "less than 2 photos")
                    print("사진 2장 미만: 영상 생성을 중단합니다", flush=True)
                    return
                folder = OUTPUT_DIR / place_id
                photo_folder = folder / "03_photos"
                photo_folder.mkdir(parents=True)
                photos = [Path(shutil.move(str(photo), str(photo_folder / photo.name))) for photo in photos + review_photos]
        finally:
            browser.close()

    write_json(folder / "01_place.json", place)
    write_json(folder / "02_reviews.json", reviews)
    write_json(folder / "04_analysis_photos.json", photo_manifest(photos))
    client = OpenAI()
    analysis = analyze(recorded_client(client, folder, "analysis", "04"), photos)
    (folder / "04_analysis.txt").write_text(analysis, encoding="utf-8")
    try:
        result = plan(recorded_client(client, folder, "plan", "05"), place, reviews, analysis, photos)
    except NoUsableReferencePhotos:
        record_failure(place_id, "too much text")
        raise
    record_failure(place_id, None)
    write_json(folder / "05_plan.json", result)
    write_json(folder / "05_marketing.json", {"가게": place, "후기": reviews, "사진_분석": analysis, "기획": result})
    return {"place": place, "place_id": place_id, "folder": folder, "photos": photos, "result": result}


def main():
    if not PLACE_IDS or any(not isinstance(place_id, str) or not place_id.isdigit() for place_id in PLACE_IDS):
        raise ValueError("PLACE_IDS에 테스트할 가게 ID를 문자열 목록으로 입력하세요")
    prepared = []
    for place_id in PLACE_IDS:
        try:
            job = process_place(place_id)
            if job:
                prepared.append(job)
        except Exception as exc:
            reason = failure_reason(exc, "test")
            try:
                record_failure(place_id, reason)
            except Exception as db_exc:
                print(f"실패 사유 저장 실패: {place_id} ({db_exc})", file=sys.stderr, flush=True)
            print(f"가게 처리 실패: {place_id} ({reason}: {exc})", file=sys.stderr, flush=True)
    voices = generate_qwen_batch(prepared) if qwen_tts_enabled() else {}
    client = OpenAI()
    for job in prepared:
        place, place_id, folder, result = job["place"], job["place_id"], job["folder"], job["result"]
        try:
            voice = voices.get(str(folder.resolve()))
            if qwen_tts_enabled() and (not voice or voice.get("error") or not voice.get("clips")):
                raise ValueError(voice.get("error", "Qwen 음성 결과가 없습니다") if voice else "Qwen 음성 결과가 없습니다")
            raw_video = folder / "06_video_raw.mp4"
            reference = None if qwen_tts_enabled() else narration_reference(result["15초_스크립트"])
            generate_video_without_subtitles(client, COMFY_URL, selected_photos(result, job["photos"]), result["minimax_prompt"], raw_video, reference)
            add_subtitles(raw_video, folder / "video.mp4", result["15초_스크립트"],
                          subtitle_name="06_subtitles.srt", place_name=place["place_name"], voice_clips=voice["clips"] if voice else None)
            record_process(place, folder / "video.mp4")
            print(f"완료: {folder}", flush=True)
        except Exception as exc:
            reason = failure_reason(exc, "test")
            try:
                record_failure(place_id, reason)
            except Exception as db_exc:
                print(f"실패 사유 저장 실패: {place_id} ({db_exc})", file=sys.stderr, flush=True)
            print(f"가게 처리 실패: {place_id} ({reason}: {exc})", file=sys.stderr, flush=True)
    print("업로드 시간:", flush=True)
    with closing(sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for place_id in PLACE_IDS:
            row = db.execute("SELECT publish_at FROM places WHERE place_id = ?", (place_id,)).fetchone()
            upload_time = "가게를 찾을 수 없음" if row is None else (row[0] or "등록된 업로드 시간 없음")
            print(f"- {place_id}: {upload_time}", flush=True)


if __name__ == "__main__":
    main()
