"""수집한 자료로 GPT-6 Luna 마케팅 기획을 생성한다."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from openai import OpenAI
from marketing.main import photo_manifest, plan
from marketing.token_usage import record_token_usage
from test.common import folder, photos, read_json, save_json

PLACE_ID = "1843998815"  # 테스트할 카카오 장소 ID
RESULTS_DIR = Path(__file__).resolve().parent / "results"

args = SimpleNamespace(place_id=PLACE_ID, results=RESULTS_DIR)
output = folder(args)
place = read_json(output / "place.json")
reviews = read_json(output / "reviews.json")
analysis = (output / "analysis.txt").read_text(encoding="utf-8-sig")
images = photos(output / "photos")
manifest_path = output / "analysis_photos.json"
if not manifest_path.exists() or read_json(manifest_path) != photo_manifest(images):
    raise SystemExit("분석 후 사진이 변경되었거나 사진 목록이 없습니다. 4번 테스트를 다시 실행하세요.")

with OpenAI() as client:
    attempts = [0]
    def create_with_display(**kwargs):
        attempts[0] += 1
        prompt = f"--- 지침 ---\n{kwargs.get('instructions', '')}\n\n--- 입력 ---\n{kwargs.get('input', '')}"
        (output / "gpt_prompt.txt").write_text(prompt, encoding="utf-8")
        (output / f"gpt_prompt_{attempts[0]}.txt").write_text(prompt, encoding="utf-8")
        print("\n--- GPT에 보낸 지침 ---", flush=True)
        print(kwargs.get("instructions", ""), flush=True)
        print("\n--- GPT에 보낸 입력 ---", flush=True)
        print(kwargs.get("input", ""), flush=True)
        response = client.responses.create(**kwargs)
        record_token_usage(output, "plan", response)
        (output / "gpt_response.txt").write_text(response.output_text, encoding="utf-8")
        (output / f"gpt_response_{attempts[0]}.txt").write_text(response.output_text, encoding="utf-8")
        print("\n--- GPT가 반환한 결과 ---", flush=True)
        print(response.output_text, flush=True)
        return response

    display_client = SimpleNamespace(responses=SimpleNamespace(create=create_with_display))
    result = plan(display_client, place, reviews, analysis, images)
save_json(output / "plan.json", result)
