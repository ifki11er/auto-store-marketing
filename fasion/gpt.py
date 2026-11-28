"""패션 사진 분석과 영상 기획을 위한 GPT 요청."""
import base64
import json
import mimetypes
import re

from fasion.pricing import formatted_price
from fasion.prompts import planning_prompt
from marketing.token_usage import record_token_usage

def image_input(path):
    mime = mimetypes.guess_type(path.name)[0]
    if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        raise ValueError(f"지원하지 않는 이미지 형식: {path}")
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "input_image", "image_url": f"data:{mime};base64,{data}", "detail": "auto"}


def ask(client, content, job, stage):
    request = {"model": "gpt-6-luna", "input": [{"role": "user", "content": content}],
               "text": {"format": {"type": "json_object"}}}
    (job / f"{stage}_request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    result = client.responses.create(**request)
    (job / f"{stage}_response.json").write_text(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    record_token_usage(job, "analysis" if stage.startswith("01_gpt_analysis_") else "plan", result)
    if not result.output_text:
        raise RuntimeError("GPT 응답에 텍스트가 없습니다")
    return json.loads(result.output_text)


def plan(client, brand, category, products, job, seconds, country, settings):
    prompt = planning_prompt(brand, category, products, seconds, settings["language"], country)
    title_limit = 10
    name_limit = 20 if country == "kr" else 24

    def valid_lines(value, limit):
        if not isinstance(value, str) or "\r" in value:
            return False
        lines = value.split("\n")
        return 1 <= len(lines) <= 2 and all(line.strip() and len(line.strip()) <= limit for line in lines)

    for attempt in range(1, 4):
        stage = "02_gpt_plan" if attempt == 1 else f"02_gpt_plan_{attempt}"
        result = ask(client, [{"type": "input_text", "text": prompt}], job, stage)
        names = result.get("display_names")
        valid_names = (isinstance(names, list) and len(names) == len(products) and
                       all(valid_lines(name, name_limit) and
                           (len(product["name"]) > name_limit * 2 or
                            "".join(name.split()) == "".join(product["name"].split()))
                           for product, name in zip(products, names)))
        if (valid_lines(result.get("video_title"), title_limit)
                and brand.casefold() in result["video_title"].casefold() and valid_names):
            result["video_title"] = re.sub(re.escape(brand), lambda match: match.group().upper(), result["video_title"].strip(), flags=re.IGNORECASE)
            if not valid_lines(result["video_title"], title_limit):
                continue
            result["display_names"] = ["\n".join(line.strip() for line in name.split("\n")) for name in names]
            break
    else:
        raise ValueError(f"GPT가 브랜드명을 포함한 줄당 {title_limit}자 이내 제목과 줄당 {name_limit}자 이내 상품명을 최대 두 줄로 생성하지 못했습니다")
    if not all(isinstance(result.get(k), str) and result[k].strip() for k in ("video_title", "youtube_title", "description")) or not isinstance(result.get("tags"), list):
        raise ValueError("GPT 영상 기획 응답 형식이 올바르지 않습니다")
    result["description"] += "\n\n" + "\n".join(f"{p['name']} | {formatted_price(p['price'], country)}" for p in products)
    return result
