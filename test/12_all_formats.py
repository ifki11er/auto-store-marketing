"""한 가게를 자동 포함 모든 포맷으로 기획하고 선택적으로 영상을 생성한다."""
import os
import shutil
import sqlite3
import sys
import tempfile
import traceback
from contextlib import closing
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from openai import OpenAI
from playwright.sync_api import sync_playwright
from marketing.main import (
    ROOT, add_subtitles, analyze, generate_qwen_batch, generate_video_without_subtitles, narration_reference, qwen_tts_enabled,
    photo_manifest, photos_for, place_availability, plan, recorded_client,
    reviews_for, selected_photos, write_json,
)
from marketing.prompts import FORMAT_PROMPTS
from marketing.process_log import record_process

PLACE_ID = "1586856250"  # 비교할 가게의 카카오 장소 ID
DB_PATH = ROOT / "kakao_places_filtered.db"
RESULTS_DIR = ROOT / "test" / "results" / "12_all_formats"
COMFY_URL = "http://127.0.0.1:8188"
MAX_PHOTOS = 10
MAX_FORMATS = 4  # 확인할 포맷 수 (자동부터 순서대로)
GENERATE_VIDEO = True  # 기획만 확인: False, 영상까지 생성: True


def main():
    if not PLACE_ID or not str(PLACE_ID).isdigit():
        raise ValueError("PLACE_ID에 테스트할 가게 ID를 입력하세요")
    if not isinstance(MAX_FORMATS, int) or isinstance(MAX_FORMATS, bool) or MAX_FORMATS < 1:
        raise ValueError("MAX_FORMATS는 1 이상의 정수여야 합니다")
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError(".env에 OPENAI_API_KEY를 입력하세요")
    uri = DB_PATH.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM places WHERE place_id = ?", (PLACE_ID,)).fetchone()
    if row is None:
        raise ValueError(f"DB에 가게가 없습니다: {PLACE_ID}")
    place = dict(row)
    print(f"가게 수집: {PLACE_ID} {place['place_name']}", flush=True)

    with tempfile.TemporaryDirectory(dir=ROOT) as temp_dir:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                availability = place_availability(browser, place["place_url"])
                if not availability[1]:
                    print("사진이 없어 테스트를 종료합니다.", flush=True)
                    return
                reviews = reviews_for(browser, place["place_url"], availability)
                downloaded = photos_for(browser, place["place_url"], Path(temp_dir) / "photos", MAX_PHOTOS, availability)
            finally:
                browser.close()
        if not downloaded:
            print("사진이 없어 테스트를 종료합니다.", flush=True)
            return

        run_folder = RESULTS_DIR / str(PLACE_ID) / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        shared = run_folder / "00_공통"
        photo_folder = shared / "03_photos"
        photo_folder.mkdir(parents=True)
        photos = [Path(shutil.move(str(photo), str(photo_folder / photo.name))) for photo in downloaded]
        write_json(shared / "01_place.json", place)
        write_json(shared / "02_reviews.json", reviews)
        write_json(shared / "04_analysis_photos.json", photo_manifest(photos))
        formats = ["자동", *FORMAT_PROMPTS][:MAX_FORMATS]
        statuses = []
        prior_concepts = []
        prepared = []
        original_format = os.environ.get("VIDEO_FORMAT")
        try:
            with OpenAI() as client:
                print(f"사진 {len(photos)}장 공통 분석", flush=True)
                analysis = analyze(recorded_client(client, shared, "analysis", "04"), photos)
                (shared / "04_analysis.txt").write_text(analysis, encoding="utf-8")
                for index, video_format in enumerate(formats, 1):
                    folder = run_folder / f"{index:02d}_{video_format}"
                    folder.mkdir()
                    print(f"[{index}/{len(formats)}] {video_format} 기획 시작", flush=True)
                    try:
                        for name in ("01_place.json", "02_reviews.json", "04_analysis_photos.json", "04_analysis.txt", "04_gpt_analysis_request.json", "04_gpt_analysis_response.json", "05_gpt_token_usage.json"):
                            shutil.copy2(shared / name, folder / name)
                        format_photos = [folder / "03_photos" / photo.name for photo in photos]
                        (folder / "03_photos").mkdir()
                        for source, target in zip(photos, format_photos):
                            shutil.copy2(source, target)
                        os.environ["VIDEO_FORMAT"] = video_format
                        result = plan(recorded_client(client, folder, "plan", "05"), place, reviews, analysis, format_photos, prior_concepts=prior_concepts)
                        write_json(folder / "05_plan.json", result)
                        write_json(folder / "05_marketing.json", {"가게": place, "후기": reviews, "사진_분석": analysis, "기획": result})
                        summary = result.get("중복_비교_요약")
                        if not isinstance(summary, str) or not summary.strip():
                            summary = str(result.get("스토리_요약", ""))
                        prior_concepts.append({"포맷": video_format, "핵심_상황과_전개": summary.strip()[:80]})
                        if GENERATE_VIDEO:
                            prepared.append({"folder": folder, "result": result, "photos": format_photos, "format": video_format})
                        statuses.append({"포맷": video_format, "상태": "완료", "영상_생성": GENERATE_VIDEO, "폴더": str(folder)})
                        print(f"완료: {folder}", flush=True)
                    except Exception as exc:
                        (folder / "오류.txt").write_text(traceback.format_exc(), encoding="utf-8")
                        statuses.append({"포맷": video_format, "상태": "실패", "오류": str(exc), "폴더": str(folder)})
                        print(f"{video_format} 실패: {exc}", flush=True)
                    write_json(run_folder / "결과.json", statuses)
                if prepared:
                    voices = generate_qwen_batch(prepared) if qwen_tts_enabled() else {}
                    for job in prepared:
                        folder, result = job["folder"], job["result"]
                        try:
                            voice = voices.get(str(folder.resolve()))
                            if qwen_tts_enabled() and (not voice or voice.get("error") or not voice.get("clips")):
                                raise ValueError(voice.get("error", "Qwen 음성 결과가 없습니다") if voice else "Qwen 음성 결과가 없습니다")
                            raw = folder / "06_video_raw.mp4"
                            reference = None if qwen_tts_enabled() else narration_reference(result["15초_스크립트"])
                            generate_video_without_subtitles(client, COMFY_URL, selected_photos(result, job["photos"]), result["minimax_prompt"], raw, reference)
                            add_subtitles(raw, folder / "video.mp4", result["15초_스크립트"], subtitle_name="06_subtitles.srt", place_name=place["place_name"], voice_clips=voice["clips"] if voice else None)
                            record_process(place, folder / "video.mp4")
                        except Exception as exc:
                            (folder / "오류.txt").write_text(traceback.format_exc(), encoding="utf-8")
                            for status in statuses:
                                if status["폴더"] == str(folder):
                                    status.update({"상태": "실패", "오류": str(exc)})
                            print(f"{job['format']} 실패: {exc}", flush=True)
                        write_json(run_folder / "결과.json", statuses)
        finally:
            if original_format is None:
                os.environ.pop("VIDEO_FORMAT", None)
            else:
                os.environ["VIDEO_FORMAT"] = original_format
        print(f"전체 결과: {run_folder}", flush=True)
        print(f"완료 {sum(item['상태'] == '완료' for item in statuses)} / {len(formats)}", flush=True)


if __name__ == "__main__":
    main()
