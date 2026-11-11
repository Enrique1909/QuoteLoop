"""Deterministic money and date helpers. All amounts are integer paise."""
from datetime import date, timedelta


def inr_to_paise(value) -> int:
    return int(round(float(value) * 100))


def format_inr(paise: int) -> str:
    """Format paise as Indian rupees with lakh/crore grouping, e.g. 11500000 -> ₹1,15,000."""
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


def approved_total(agreement, change_requests) -> int:
    return agreement.base_price_paise + sum(
        cr.extra_price_paise for cr in change_requests if cr.status == "approved"
    )


def approved_extra_days(change_requests) -> int:
    return sum(cr.extra_days for cr in change_requests if cr.status == "approved")


def final_deadline(agreement, change_requests):
    """Last milestone due date plus approved extra days, or None if no dated milestone."""
    dates = []
    for m in agreement.milestones or []:
        try:
            dates.append(date.fromisoformat(str(m.get("due_date", ""))))
        except ValueError:
            continue
    if not dates:
        return None
    return max(dates) + timedelta(days=approved_extra_days(change_requests))
