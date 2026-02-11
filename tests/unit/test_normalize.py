from bp_scraper.parsing.normalize import nws, norm_name

def test_nws():
    assert nws("  a   b ") == "a b"

def test_norm_name():
    assert norm_name("Jane Doe (Incumbent)") == "jane doe"
