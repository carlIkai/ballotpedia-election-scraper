from __future__ import annotations
import re
import requests

BASE = "https://ballotpedia.org/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/119.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

DEFAULT_DELAY = 0.6
DEFAULT_RETRIES = 5
DEFAULT_JITTER = 0.35
DEFAULT_BACKOFF = 1.6

USPS = {
    "Alabama":"AL","Alaska":"AK","Arizona":"AZ","Arkansas":"AR","California":"CA","Colorado":"CO",
    "Connecticut":"CT","Delaware":"DE","Florida":"FL","Georgia":"GA","Hawaii":"HI","Idaho":"ID",
    "Illinois":"IL","Indiana":"IN","Iowa":"IA","Kansas":"KS","Kentucky":"KY","Louisiana":"LA",
    "Maine":"ME","Maryland":"MD","Massachusetts":"MA","Michigan":"MI","Minnesota":"MN","Mississippi":"MS",
    "Missouri":"MO","Montana":"MT","Nebraska":"NE","Nevada":"NV","New Hampshire":"NH","New Jersey":"NJ",
    "New Mexico":"NM","New York":"NY","North Carolina":"NC","North Dakota":"ND","Ohio":"OH","Oklahoma":"OK",
    "Oregon":"OR","Pennsylvania":"PA","Rhode Island":"RI","South Carolina":"SC","South Dakota":"SD",
    "Tennessee":"TN","Texas":"TX","Utah":"UT","Vermont":"VT","Virginia":"VA","Washington":"WA",
    "West Virginia":"WV","Wisconsin":"WI","Wyoming":"WY","District of Columbia":"DC"
}

USPS_INV = {v: k for k, v in USPS.items()}

HOUSE_AT_LARGE_STATES = {
    "Alaska","Delaware","North Dakota","South Dakota","Vermont","Wyoming","District of Columbia"
}

PCT_RE = re.compile(r"([0-9]{1,3}(?:\.[0-9]+)?)\s*%")

PARTY_FROM_CLASS = {
    "cc-democratic": "Democratic",
    "cc-republican": "Republican",
    "cc-libertarian": "Libertarian",
    "cc-green": "Green",
    "cc-independent": "Independent",
    "cc-nonpartisan": "Nonpartisan",
    "cc-other": "Other",
}

PAREN_PARTY = {"d":"Democratic","r":"Republican","l":"Libertarian","g":"Green","i":"Independent"}

PRIMARY_PARTY_FROM_LABEL = {
    "democratic primary": "Democratic",
    "republican primary": "Republican",
    "libertarian primary": "Libertarian",
    "green primary": "Green",
    "nonpartisan primary": "Nonpartisan",
    "working families primary": "Working Families",
    "aloha ʻāina primary": "Aloha ʻĀina",
    "aloha aina primary": "Aloha ʻĀina",
    "constitution primary": "Constitution",
    "progressive primary": "Progressive",
    "independent american primary": "Independent American",
    "independent primary": "Independent",
}

EXTRA_PARTY_KEYS = {
    "peace and freedom": "Peace and Freedom",
    "socialist workers": "Socialist Workers",
    "no party preference": "Independent",
    "american independent": "American Independent",
    "constitution": "Constitution",
    "progressive": "Progressive",
    "working families": "Working Families",
    "aloha ʻāina": "Aloha ʻĀina",
    "aloha aina": "Aloha ʻĀina",
    "aloha-aina": "Aloha ʻĀina",
    "independent american": "Independent American",
}

SENATE_OVERVIEW_TEMPLATE = "United_States_Senate_elections,_{year}"
HOUSE_OVERVIEW_TEMPLATE  = "United_States_House_of_Representatives_elections,_{year}"

STATE_ELECTIONS_OVERVIEW_TEMPLATE = "{state}_elections,_{year}"

CANON_SENATE_STATE_URL = re.compile(
    r"/United_States_Senate_(special_)?election_in_[^,]+,_\d{4}$", re.I
)

