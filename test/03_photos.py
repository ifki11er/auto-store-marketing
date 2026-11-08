"""카카오 장소 사진을 다운로드한다."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import sync_playwright
from marketing.main import photos_for
from test.common import folder

PLACE_ID = "1843998815"  # 테스트할 카카오 장소 ID
RESULTS_DIR = Path(__file__).resolve().parent / "results"
MAX_PHOTOS = 10

args = SimpleNamespace(place_id=PLACE_ID, results=RESULTS_DIR, max_photos=MAX_PHOTOS)
output = folder(args) / "photos"
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    try:
        files = photos_for(browser, f"https://place.map.kakao.com/{args.place_id}", output, args.max_photos)
    finally:
        browser.close()
print("\n".join(map(str, files)) if files else "사진 없음")
