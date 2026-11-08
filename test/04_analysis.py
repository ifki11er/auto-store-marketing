"""수집한 사진을 GPT-6 Luna로 분석한다."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from openai import OpenAI
from marketing.main import analyze, photo_manifest
from marketing.token_usage import record_token_usage
from test.common import folder, photos, save_json

PLACE_ID = "1843998815"  # 테스트할 카카오 장소 ID
RESULTS_DIR = Path(__file__).resolve().parent / "results"

args = SimpleNamespace(place_id=PLACE_ID, results=RESULTS_DIR)
output = folder(args)
images = photos(output / "photos")
manifest = photo_manifest(images)
client = OpenAI()
def create_with_usage(**kwargs):
    response = client.responses.create(**kwargs)
    record_token_usage(output, "analysis", response)
    return response

result = analyze(SimpleNamespace(responses=SimpleNamespace(create=create_with_usage)), images)
if photo_manifest(images) != manifest:
    raise RuntimeError("분석 도중 사진이 변경되었습니다. 4번을 다시 실행하세요")
save_json(output / "analysis_photos.json", manifest)
path = output / "analysis.txt"
path.write_text(result, encoding="utf-8")
print(result)
print(f"저장: {path}")
