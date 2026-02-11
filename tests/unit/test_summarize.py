from bp_scraper.transform.summarize import summarize_race

def test_summarize_winner_by_pct():
    rows = [
        {"candidate": "A", "pct": 48.0, "total_votes": 1000},
        {"candidate": "B", "pct": 52.0, "total_votes": 900},
    ]
    out = summarize_race(rows)
    assert out["winner_name"] == "B"

def test_summarize_fallback_votes():
    rows = [
        {"candidate": "A", "pct": None, "total_votes": 100},
        {"candidate": "B", "pct": None, "total_votes": 200},
    ]
    out = summarize_race(rows)
    assert out["winner_name"] == "B"
