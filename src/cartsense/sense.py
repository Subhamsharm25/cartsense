"""The "human sense" engine: predicts what a shopper needs right now, with a reason for every suggestion.

Three interpretable signals, no black box:

1. Replenishment sense — people buy staples on a personal rhythm. From each user's purchase
   timestamps we take the median interval per product; `days since last / median interval`
   is the due-ratio (1.0 = "you usually would have bought it again by now").
2. Context sense — categories have time-of-day habits (breakfast in the morning, snacks in the
   evening). We compute the lift of each category inside the current time bucket versus the
   user's overall mix, from their own history only.
3. Basket sense — co-purchase lift mined from every order in the store:
   lift(a→b) = P(b | a) / P(b). Items that usually ride along with what is in the cart.

The blended "For you, right now" ranking combines (1) and (2); the cart drawer uses (3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from statistics import median

MIN_PURCHASES = 3  # below this, an interval estimate is noise
MIN_BASKET_SUPPORT = 3  # a pair seen fewer times than this is ignored
DUE_THRESHOLD = 0.8  # start suggesting slightly before the usual day

BUCKETS = (("morning", 5, 11), ("afternoon", 11, 16), ("evening", 16, 21), ("night", 21, 29))


def bucket_of(hour: int) -> str:
    h = hour if hour >= 5 else hour + 24  # 0-4 counts as late night
    for name, lo, hi in BUCKETS:
        if lo <= h < hi:
            return name
    return "night"


@dataclass
class Replenishment:
    product_id: int
    median_days: float
    days_since: float
    due_ratio: float


def replenishment(purchases: dict[int, list[datetime]], now: datetime) -> list[Replenishment]:
    """Products ranked by how overdue they are on the user's own buying rhythm."""
    out: list[Replenishment] = []
    for pid, times in purchases.items():
        if len(times) < MIN_PURCHASES:
            continue
        gaps = [(b - a).total_seconds() / 86400 for a, b in zip(times, times[1:], strict=False)]
        med = median(gaps)
        if med <= 0:
            continue
        days_since = (now - times[-1]).total_seconds() / 86400
        ratio = days_since / med
        if ratio >= DUE_THRESHOLD:
            out.append(Replenishment(pid, round(med, 1), round(days_since, 1), round(ratio, 2)))
    return sorted(out, key=lambda r: r.due_ratio, reverse=True)


def category_lift(events: list[tuple[datetime, str]], now: datetime) -> dict[str, float]:
    """How much more than usual the user shops each category in the current time bucket."""
    if not events:
        return {}
    current = bucket_of(now.hour)
    total: dict[str, int] = {}
    in_bucket: dict[str, int] = {}
    bucket_n = 0
    for ts, cat in events:
        total[cat] = total.get(cat, 0) + 1
        if bucket_of(ts.hour) == current:
            in_bucket[cat] = in_bucket.get(cat, 0) + 1
            bucket_n += 1
    if bucket_n == 0:
        return {}
    n = len(events)
    lifts: dict[str, float] = {}
    for cat, cnt in in_bucket.items():
        base = total[cat] / n
        share = cnt / bucket_n
        lifts[cat] = round(share / base, 2) if base > 0 else 0.0
    return lifts


def complements(baskets: list[set[int]], cart: set[int]) -> list[tuple[int, float]]:
    """Co-purchase suggestions for the current cart, ranked by lift (max over cart items)."""
    if not cart or not baskets:
        return []
    n = len(baskets)
    count: dict[int, int] = {}
    pair: dict[tuple[int, int], int] = {}
    for basket in baskets:
        for a in basket:
            count[a] = count.get(a, 0) + 1
        for a in basket & cart:
            for b in basket - cart:
                pair[(a, b)] = pair.get((a, b), 0) + 1
    best: dict[int, float] = {}
    for (a, b), n_ab in pair.items():
        if n_ab < MIN_BASKET_SUPPORT:
            continue
        p_b = count[b] / n
        lift = (n_ab / count[a]) / p_b
        if lift > 1.0:
            best[b] = max(best.get(b, 0.0), round(lift, 2))
    return sorted(best.items(), key=lambda kv: kv[1], reverse=True)


@dataclass
class Suggestion:
    product_id: int
    score: float
    reasons: list[str] = field(default_factory=list)


def for_you(
    purchases: dict[int, list[datetime]],
    events: list[tuple[datetime, str]],
    categories: dict[int, str],
    now: datetime,
    limit: int = 8,
) -> list[Suggestion]:
    """Blend replenishment and context into one ranked, explained list."""
    scores: dict[int, Suggestion] = {}

    for r in replenishment(purchases, now):
        s = scores.setdefault(r.product_id, Suggestion(r.product_id, 0.0))
        s.score += 2.0 * min(r.due_ratio, 2.0)
        s.reasons.append(f"You buy this about every {r.median_days:g} days — it has been {r.days_since:g}")

    lifts = category_lift(events, now)
    bucket = bucket_of(now.hour)
    freq = {pid: len(times) for pid, times in purchases.items() if len(times) >= MIN_PURCHASES}
    for pid, n_buys in freq.items():
        lift = lifts.get(categories.get(pid, ""), 0.0)
        if lift > 1.1:
            s = scores.setdefault(pid, Suggestion(pid, 0.0))
            s.score += 0.5 * min(lift, 3.0)
            s.reasons.append(f"You usually shop {categories[pid]} in the {bucket} (×{lift:g} your average)")
        elif pid not in scores and n_buys >= 5:
            s = scores.setdefault(pid, Suggestion(pid, 0.0))
            s.score += 0.2
            s.reasons.append(f"A regular of yours — bought {n_buys} times")

    ranked = sorted(scores.values(), key=lambda s: s.score, reverse=True)
    for s in ranked:
        s.score = round(s.score, 2)
    return ranked[:limit]
