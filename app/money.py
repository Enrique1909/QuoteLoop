"""Deterministic money helpers. All amounts are integer paise; no floats in arithmetic."""
from decimal import ROUND_HALF_UP, Decimal


MAX_PAISE = 100_000_000_000  # ₹100 crore: anything above is treated as a typo


def inr_to_paise(value) -> int:
    """Rupees -> integer paise. Accepts 120000, "1,20,000", "₹1,20,000.50"; invalid or negative -> 0."""
    if value in (None, ""):
        return 0
    text = str(value).replace("₹", "").replace(",", "").replace("Rs", "").replace("INR", "").strip()
    try:
        paise = int((Decimal(text) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except Exception:
        return 0
    return paise if 0 <= paise <= MAX_PAISE else 0


def format_inr(paise) -> str:
    """Indian grouping: 18500000 -> ₹1,85,000."""
    if paise is None:
        return "—"
    negative = paise < 0
    rupees, rem = divmod(abs(int(paise)), 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    out = f"₹{s}" + (f".{rem:02d}" if rem else "")
    return f"-{out}" if negative else out


def pct(part, whole):
    if not whole:
        return None
    return round((Decimal(part) / Decimal(whole)) * 100, 1)
