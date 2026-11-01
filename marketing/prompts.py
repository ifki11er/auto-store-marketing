"""공통 기획 지시와 업종별 지시를 조합한다."""
from pathlib import Path


PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
CATEGORY_PROMPTS = {
    "AC5": "AC5.md",
    "AD5": "AD5.md",
    "AT4": "AT4.md",
    "CE7": "CE7.md",
    "CT1": "CT1.md",
    "FD6": "FD6.md",
}


def build_planning_prompt(category_group_code):
    common = (PROMPT_DIR / "common.md").read_text(encoding="utf-8-sig")
    filename = CATEGORY_PROMPTS.get(category_group_code)
    if filename is None:
        return common
    category = (PROMPT_DIR / filename).read_text(encoding="utf-8-sig")
    return common + "\n\n" + category
