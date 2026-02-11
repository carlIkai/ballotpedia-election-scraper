from bp_scraper.io.http import canonicalize_url

def test_canonicalize_requires_year():
    assert canonicalize_url("/United_States_Senate_election_in_Arizona,_2022", 2024, "senate") is None

def test_canonicalize_valid_senate():
    url = canonicalize_url("/United_States_Senate_election_in_Arizona,_2024", 2024, "senate")
    assert url is not None
