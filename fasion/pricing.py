"""언어별 상품 가격 표기."""
import re
from decimal import Decimal, InvalidOperation

def formatted_price(value, country):
    text = str(value).strip()
    if country == "en":
        if not re.fullmatch(r"(?:\$\s*)?\d[\d,]*(?:\.\d{1,2})?", text):
            raise ValueError(f"상품 가격 형식이 올바르지 않습니다: {value}")
        try:
            amount = Decimal(text.replace("$", "").replace(",", "").strip())
        except InvalidOperation as exc:
            raise ValueError(f"상품 가격 형식이 올바르지 않습니다: {value}") from exc
        formatted = f"{amount:,.2f}"
        return "$" + (formatted[:-3] if formatted.endswith(".00") else formatted)
    if not re.fullmatch(r"(?:₩\s*)?\d[\d,]*(?:\s*원)?", text):
        raise ValueError(f"상품 가격 형식이 올바르지 않습니다: {value}")
    digits = re.sub(r"[^0-9]", "", text)
    amount = f"{int(digits):,}"
    return f"{amount}원" if country == "kr" else f"{amount} ₫"
