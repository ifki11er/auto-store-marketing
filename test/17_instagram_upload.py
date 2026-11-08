"""Meta Business Suite에 직접 로그인하는 단계."""
from pathlib import Path
import re
import json
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright


ASSETS = {
    "충청": "1467960399728333", "부산": "1352922201245398",
    "경기": "1379041368634541", "경상": "1480321411824839",
    "전라": "1454905344366719", "대전": "1398087956723223",
    "제주": "1436259852896237", "강원": "1426548847208889",
    "인천": "1397257956810564", "서울": "1374288102438839",
}
BUSINESS_ID = "4590033027888091"
PROFILE_DIR = Path(__file__).resolve().parent / ".instagram_browser_profile"
RESULT_DIR = Path(__file__).resolve().parent / "results" / "instagram"
LOG_FILE = RESULT_DIR / "업로드_결과.json"


def save_log(log):
    temporary = LOG_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(LOG_FILE)


def reels_url(region):
    return (f"https://business.facebook.com/latest/reels_composer?asset_id={ASSETS[region]}"
            f"&business_id={BUSINESS_ID}&ir_qe_exposed=1"
            "&ref=biz_web_content_manager_calendar_right_panel_goals_card_create_reel&context_ref=CONTENT_CALENDAR")



def hashtags_for(video):
    text = (video.parent / "전체_설명.txt").read_text(encoding="utf-8-sig")
    sections = re.split(r"(?m)^\[영상\] ", text)
    matches = [section.split("\n", 1)[1] for section in sections[1:]
               if "\n" in section and section.split("\n", 1)[0].rstrip("\r") == video.name]
    if len(matches) != 1:
        raise ValueError(f"영상에 해당하는 설명을 하나로 확인할 수 없습니다: {video.name}")
    lines = [line.strip() for line in matches[0].splitlines() if line.strip()]
    hashtags = re.findall(r"#[\w]+", lines[-1]) if lines else []
    if not hashtags or " ".join(hashtags) != lines[-1]:
        raise ValueError(f"영상 설명의 해시태그를 확인할 수 없습니다: {video.name}")
    return hashtags


