"""Deterministic money helpers. All amounts are integer paise; no floats in arithmetic."""
from decimal import ROUND_HALF_UP, Decimal


def inr_to_paise(value) -> int:
    if value in (None, ""):
        return 0
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


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
