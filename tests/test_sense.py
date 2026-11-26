"""Unit tests for the three sense signals (pure functions, no HTTP, no DB)."""

from datetime import datetime, timedelta

from cartsense import sense


def every(days: float, n: int, end: datetime, hour: int = 8) -> list[datetime]:
    """n purchases spaced `days` apart, ending `days` before `end`."""
    start = end - timedelta(days=days * n)
    return [(start + timedelta(days=days * i)).replace(hour=hour) for i in range(n)]


NOW = datetime(2026, 11, 26, 8, 0)


class TestBuckets:
    def test_day_hours(self):
        assert sense.bucket_of(7) == "morning"
        assert sense.bucket_of(13) == "afternoon"
        assert sense.bucket_of(19) == "evening"
        assert sense.bucket_of(22) == "night"

    def test_late_night_wraps(self):
        assert sense.bucket_of(2) == "night"


class TestReplenishment:
    def test_overdue_item_is_due_with_correct_median(self):
        # bought every 2 days, last one 3 days ago -> ratio 1.5
        times = every(2.0, 10, NOW - timedelta(days=1))
        out = sense.replenishment({1: times}, NOW)
        assert len(out) == 1
        r = out[0]
        assert r.median_days == 2.0
        assert r.due_ratio >= 1.0

    def test_recent_purchase_not_due(self):
        times = [*every(5.0, 8, NOW - timedelta(days=20)), NOW - timedelta(hours=6)]
        assert sense.replenishment({1: times}, NOW) == []

    def test_too_few_purchases_ignored(self):
        assert sense.replenishment({1: [NOW - timedelta(days=9), NOW - timedelta(days=6)]}, NOW) == []

    def test_most_overdue_first(self):
        a = every(2.0, 6, NOW - timedelta(days=4))  # very overdue
        b = every(3.0, 6, NOW - timedelta(days=3))  # mildly overdue
        out = sense.replenishment({1: a, 2: b}, NOW)
        assert [r.product_id for r in out] == [1, 2]


class TestCategoryLift:
    def test_morning_category_has_lift_in_morning(self):
        events = [(NOW - timedelta(days=d), "Dairy & Breakfast") for d in range(1, 11)]  # all at 8:00
        events += [((NOW - timedelta(days=d)).replace(hour=19), "Snacks & Beverages") for d in range(1, 11)]
        lifts = sense.category_lift(events, NOW)  # NOW is morning
        assert lifts["Dairy & Breakfast"] > 1.5
        assert "Snacks & Beverages" not in lifts

    def test_no_history(self):
        assert sense.category_lift([], NOW) == {}


class TestComplements:
    def test_lift_pairs_found_and_cart_excluded(self):
        baskets = [{1, 2}] * 6 + [{1}] * 2 + [{2}] * 2 + [{3}] * 10
        out = sense.complements(baskets, {1})
        pids = [p for p, _ in out]
        assert 2 in pids and 1 not in pids and 3 not in pids

    def test_low_support_ignored(self):
        baskets = [{1, 2}] * 2 + [{3}] * 20  # pair seen only twice (< MIN_BASKET_SUPPORT)
        assert sense.complements(baskets, {1}) == []

    def test_empty_cart(self):
        assert sense.complements([{1, 2}], set()) == []


class TestForYou:
    def test_blended_ranking_has_reasons(self):
        purchases = {
            1: every(2.0, 10, NOW - timedelta(days=2)),  # overdue milk
            17: every(3.0, 8, NOW - timedelta(days=1), hour=19),  # evening chips, not morning
        }
        events = [(t, "Dairy & Breakfast") for t in purchases[1]] + [(t, "Snacks & Beverages") for t in purchases[17]]
        cats = {1: "Dairy & Breakfast", 17: "Snacks & Beverages"}
        out = sense.for_you(purchases, events, cats, NOW)
        assert out[0].product_id == 1  # replenishment-due item wins in the morning
        assert all(s.reasons for s in out)
        assert out == sorted(out, key=lambda s: s.score, reverse=True)

    def test_limit_respected(self):
        purchases = {i: every(2.0, 6, NOW - timedelta(days=3)) for i in range(1, 15)}
        cats = {i: "Staples" for i in range(1, 15)}
        assert len(sense.for_you(purchases, [], cats, NOW, limit=5)) == 5
