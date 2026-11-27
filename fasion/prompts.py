"""패션 영상의 GPT 프롬프트를 불러온다."""
import json
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
LOCATIONS = {
    "indoor_runway": "the fashion show runway and surroundings referenced from <Picture 2>",
    "garden": "an outdoor garden suited to the garment",
    "city_street": "a stylish city street suited to the garment",
    "gallery": "an art gallery suited to the garment",
    "rooftop": "a rooftop suited to the garment",
    "pool": "a swimming pool suited to swimwear",
}
CAMERAS = {
    "tracking_backward": "a smooth frontal backward tracking shot",
    "side_tracking": "a smooth side tracking shot",
    "diagonal_tracking": "a smooth diagonal tracking shot",
    "static": "a fixed camera",
    "slow_push_in": "a slow camera push-in",
}


def analysis_prompt():
    return (PROMPT_DIR / "analysis.md").read_text(encoding="utf-8-sig").strip()


def minimax_prompt(scene, seconds):
    indoor = scene["location"] == "indoor_runway"
    replacements = {
        "{{LOCATION_DEFINITION}}": LOCATIONS[scene["location"]],
        "{{GARMENT_PICTURE}}": "<Picture 3>" if indoor else "<Picture 2>",
        "{{LOCATION_RETENTION}}": "<Subject 2>: fully_preserved - retain the runway setting, layout and visual character from <Picture 2>." if indoor else "<Subject 2>: newly generated - create the selected setting without a location reference image.",
        "{{CAMERA}}": CAMERAS[scene["camera"]],
        "{{SCENE_DIRECTION}}": scene["scene_prompt"],
        "{{SECONDS}}": str(seconds),
    }
    prompt = (PROMPT_DIR / "minimax_fixed.md").read_text(encoding="utf-8-sig").strip()
    for token, value in replacements.items():
        prompt = prompt.replace(token, value)
    return prompt


def planning_prompt(brand, category, products, seconds, language, country):
    prompt = (PROMPT_DIR / "plan.md").read_text(encoding="utf-8-sig").strip()
    title_line = 10
    name_line = 20 if country == "kr" else 40
    prompt += f"\n\n모든 출력 문구(video_title, youtube_title, description, tags, display_names)는 {language}로 작성하세요. video_title과 각 display_names 항목은 최대 두 줄입니다. 두 줄이 필요하면 JSON 문자열 안에 줄바꿈 문자 \\n을 넣어 의미가 자연스러운 위치에서 끊으세요. 한 줄이면 줄바꿈을 넣지 마세요. 제목은 한 줄당 공백 포함 {title_line}자 이내, 상품명은 한 줄당 공백 포함 {name_line}자 이내입니다. 브랜드명은 줄을 가르지 말고 한 줄에 쓰세요. 단어 중간을 가르는 것은 꼭 필요한 경우에만 허용합니다."
    data = {"brand": brand, "category": category, "seconds_per_outfit": seconds,
            "products": [{"name": p["name"], "price": p["price"], "outfit_description": p["outfit_description"]} for p in products]}
    return prompt + "\n" + json.dumps(data, ensure_ascii=False)