def upload_video(page, region, video, entry, log):
    filename_match = re.match(r"^(\d{4}-\d{2}-\d{2})_(\d{4})_", video.name)
    if not filename_match:
        raise ValueError(f"파일명에서 예약 날짜와 시간을 확인할 수 없습니다: {video.name}")
    scheduled = datetime.strptime(" ".join(filename_match.groups()), "%Y-%m-%d %H%M")
    hashtags = hashtags_for(video)
    hashtag_text = " ".join(hashtags)
    print(f"선택한 영상: {video}", flush=True)
    print(f"해시태그: {hashtag_text}", flush=True)
    page.goto(reels_url(region), wait_until="domcontentloaded", timeout=60000)
    print("로그인이 필요하면 브라우저에서 로그인과 승인을 완료하세요.", flush=True)
    page.wait_for_url(re.compile(r"^https://business\.facebook\.com/latest/reels_composer(?:[/?#]|$)"), wait_until="domcontentloaded", timeout=0)
    params = parse_qs(urlsplit(page.url).query)
    if params.get("asset_id") != [ASSETS[region]] or params.get("business_id") != [BUSINESS_ID]:
        raise ValueError(f"{region} 계정 페이지인지 확인할 수 없습니다: {page.url}")
    print(f"{region} 릴스 만들기 페이지에 접속했습니다.", flush=True)
    with page.expect_file_chooser(timeout=30000) as chooser:
        page.get_by_text("동영상 추가", exact=True).click(timeout=30000)
    chooser.value.set_files(str(video))
    print("선택한 영상을 전달했습니다.", flush=True)
    page.get_by_text("Facebook 및 Instagram에 맞게 게시물 맞춤 설정", exact=True).click(timeout=30000)
    for platform in ("Instagram", "Facebook"):
        tab = page.get_by_role("tab", name=platform, exact=True)
        tab.click(timeout=30000)
        expect(tab).to_have_attribute("aria-selected", "true", timeout=30000)
        editor = page.locator('[contenteditable="true"]:visible').filter(
            has=page.locator('[data-contents="true"]')
        )
        editor.fill(hashtag_text, timeout=30000)
        print(f"{platform} 텍스트 입력 완료", flush=True)
    tag_input = page.get_by_text("태그", exact=True).locator(
        "xpath=ancestor::div[.//input[@role='combobox']][1]"
    ).locator('input[role="combobox"]')
    for hashtag in hashtags:
        tag_input.click(timeout=30000)
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(hashtag.lstrip("#"))
        page.wait_for_timeout(1000)
        page.keyboard.press("Enter")
        print(f"태그 입력 시도: {hashtag}", flush=True)
    print("Instagram, Facebook 텍스트와 태그 입력을 시도했습니다.", flush=True)
    footer = page.get_by_role("button", name="취소", exact=True).locator(
        "xpath=ancestor::div[.//*[@role='button' and (@aria-label='다음' or .//*[normalize-space()='다음'])]][1]"
    )
    next_button = footer.get_by_role("button", name="다음", exact=True)
    print("영상 업로드와 다음 버튼 활성화를 기다립니다 (최대 5분).", flush=True)
    expect(next_button).to_be_enabled(timeout=300000)
    next_button.click(timeout=30000)
    expect(page.get_by_text("Facebook 및 Instagram에 맞게 게시물 맞춤 설정", exact=True)).to_be_hidden(timeout=30000)
    print("영상 수정 단계로 이동했습니다.", flush=True)
    edit_footer = page.get_by_role("button", name="돌아가기", exact=True).locator(
        "xpath=ancestor::div[.//*[@role='button' and (@aria-label='다음' or .//*[normalize-space()='다음'])]][1]"
    )
    edit_next = edit_footer.get_by_role("button", name="다음", exact=True)
    print("영상 수정 단계의 다음 버튼 활성화를 기다립니다 (최대 5분).", flush=True)
    expect(edit_next).to_be_enabled(timeout=300000)
    edit_next.click(timeout=30000)
    print("다음 단계로 이동했습니다.", flush=True)
    schedule_group = page.get_by_role("group").filter(
        has=page.get_by_role("button", name="지금 공유하기", exact=True)
    )
    schedule_button = schedule_group.get_by_role("button", name="예약", exact=True)
    schedule_button.click(timeout=30000)
    expect(schedule_button).to_have_attribute("aria-pressed", "true", timeout=30000)
    for platform in ("Facebook", "Instagram"):
        section = page.get_by_role("heading", name=platform, exact=True).locator(
            "xpath=ancestor::div[.//input[@placeholder='yyyy-mm-dd'] and .//*[@role='spinbutton']][1]"
        )
        if not (section.get_by_text("Asia/Jayapura", exact=True).count() or
                section.get_by_text("Asia/Seoul", exact=True).count()):
            raise ValueError(f"{platform} 예약 시간대가 한국 시간과 같은지 확인할 수 없습니다")
        date_input = section.get_by_placeholder("yyyy-mm-dd")
        date_input.click()
        date_input.press("Control+A")
        date_input.press_sequentially(scheduled.strftime("%Y-%m-%d"), delay=100)
        date_input.press("Tab")
        expected_date = re.compile(rf"^(?:{scheduled:%Y-%m-%d}|{scheduled.year}년\s*{scheduled.month}월\s*{scheduled.day}일)$")
        expect(date_input).to_have_value(expected_date, timeout=30000)
        for label, target in (("오전/오후", int(scheduled.hour >= 12)),
                              ("시간", scheduled.hour % 12 or 12),
                              ("분", scheduled.minute)):
            spin = section.get_by_role("spinbutton", name=label, exact=True)
            current = int(spin.get_attribute("aria-valuenow"))
            key = "ArrowUp" if target > current else "ArrowDown"
            for _ in range(abs(target - current)):
                spin.press(key)
            expect(spin).to_have_attribute("aria-valuenow", str(target), timeout=30000)
        print(f"{platform} 예약 시간 설정: {scheduled:%Y-%m-%d %H:%M} (한국 시간)", flush=True)
    final_footer = page.get_by_role("button", name="돌아가기", exact=True).locator(
        "xpath=ancestor::div[.//*[@role='button' and (@aria-label='예약' or .//*[normalize-space()='예약'])]][1]"
    )
    final_schedule = final_footer.get_by_role("button", name="예약", exact=True)
    expect(final_schedule).to_be_enabled(timeout=300000)
    entry.update({"상태": "예약_결과_확인_필요", "시간": datetime.now().isoformat(timespec="seconds")})
    save_log(log)
    final_schedule.click(timeout=30000)
    print("예약 결과를 기다립니다.", flush=True)
    expect(page.get_by_text("릴스 예약됨", exact=True)).to_be_visible(timeout=300000)
    result_date = rf"{scheduled.year}\.\s*{scheduled.month}\.\s*{scheduled.day}\."
    result_text = re.compile(rf"Facebook에서\s*{result_date}.*Instagram에서\s*{result_date}", re.DOTALL)
    expect(page.get_by_text(result_text)).to_be_visible(timeout=30000)
    entry.update({"상태": "예약_확인", "시간": datetime.now().isoformat(timespec="seconds")})
    save_log(log)
    page.get_by_role("button", name="완료", exact=True).click(timeout=300000)
    video.unlink()
    print(f"예약 확인 후 영상 삭제: {video}", flush=True)


