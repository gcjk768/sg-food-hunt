"""Region and planning district from a Singapore postal code (no network needed).

Postal sectors are the first two digits. The mapping follows URA's 28 postal districts and
groups them into the five regions used throughout the project.
"""

from __future__ import annotations

_DISTRICTS: dict[int, tuple[str, str, tuple[str, ...]]] = {
    1: ("D01 Raffles Place / Marina", "Central", ("01", "02", "03", "04", "05", "06")),
    2: ("D02 Tanjong Pagar / Chinatown", "Central", ("07", "08")),
    3: ("D03 Queenstown / Tiong Bahru", "Central", ("14", "15", "16")),
    4: ("D04 Harbourfront / Telok Blangah", "Central", ("09", "10")),
    5: ("D05 Pasir Panjang / Clementi", "West", ("11", "12", "13")),
    6: ("D06 City Hall / Clarke Quay", "Central", ("17",)),
    7: ("D07 Bugis / Beach Road", "Central", ("18", "19")),
    8: ("D08 Little India / Farrer Park", "Central", ("20", "21")),
    9: ("D09 Orchard / River Valley", "Central", ("22", "23")),
    10: ("D10 Tanglin / Holland / Bukit Timah", "Central", ("24", "25", "26", "27")),
    11: ("D11 Novena / Newton / Thomson", "Central", ("28", "29", "30")),
    12: ("D12 Balestier / Toa Payoh", "Central", ("31", "32", "33")),
    13: ("D13 Macpherson / Potong Pasir", "Central", ("34", "35", "36", "37")),
    14: ("D14 Geylang / Paya Lebar", "Central", ("38", "39", "40", "41")),
    15: ("D15 Katong / Joo Chiat / Marine Parade", "East", ("42", "43", "44", "45")),
    16: ("D16 Bedok / Upper East Coast", "East", ("46", "47", "48")),
    17: ("D17 Changi / Loyang", "East", ("49", "50", "81")),
    18: ("D18 Tampines / Pasir Ris", "East", ("51", "52")),
    19: ("D19 Serangoon / Hougang / Punggol", "North East", ("53", "54", "55", "82")),
    20: ("D20 Ang Mo Kio / Bishan", "North East", ("56", "57")),
    21: ("D21 Upper Bukit Timah / Clementi Park", "West", ("58", "59")),
    22: ("D22 Jurong / Boon Lay", "West", ("60", "61", "62", "63", "64")),
    23: ("D23 Bukit Batok / Choa Chu Kang", "West", ("65", "66", "67", "68")),
    24: ("D24 Lim Chu Kang / Tengah", "West", ("69", "70", "71")),
    25: ("D25 Woodlands / Admiralty", "North", ("72", "73")),
    26: ("D26 Upper Thomson / Springleaf", "North", ("77", "78")),
    27: ("D27 Yishun / Sembawang", "North", ("75", "76")),
    28: ("D28 Seletar / Yio Chu Kang", "North East", ("79", "80")),
}
_SECTOR_LOOKUP = {
    sector: (name, region) for name, region, sectors in _DISTRICTS.values() for sector in sectors
}


def region_for_postal(postal_code: str | None) -> tuple[str | None, str | None]:
    """Return (planning district, region) for a six digit postal code."""
    if not postal_code or len(postal_code) != 6 or not postal_code.isdigit():
        return None, None
    hit = _SECTOR_LOOKUP.get(postal_code[:2])
    return hit if hit else (None, None)
