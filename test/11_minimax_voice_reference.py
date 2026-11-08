"""참조 WAV의 목소리로 MiniMax가 나레이션을 생성하는지 테스트한다."""
import json
import mimetypes
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from marketing.main import WORKFLOW


# 사용할 가게 사진의 실제 경로로 변경하세요. MiniMax 오디오 참조에는 사진이 필요합니다.
REFERENCE_IMAGE = ROOT / "test" / "reference.jpg"
# .env의 MINIMAX_REFERENCE_VOICE를 사용합니다. 비어 있으면 아래 파일을 사용합니다.
REFERENCE_VOICE = ROOT / "res" / (os.environ.get("MINIMAX_REFERENCE_VOICE", "").strip() or "kr_male_30_typecast_changsu.wav")
NARRATION = "오늘은 이곳에서 특별한 한 끼를 만나보세요."
COMFY_URL = "http://127.0.0.1:8188"
OUTPUT_DIR = ROOT / "test" / "results" / "11_minimax_voice_reference"


def upload_file(path):
    with path.open("rb") as media:
        response = requests.post(COMFY_URL.rstrip("/") + "/upload/image",
                                 files={"image": (path.name, media, mimetypes.guess_type(path.name)[0] or "application/octet-stream")},
                                 data={"overwrite": "false", "type": "input"}, timeout=60)
    response.raise_for_status()
    result = response.json()
    return "/".join(filter(None, (result.get("subfolder"), result["name"])))


def build_prompt(text):
    return f'''subject_definitions:
<Picture 1> is the supplied restaurant photograph. <Subject 1> is the place and food visible in the photograph.
<Audio 1> is a voice-timbre and delivery reference for the off-screen Korean narrator (S1).

summary:
[reference generation + audio reference] Create a 15-second vertical 9:16 restaurant video using <Picture 1> as the visual reference and <Audio 1> as the narrator's voice reference.

retention_analysis:
<Subject 1>: preserve the recognizable appearance of the supplied photo.
<Audio 1>: reference - use the voice timbre and delivery, without copying the original words or audio.

detailed_description:
[Shot 1] Present <Subject 1> with natural, gentle camera movement. An off-screen narrator (S1) speaks in Korean using the voice characteristics of <Audio 1>, once: <d>{text}</d> Keep the rest of the video free of additional speech and do not generate on-screen captions.

overall_soundscape:
Keep the narrator clear, with subtle ambience appropriate to the scene.

non_diegetic_music:
Light instrumental background music, quiet beneath the narration, without vocals.'''


def main():
    for path, name in ((REFERENCE_IMAGE, "참조 사진"), (REFERENCE_VOICE, "참조 음성")):
        if not path.is_file():
            raise FileNotFoundError(f"{name} 파일이 없습니다. 테스트 파일 상단 설정을 확인하세요: {path}")
    if not NARRATION.strip() or "<d>" in NARRATION or "</d>" in NARRATION:
        raise ValueError("NARRATION에는 태그 없이 읽을 대사만 입력하세요")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(NARRATION)
    (OUTPUT_DIR / "11_prompt.txt").write_text(prompt, encoding="utf-8")
    print(f"참조 사진: {REFERENCE_IMAGE}\n참조 음성: {REFERENCE_VOICE}\n대사: {NARRATION}", flush=True)

    image_name = upload_file(REFERENCE_IMAGE)
    audio_name = upload_file(REFERENCE_VOICE)
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8-sig"))
    inputs = workflow["235"]["inputs"]
    for key in list(inputs):
        if key.startswith(("ref_images.", "ref_videos.", "ref_video_audios.", "ref_audios.")):
            del inputs[key]
    workflow["9000"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    workflow["9001"] = {"class_type": "LoadAudio", "inputs": {"audio": audio_name}}
    inputs["ref_images.ref_image_0"] = ["9000", 0]
    inputs["ref_audios.ref_audio_0"] = ["9001", 0]
    workflow["186"]["inputs"]["text"] = prompt
    workflow["132"]["inputs"]["value"] = 5
    needed = set()

    def visit(node):
        if node in needed:
            return
        needed.add(node)
        for value in workflow[node]["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] in workflow:
                visit(value[0])

    visit("144")
    payload = {key: workflow[key] for key in needed}
    (OUTPUT_DIR / "11_workflow.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    response = requests.post(COMFY_URL.rstrip("/") + "/prompt", json={"prompt": payload}, timeout=60)
    response.raise_for_status()
    prompt_id = response.json()["prompt_id"]
    print(f"ComfyUI 작업 ID: {prompt_id}", flush=True)
    deadline = time.monotonic() + 7200
    while time.monotonic() < deadline:
        time.sleep(5)
        response = requests.get(COMFY_URL.rstrip("/") + "/history/" + prompt_id, timeout=30)
        response.raise_for_status()
        history = response.json().get(prompt_id)
        if not history:
            continue
        status = history.get("status", {})
        if status.get("status_str") == "error":
            raise RuntimeError(f"ComfyUI 생성 실패: {status.get('messages')}")
        videos = history.get("outputs", {}).get("144", {}).get("gifs", [])
        if not videos:
            raise RuntimeError("ComfyUI 결과에 영상이 없습니다")
        video = videos[0]
        response = requests.get(COMFY_URL.rstrip("/") + "/view", params={"filename": video["filename"],
                                "subfolder": video.get("subfolder", ""), "type": video.get("type", "output")}, timeout=120)
        response.raise_for_status()
        output = OUTPUT_DIR / "11_video.mp4"
        output.write_bytes(response.content)
        print(f"완료: {output}")
        return
    raise TimeoutError(f"ComfyUI 작업 대기 시간 초과: {prompt_id}")


if __name__ == "__main__":
    main()
