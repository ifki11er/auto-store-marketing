"""GPT 응답의 토큰 사용량을 단계별로 저장한다."""
import json
from datetime import datetime


def record_token_usage(folder, stage, response):
    path = folder / "05_gpt_token_usage.json"
    report = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {"호출": []}
    data = response.model_dump(mode="json")
    usage = data.get("usage") or {}
    input_details = usage.get("input_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or {}
    calls = report["호출"]
    calls.append({
        "번호": len(calls) + 1,
        "시각": datetime.now().astimezone().isoformat(),
        "단계": {"analysis": "사진 분석", "plan": "영상 기획"}.get(stage, stage),
        "모델": data.get("model"),
        "응답_ID": data.get("id"),
        "입력_토큰": usage.get("input_tokens"),
        "출력_토큰": usage.get("output_tokens"),
        "합계_토큰": usage.get("total_tokens"),
        "상세": {
            "캐시된_입력_토큰": input_details.get("cached_tokens"),
            "캐시_기록_입력_토큰": input_details.get("cache_write_tokens"),
            "추론_토큰": output_details.get("reasoning_tokens"),
        },
    })

    def totals(items):
        result = {"호출_횟수": len(items)}
        for key in ("입력_토큰", "출력_토큰", "합계_토큰"):
            values = [item[key] for item in items if isinstance(item[key], int)]
            result[key] = sum(values) if values else None
        result["사용량_미제공_호출수"] = sum(
            any(item[key] is None for key in ("입력_토큰", "출력_토큰", "합계_토큰"))
            for item in items
        )
        return result

    report["단계별_합계"] = {
        name: totals([item for item in calls if item["단계"] == name])
        for name in dict.fromkeys(item["단계"] for item in calls)
    }
    report["누적_합계"] = totals(calls)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
