"""공통 기획 지시와 업종별 지시를 조합한다."""
from pathlib import Path
import os
import random


PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
CATEGORY_PROMPTS = {
    "AC5": "AC5.md",
    "AD5": "AD5.md",
    "AT4": "AT4.md",
    "CE7": "CE7.md",
    "CT1": "CT1.md",
    "FD6": "FD6.md",
}
FORMAT_PROMPTS = {
    "브이로그형": "vlog.md",
    "정보형": "informational.md",
    "체험형": "experience.md",
}


def select_video_format():
    video_format = os.environ.get("VIDEO_FORMAT", "자동").strip() or "자동"
    if video_format.lower() == "random":
        return random.choice(["자동", *FORMAT_PROMPTS])
    if video_format != "자동" and video_format not in FORMAT_PROMPTS:
        raise ValueError(f"VIDEO_FORMAT은 random, 자동, {', '.join(FORMAT_PROMPTS)} 중 하나여야 합니다: {video_format}")
    return video_format


def qwen_tts_enabled():
    value = os.environ.get("use_tts", "minimax").strip().lower()
    if value not in ("minimax", "qwen3"):
        raise ValueError("use_tts는 minimax 또는 qwen3로 설정하세요")
    return value == "qwen3"


def build_planning_prompt(category_group_code, video_format=None):
    common = (PROMPT_DIR / ("common_qwen.md" if qwen_tts_enabled() else "common.md")).read_text(encoding="utf-8-sig")
    if video_format is None:
        video_format = select_video_format()
    parts = [common]
    filename = CATEGORY_PROMPTS.get(category_group_code)
    if filename is not None:
        parts.append((PROMPT_DIR / filename).read_text(encoding="utf-8-sig"))
    if video_format != "자동":
        parts.append((PROMPT_DIR / "formats" / FORMAT_PROMPTS[video_format]).read_text(encoding="utf-8-sig"))
    return "\n\n".join(parts)
