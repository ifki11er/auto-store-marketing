"""패션 영상의 GPT 프롬프트를 불러온다."""
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
FACE_DESCRIPTIONS = {
    "suji.png": "a woman with an oval face, almond-shaped brown eyes, straight black bangs, and long black hair in a high ponytail with a small silver hair accessory",
    "vn.png": "a woman with an oval face, dark brown eyes, and long dark brown side-parted hair styled in loose waves",
    "en.png": "a woman with blue eyes, defined eyebrows, and long center-parted dark blonde hair styled in voluminous waves",
}


def analysis_prompt():
    return (PROMPT_DIR / "analysis.md").read_text(encoding="utf-8-sig").strip()


def minimax_prompt(scene, seconds, face_image):
    indoor = scene["use_runway_reference"]
    replacements = {
        "{{LOCATION}}": "the runway in <Picture 2>, " + scene["background"] if indoor else scene["background"],
        "{{GARMENT_PICTURE}}": "<Picture 3>" if indoor else "<Picture 2>",
        "{{LOCATION_RETENTION}}": "<Subject 3>: fully_preserved - preserve the runway shown in <Picture 2>." if indoor else "",
        "{{ACTION}}": scene["action"],
        "{{SECONDS}}": str(seconds),
        "{{FACE_DESCRIPTION}}": FACE_DESCRIPTIONS.get(Path(face_image).name.lower(), "the person shown in <Picture 1>")
        if Path(face_image).resolve().parent == (PROMPT_DIR.parent / "resources").resolve()
        else "the person shown in <Picture 1>",
    }
    prompt = (PROMPT_DIR / "minimax_fixed.md").read_text(encoding="utf-8-sig").strip()
    for token, value in replacements.items():
        prompt = prompt.replace(token, value)
    return prompt


def planning_prompt(brand, category, products, seconds, language, country):
    prompt = (PROMPT_DIR / "plan.md").read_text(encoding="utf-8-sig").strip()
    title_line = 10
    name_line = 20 if country == "kr" else 24
    prompt += f"\n\n모든 출력 문구(video_title, youtube_title, description, tags, display_names)는 {language}로 작성하세요. video_title과 각 display_names 항목은 최대 두 줄입니다. 두 줄이 필요하면 JSON 문자열 안에 줄바꿈 문자 \\n을 넣어 의미가 자연스러운 위치에서 끊으세요. 한 줄이면 줄바꿈을 넣지 마세요. 제목은 한 줄당 공백 포함 {title_line}자 이내, 상품명은 한 줄당 공백 포함 {name_line}자 이내입니다. 브랜드명은 줄을 가르지 말고 한 줄에 쓰세요. 단어 중간을 가르는 것은 꼭 필요한 경우에만 허용합니다."
    data = {"brand": brand, "category": category, "current_year": datetime.now(timezone(timedelta(hours=9))).year,
            "item_count": len(products), "seconds_per_outfit": seconds,
            "products": [{"name": p["name"], "price": p["price"]} for p in products]}
    return prompt + "\n" + json.dumps(data, ensure_ascii=False)
