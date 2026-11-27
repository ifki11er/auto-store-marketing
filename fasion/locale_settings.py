"""패션 수집과 영상에 공통으로 적용할 국가별 설정."""
import os
import json
import ast

LOCALES = {
    "kr": {"url": "https://www.zara.com/kr/ko/", "locale": "ko-KR", "language": "한국어", "currency": "KRW"},
    "vn": {"url": "https://www.zara.com/vn/vi/", "locale": "vi-VN", "language": "베트남어", "currency": "VND"},
    "en": {"url": "https://www.zara.com/us/en/", "locale": "en-US", "language": "영어", "currency": "USD"},
}


def selected_locales():
    raw = os.environ.get("FASION_LANGUAGE", "kr").strip()
    if raw.startswith("["):
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            try:
                values = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                raise ValueError("FASION_LANGUAGE는 kr,vn,en 또는 ['kr', 'vn', 'en'] 형식이어야 합니다") from exc
    else:
        values = raw.split(",")
    if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError("FASION_LANGUAGE에 kr, vn, en 중 하나 이상을 입력하세요")
    countries = [value.strip().lower() for value in values]
    if len(set(countries)) != len(countries):
        raise ValueError("FASION_LANGUAGE에 중복된 언어가 있습니다")
    for country in countries:
        if country not in LOCALES:
            raise ValueError(f"지원하지 않는 FASION_LANGUAGE: {country} (kr, vn 또는 en)")
    return [(country, LOCALES[country]) for country in countries]
