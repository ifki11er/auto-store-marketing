"""기획의 MiniMax 프롬프트로 ComfyUI 영상을 생성한다."""
import sys
from pathlib import Path
from types import SimpleNamespace
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from marketing.main import add_subtitles, generate_video_without_subtitles, narration_reference, photo_manifest, selected_photos
from marketing.process_log import record_process
from test.common import folder, photos, read_json

PLACE_ID = "1843998815"  # 테스트할 카카오 장소 ID
RESULTS_DIR = Path(__file__).resolve().parent / "results"
COMFY_URL = "http://127.0.0.1:8188"

args = SimpleNamespace(place_id=PLACE_ID, results=RESULTS_DIR, comfy_url=COMFY_URL)
output = folder(args)
plan = read_json(output / "plan.json")
images = photos(output / "photos")
manifest_path = output / "analysis_photos.json"
if not manifest_path.exists() or read_json(manifest_path) != photo_manifest(images) or plan.get("분석_사진_목록") != photo_manifest(images):
    raise SystemExit("분석 후 사진이 변경되었거나 사진 목록이 없습니다. 4번과 5번을 다시 실행하세요.")
prompt = plan["minimax_prompt"]
video = output / "video.mp4"
shots = plan["15초_스크립트"]
if any(not isinstance(shot.get("자막"), str) for shot in shots):
    raise SystemExit("자막 형식이 잘못되었습니다. 5번 테스트를 다시 실행하세요.")
raw_video = output / "video_raw.mp4"
reference_audio = narration_reference(shots)
prompt_id = generate_video_without_subtitles(OpenAI(), args.comfy_url, selected_photos(plan, images), prompt, raw_video, reference_audio)
place = read_json(output / "place.json")
add_subtitles(raw_video, video, shots, place_name=place["place_name"])
record_process(place, video)
print(f"완료: {video} (작업 ID: {prompt_id})")
