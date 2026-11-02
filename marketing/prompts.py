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
    "인플루언서형": "influencer.md",
    "브이로그형": "vlog.md",
    "드라마형": "drama.md",
    "꽁트형": "skit.md",
    "재미형": "fun.md",
    "정보형": "informational.md",
    "고급 광고형": "premium_ad.md",
    "체험형": "experience.md",
    "인터뷰형": "interview.md",
    "상황 공감형": "relatable.md",
    "셀카형": "selfie.md",
}


def select_video_format():
    video_format = os.environ.get("VIDEO_FORMAT", "자동").strip() or "자동"
    if video_format.lower() == "random":
        return random.choice(["자동", *FORMAT_PROMPTS])
    if video_format != "자동" and video_format not in FORMAT_PROMPTS:
        raise ValueError(f"VIDEO_FORMAT은 random, 자동, {', '.join(FORMAT_PROMPTS)} 중 하나여야 합니다: {video_format}")
    return video_format


def build_planning_prompt(category_group_code, video_format=None):
    common = (PROMPT_DIR / "common.md").read_text(encoding="utf-8-sig")
    if video_format is None:
        video_format = select_video_format()
    parts = [common]
    filename = CATEGORY_PROMPTS.get(category_group_code)
    if filename is not None:
        parts.append((PROMPT_DIR / filename).read_text(encoding="utf-8-sig"))
    if video_format != "자동":
        parts.append((PROMPT_DIR / "formats" / FORMAT_PROMPTS[video_format]).read_text(encoding="utf-8-sig"))
    return "\n\n".join(parts)
