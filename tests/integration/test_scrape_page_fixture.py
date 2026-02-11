from bs4 import BeautifulSoup
from bp_scraper.pipelines.scrape import scrape_page

def test_scrape_page_fixture(monkeypatch):
    html = "<html><body><h2>General election results</h2></body></html>"

    def fake_get_soup(url: str, **_kwargs):
        return BeautifulSoup(html, "lxml")

    monkeypatch.setattr("bp_scraper.pipelines.scrape.get_soup", fake_get_soup)

    rows, summaries, cards = scrape_page(
        url="https://ballotpedia.org/United_States_Senate_election_in_Arizona,_2024",
        year=2024,
        chamber="senate",
        verbose=False,
        primary=False,
        delay=0.0,
        retries=0,
        scope="federal",
    )

    assert isinstance(rows, list)
    assert isinstance(summaries, list)
    assert isinstance(cards, list)