APOS_CHAR_CLASS = r"(?:'|\u2019)"
APOS_ENC_CLASS = r"(?:%27|%E2%80%99)"
APOS_ANY = rf"(?:{APOS_ENC_CLASS}|{APOS_CHAR_CLASS})"

CANON_HOUSE_STATE_URL = re.compile(
    r"/United_States_House(_of_Representatives)?_elections?_in_[^,]+,_\d{4}$",
    re.I
)

CANON_HOUSE_DISTRICT_URL = re.compile(
    rf"/[A-Za-z0-9_%\-\u2019']+(?:At[-_]large|[0-9]{{1,2}}(?:st|nd|rd|th))_Congressional_District_election,_\d{{4}}$",
    re.I
)

CANON_STATE_GOV_URL = re.compile(
    r"/[A-Za-z0-9_%\-\u2019']+_gubernatorial_election,_(19|20)\d{2}$", re.I
)

CANON_STATE_LT_GOV_URL = re.compile(
    r"/[A-Za-z0-9_%\-\u2019']+_lieutenant_gubernatorial_election,_(19|20)\d{2}$", re.I
)

CANON_STATE_AG_URL = re.compile(
    r"/[A-Za-z0-9_%\-\u2019']+_attorney_general_election,_(19|20)\d{2}$", re.I
)

CANON_STATE_LOWER_URL = re.compile(
    r"/[A-Za-z0-9_%\-\u2019']+_(?:house_of_delegates|house_of_representatives|state_house)_election,_(19|20)\d{2}$",
    re.I
)

CANON_STATE_UPPER_URL = re.compile(
    r"/[A-Za-z0-9_%\-\u2019']+_(?:state_senate|senate)_election,_(19|20)\d{2}$",
    re.I
)

CANON_STATE_LEG_DISTRICT_URL = re.compile(
    r"/[A-Za-z0-9_%\-\u2019']+_(?:house_of_delegates|house_of_representatives|state_house|state_senate|senate)_district_\d{1,3}_election,_(19|20)\d{2}$",
    re.I
)


CANON_STATE_LTGOV_URL = CANON_STATE_LT_GOV_URL
CANON_STATE_ATTORNEY_GENERAL_URL = CANON_STATE_AG_URL
CANON_STATE_STATE_LOWER_URL = CANON_STATE_LOWER_URL
CANON_STATE_STATE_UPPER_URL = CANON_STATE_UPPER_URL

HEADER_OK_PATTERNS_GENERAL = [
    re.compile(r"\bGeneral(?:\s+runoff)?\s+election\b", re.I),
    re.compile(r"\bGeneral(?:\s+runoff)?\s+election\s+results\b", re.I),
    re.compile(r"\bRunoff\s+election\b", re.I),
]

PRIMARY_WORD_RE = re.compile(r"\bprimary\b", re.I)
PRIMARY_RUNOFF_RE = re.compile(r"\brunoff\b", re.I)
LA_PRIMARY_PATTERNS = [re.compile(r"\bNonpartisan\s+blanket\s+primary\b", re.I)]

PRIMARY_PARTY_MAP = {
    "democratic":"Democratic","republican":"Republican","libertarian":"Libertarian","green":"Green",
    "independent":"Independent","independent american":"Independent American","nonpartisan":"Nonpartisan",
    "working families":"Working Families","aloha":"Aloha ʻĀina",
    "constitution":"Constitution","progressive":"Progressive",
}

YEAR_ONLY_RE = re.compile(r"\b(19|20)\d{2}\b")
PAST_ELEX_RE = re.compile(r"\b(past|previous)\s+elections\b", re.I)

AGG_WRITEIN_PAT = re.compile(r"^\s*Other/Write-in votes\s*$", re.I)
WRITEIN_SUFFIX = re.compile(r"\s*\(Write-in\)\s*$", re.I)

ORD_RE = re.compile(r"(\d+)(st|nd|rd|th)", re.I)
