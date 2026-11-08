"""카카오 장소 후기를 수집한다."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import sync_playwright
from marketing.main import reviews_for
from test.common import folder, save_json

PLACE_ID = "1843998815"  # 테스트할 카카오 장소 ID
RESULTS_DIR = Path(__file__).resolve().parent / "results"

args = SimpleNamespace(place_id=PLACE_ID, results=RESULTS_DIR)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    try:
        result = reviews_for(browser, f"https://place.map.kakao.com/{args.place_id}")
    finally:
        browser.close()
save_json(folder(args) / "reviews.json", result)
print(f"후기 {len(result)}건" if result else "텍스트 후기 없음")