def main():
    if not RESULT_DIR.is_dir():
        raise FileNotFoundError(f"15번 테스트 결과 폴더가 없습니다: {RESULT_DIR}")
    log = json.loads(LOG_FILE.read_text(encoding="utf-8")) if LOG_FILE.exists() else {}
    results = {"성공": [], "실패": []}
    folders = [folder for run in sorted(RESULT_DIR.iterdir()) if run.is_dir()
               for folder in sorted(run.iterdir()) if folder.is_dir()]
    if not folders:
        print("업로드할 지역 폴더가 없습니다.", flush=True)
        return
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(PROFILE_DIR, headless=False)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                for folder in folders:
                    region = folder.name
                    videos = sorted(folder.glob("*.mp4"))
                    if region not in ASSETS:
                        results["실패"].append(f"{folder}: 지역 계정 정보 없음")
                        continue
                    for video in videos:
                        key = str(video.relative_to(RESULT_DIR))
                        entry = log.setdefault(key, {"지역": region, "영상": str(video)})
                        if entry.get("상태") == "예약_결과_확인_필요":
                            results["실패"].append(f"{video}: 예약 결과 확인 필요 (재예약하지 않음)")
                            continue
                        if entry.get("상태") == "완료":
                            results["실패"].append(f"{video}: 이전 완료 이력이 있어 재예약하지 않음")
                            continue
                        try:
                            if entry.get("상태") == "예약_확인":
                                video.unlink()
                                print(f"예약 확인된 영상 삭제: {video}", flush=True)
                            else:
                                upload_video(page, region, video, entry, log)
                            entry.update({"상태": "완료", "시간": datetime.now().isoformat(timespec="seconds")})
                            entry.pop("오류", None)
                            save_log(log)
                            results["성공"].append(str(video))
                        except Exception as exc:
                            if entry.get("상태") not in ("예약_결과_확인_필요", "예약_확인"):
                                entry["상태"] = "실패"
                            entry.update({"오류": str(exc), "시간": datetime.now().isoformat(timespec="seconds")})
                            save_log(log)
                            results["실패"].append(f"{video}: {exc}")
                            print(f"처리 실패: {video} ({exc})", flush=True)
                    if videos and not list(folder.glob("*.mp4")):
                        remaining = list(folder.iterdir())
                        if all(path.is_file() and path.name == "전체_설명.txt" for path in remaining):
                            try:
                                for path in remaining:
                                    path.unlink()
                                folder.rmdir()
                                print(f"지역 폴더 삭제: {folder}", flush=True)
                            except OSError as exc:
                                results["실패"].append(f"{folder}: 폴더 정리 실패 ({exc})")
            finally:
                context.close()
    finally:
        for status, entries in results.items():
            print(f"{status}: {len(entries)}건", flush=True)
            for item in entries:
                print(f"- {item}", flush=True)
        print(f"결과 이력: {LOG_FILE}", flush=True)


if __name__ == "__main__":
    main()
