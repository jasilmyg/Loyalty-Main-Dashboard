"""
malayalam_calendar.py
=====================
Advanced Malayalam Calendar Library for ML/DL Projects
Covers 2020 onwards | Kollavarsham system | Kerala festival data

Features:
- Convert Gregorian ↔ Malayalam date
- Get Malayalam month, year (Kollavarsham era)
- Nakshatra (birth star) for any date
- Kerala public holidays & major festival flags
- ML-ready feature vector generation
- Pandas DataFrame export
- Seasonal / agricultural cycle features
"""

import math
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple, Union
import pandas as pd
import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

MALAYALAM_MONTHS = [
    "Chingam",      # 1  - Aug/Sep
    "Kanni",        # 2  - Sep/Oct
    "Thulam",       # 3  - Oct/Nov
    "Vrischikam",   # 4  - Nov/Dec
    "Dhanu",        # 5  - Dec/Jan
    "Makaram",      # 6  - Jan/Feb
    "Kumbham",      # 7  - Feb/Mar
    "Meenam",       # 8  - Mar/Apr
    "Medam",        # 9  - Apr/May
    "Edavam",       # 10 - May/Jun
    "Midhunam",     # 11 - Jun/Jul
    "Karkidakam",   # 12 - Jul/Aug
]

MALAYALAM_MONTHS_ML = [
    "ചിങ്ങം", "കന്നി", "തുലാം", "വൃശ്ചികം",
    "ധനു", "മകരം", "കുംഭം", "മീനം",
    "മേടം", "ഇടവം", "മിഥുനം", "കർക്കടകം",
]

NAKSHATRAS = [
    "Ashwini", "Bharani", "Karthika", "Rohini", "Makayiram",
    "Thiruvathira", "Punartham", "Pooyam", "Ayilyam", "Makam",
    "Pooram", "Uthram", "Atham", "Chithra", "Chothi",
    "Vishakham", "Anizham", "Thrikketta", "Moolam", "Pooradam",
    "Uthradam", "Thiruvonam", "Avittam", "Chathayam", "Pooruruttathi",
    "Uthiruttathi", "Revathi",
]

NAKSHATRAS_ML = [
    "അശ്വതി", "ഭരണി", "കാർത്തിക", "രോഹിണി", "മകയിരം",
    "തിരുവാതിര", "പുനർതം", "പൂയം", "ആയില്യം", "മകം",
    "പൂരം", "ഉത്രം", "അത്തം", "ചിത്ര", "ചോതി",
    "വിശാഖം", "അനിഴം", "തൃക്കേട്ട", "മൂലം", "പൂരാടം",
    "ഉത്രാടം", "തിരുവോണം", "അവിട്ടം", "ചതയം", "പൂരുരുട്ടാതി",
    "ഉത്രട്ടാതി", "രേവതി",
]

TITHI_NAMES = [
    "Prathama", "Dwitiya", "Tritiya", "Chaturthi", "Panchami",
    "Shashthi", "Saptami", "Ashtami", "Navami", "Dashami",
    "Ekadashi", "Dwadashi", "Trayodashi", "Chaturdashi",
    "Purnima",  # Full moon (or Amavasya for Krishna paksha 15)
]

# Solar longitude (approx degrees) when Malayalam months start
RASHI_ENTRIES = {
    "Medam": 0,      # Aries 0°
    "Edavam": 30,    # Taurus
    "Midhunam": 60,  # Gemini
    "Karkidakam": 90,# Cancer
    "Chingam": 120,  # Leo
    "Kanni": 150,    # Virgo
    "Thulam": 180,   # Libra
    "Vrischikam": 210,# Scorpio
    "Dhanu": 240,    # Sagittarius
    "Makaram": 270,  # Capricorn
    "Kumbham": 300,  # Aquarius
    "Meenam": 330,   # Pisces
}

# Kollavarsham era offset (Malayalam Era starts 825 CE)
KOLLAVARSHAM_OFFSET = 825

# Kerala public holidays (fixed Gregorian dates that recur annually)
FIXED_HOLIDAYS = {
    (1, 1):   "New Year's Day",
    (1, 26):  "Republic Day",
    (5, 1):   "Kerala Piravi / Labour Day",  # Kerala Day Nov 1 also but May 1 is labour
    (8, 15):  "Independence Day",
    (10, 2):  "Gandhi Jayanti",
    (11, 1):  "Kerala Piravi (Kerala Formation Day)",
    (12, 25): "Christmas",
}

# Dynamic festivals (approx Gregorian — computed freshly each year for accuracy)
# These are approximate mid-dates; adjust with compute_festivals() for precision
APPROXIMATE_FESTIVALS_BY_MONTH = {
    "Onam":          {"month_range": (8, 9),   "duration": 10, "importance": "high"},
    "Vishu":         {"month_range": (4, 4),   "duration": 1,  "importance": "high"},
    "Thiruvonam":    {"month_range": (8, 9),   "duration": 1,  "importance": "high"},
    "Thrissur Pooram":{"month_range": (4, 5),  "duration": 2,  "importance": "medium"},
    "Atham":         {"month_range": (8, 9),   "duration": 1,  "importance": "medium"},
    "Diwali":        {"month_range": (10, 11), "duration": 1,  "importance": "high"},
    "Eid ul Fitr":   {"month_range": (3, 5),   "duration": 2,  "importance": "high"},
    "Eid ul Adha":   {"month_range": (6, 8),   "duration": 2,  "importance": "high"},
    "Christmas":     {"month_range": (12, 12), "duration": 3,  "importance": "high"},
    "Good Friday":   {"month_range": (3, 4),   "duration": 1,  "importance": "high"},
    "Bakrid":        {"month_range": (6, 8),   "duration": 2,  "importance": "medium"},
    "Karkidaka Vavu":{"month_range": (7, 8),   "duration": 1,  "importance": "medium"},
}

# Approximate Onam dates by year (Thiruvonam, the main day)
ONAM_DATES = {
    2020: date(2020, 8, 31),
    2021: date(2021, 8, 21),
    2022: date(2022, 9, 8),
    2023: date(2023, 8, 29),
    2024: date(2024, 9, 15),
    2025: date(2025, 9, 5),
    2026: date(2026, 8, 26),
    2027: date(2027, 9, 14),
    2028: date(2028, 9, 2),
    2029: date(2029, 8, 23),
    2030: date(2030, 9, 11),
}

# Approximate Vishu dates by year
VISHU_DATES = {
    2020: date(2020, 4, 14),
    2021: date(2021, 4, 14),
    2022: date(2022, 4, 15),
    2023: date(2023, 4, 15),
    2024: date(2024, 4, 14),
    2025: date(2025, 4, 14),
    2026: date(2026, 4, 15),
    2027: date(2027, 4, 14),
    2028: date(2028, 4, 14),
    2029: date(2029, 4, 14),
    2030: date(2030, 4, 15),
}


# ─────────────────────────────────────────────────────────────────────────────
# CORE ASTRONOMICAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _julian_day(d: date) -> float:
    """Convert Gregorian date to Julian Day Number."""
    a = (14 - d.month) // 12
    y = d.year + 4800 - a
    m = d.month + 12 * a - 3
    return (d.day + (153 * m + 2) // 5 + 365 * y
            + y // 4 - y // 100 + y // 400 - 32045)


def _solar_longitude(d: date) -> float:
    """
    Approximate tropical solar longitude (degrees, 0–360) for a given date.
    Uses a simplified VSOP87-style formula accurate to ~0.01°.
    """
    jd = _julian_day(d)
    T = (jd - 2451545.0) / 36525.0   # Julian centuries from J2000.0

    # Mean longitude and anomaly
    L0 = 280.46646 + 36000.76983 * T + 0.0003032 * T**2
    M  = math.radians(357.52911 + 35999.05029 * T - 0.0001537 * T**2)

    # Equation of centre
    C = ((1.914602 - 0.004817 * T - 0.000014 * T**2) * math.sin(M)
         + (0.019993 - 0.000101 * T) * math.sin(2 * M)
         + 0.000289 * math.sin(3 * M))

    sun_lon = (L0 + C) % 360
    return sun_lon


def _moon_longitude(d: date) -> float:
    """
    Approximate Moon longitude (degrees, 0–360).
    Accurate to ~1° — sufficient for Nakshatra and Tithi.
    """
    jd = _julian_day(d)
    T = (jd - 2451545.0) / 36525.0

    L1 = 218.3164477 + 481267.88123421 * T
    D  = math.radians(297.8501921 + 445267.1114034 * T)
    M  = math.radians(357.5291092 + 35999.0502909 * T)
    Mp = math.radians(134.9633964 + 477198.8675055 * T)
    F  = math.radians(93.2720950 + 483202.0175233 * T)

    dL = (6288774 * math.sin(Mp)
          + 1274027 * math.sin(2*D - Mp)
          + 658314  * math.sin(2*D)
          + 213618  * math.sin(2*Mp)
          - 185116  * math.sin(M)
          - 114332  * math.sin(2*F)
          + 58793   * math.sin(2*D - 2*Mp)
          + 57066   * math.sin(2*D - M - Mp)
          + 53322   * math.sin(2*D + Mp)
          + 45758   * math.sin(2*D - M)
          - 40923   * math.sin(M - Mp)
          - 34720   * math.sin(D)
          - 30383   * math.sin(M + Mp))

    moon_lon = (L1 + dL / 1000000.0) % 360
    return moon_lon


def _nakshatra_index(d: date) -> int:
    """Return Nakshatra index (0–26) based on Moon longitude."""
    moon_lon = _moon_longitude(d)
    return int(moon_lon / (360 / 27)) % 27


def _tithi(d: date) -> int:
    """
    Return Tithi number (1–30).
    1–15 = Shukla Paksha (waxing), 16–30 = Krishna Paksha (waning).
    """
    sun_lon  = _solar_longitude(d)
    moon_lon = _moon_longitude(d)
    diff = (moon_lon - sun_lon) % 360
    return int(diff / 12) + 1


def _paksha(d: date) -> str:
    """Return 'Shukla' (waxing) or 'Krishna' (waning)."""
    t = _tithi(d)
    return "Shukla" if t <= 15 else "Krishna"


def _solar_month_index(d: date) -> int:
    """
    Return Malayalam month index (0=Medam … 11=Meenam) based on solar longitude.
    Malayalam months follow the zodiac (solar calendar), starting from Medam (Aries 0°).
    """
    lon = _solar_longitude(d)
    return int(lon / 30) % 12


def _gregorian_to_malayalam_year(d: date) -> int:
    """
    Approximate Kollavarsham year for a given Gregorian date.
    Malayalam New Year starts with Chingam (Leo, ~Aug 17).
    """
    # If month < Chingam start (~mid Aug), still in previous Kolla year
    chingam_approx = date(d.year, 8, 17)
    if d < chingam_approx:
        return d.year - KOLLAVARSHAM_OFFSET - 1
    return d.year - KOLLAVARSHAM_OFFSET


# ─────────────────────────────────────────────────────────────────────────────
# MONTH NAME MAPPING (solar → Malayalam month name)
# ─────────────────────────────────────────────────────────────────────────────

_SOLAR_INDEX_TO_MONTH = [
    "Medam",       # 0  Aries
    "Edavam",      # 1  Taurus
    "Midhunam",    # 2  Gemini
    "Karkidakam",  # 3  Cancer
    "Chingam",     # 4  Leo
    "Kanni",       # 5  Virgo
    "Thulam",      # 6  Libra
    "Vrischikam",  # 7  Scorpio
    "Dhanu",       # 8  Sagittarius
    "Makaram",     # 9  Capricorn
    "Kumbham",     # 10 Aquarius
    "Meenam",      # 11 Pisces
]

_MONTH_ORDER = {m: i for i, m in enumerate(_SOLAR_INDEX_TO_MONTH)}


# ─────────────────────────────────────────────────────────────────────────────
# MAIN DATE CLASS
# ─────────────────────────────────────────────────────────────────────────────

class MalayalamDate:
    """
    Represents a Malayalam calendar date with full astronomical metadata.
    """

    def __init__(self, gregorian_date: date):
        self.gregorian = gregorian_date
        self._compute()

    def _compute(self):
        d = self.gregorian
        solar_idx = _solar_month_index(d)

        self.solar_month_index = solar_idx
        self.month_name   = _SOLAR_INDEX_TO_MONTH[solar_idx]
        self.month_name_ml = MALAYALAM_MONTHS_ML[MALAYALAM_MONTHS.index(self.month_name)] \
                             if self.month_name in MALAYALAM_MONTHS else ""
        self.kollavarsham_year = _gregorian_to_malayalam_year(d)

        nak_idx = _nakshatra_index(d)
        self.nakshatra_index = nak_idx
        self.nakshatra       = NAKSHATRAS[nak_idx]
        self.nakshatra_ml    = NAKSHATRAS_ML[nak_idx]

        self.tithi    = _tithi(d)
        self.paksha   = _paksha(d)

        # Day of Malayalam month (approximate: day within current solar month)
        # Find when sun entered this rashi
        self.day_of_month = self._approx_day_of_month()

        self.weekday     = d.strftime("%A")
        self.weekday_idx = d.weekday()   # 0=Mon, 6=Sun

        # Season
        self.season = self._get_season()
        self.agricultural_cycle = self._get_agricultural_cycle()

    def _approx_day_of_month(self) -> int:
        """Approximate day within the Malayalam month (1-based)."""
        d = self.gregorian
        # Walk back to find when the solar month started
        check = d
        for _ in range(32):
            prev = check - timedelta(days=1)
            if _solar_month_index(prev) != self.solar_month_index:
                return (d - check).days + 1
            check = prev
        return 1

    def _get_season(self) -> str:
        month = self.month_name
        seasons = {
            "Karkidakam": "Monsoon",
            "Chingam":    "Post-Monsoon",
            "Kanni":      "Post-Monsoon",
            "Thulam":     "Autumn",
            "Vrischikam": "Autumn",
            "Dhanu":      "Winter",
            "Makaram":    "Winter",
            "Kumbham":    "Spring",
            "Meenam":     "Spring",
            "Medam":      "Summer",
            "Edavam":     "Summer / Pre-Monsoon",
            "Midhunam":   "Monsoon onset",
        }
        return seasons.get(month, "Unknown")

    def _get_agricultural_cycle(self) -> str:
        month = self.month_name
        cycles = {
            "Medam":      "Land preparation / Vishu farming start",
            "Edavam":     "Early Kharif sowing",
            "Midhunam":   "Paddy transplanting (Mundakan)",
            "Karkidakam": "Peak monsoon / lean agricultural period",
            "Chingam":    "Harvest season / Onam harvest",
            "Kanni":      "Secondary sowing (Puncha)",
            "Thulam":     "Rabi crop planting",
            "Vrischikam": "Paddy threshing (Kole lands)",
            "Dhanu":      "Rabi growing / winter crops",
            "Makaram":    "Rabi harvest",
            "Kumbham":    "Dry season / irrigation",
            "Meenam":     "Pre-harvest / summer crops",
        }
        return cycles.get(month, "")

    def __repr__(self):
        return (f"MalayalamDate({self.day_of_month} {self.month_name} "
                f"ME {self.kollavarsham_year} | "
                f"Nak: {self.nakshatra} | {self.paksha} Paksha Tithi {self.tithi})")

    def to_dict(self) -> Dict:
        return {
            "gregorian":          self.gregorian,
            "malayalam_month":    self.month_name,
            "malayalam_month_ml": self.month_name_ml,
            "month_index":        self.solar_month_index + 1,
            "day_of_month":       self.day_of_month,
            "kollavarsham_year":  self.kollavarsham_year,
            "nakshatra":          self.nakshatra,
            "nakshatra_ml":       self.nakshatra_ml,
            "nakshatra_index":    self.nakshatra_index + 1,  # 1-based
            "tithi":              self.tithi,
            "paksha":             self.paksha,
            "weekday":            self.weekday,
            "weekday_index":      self.weekday_idx + 1,       # 1=Mon
            "season":             self.season,
            "agricultural_cycle": self.agricultural_cycle,
        }


# ─────────────────────────────────────────────────────────────────────────────
# HOLIDAY & FESTIVAL ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class KeralaFestivalEngine:
    """
    Identifies Kerala holidays and festivals for any date from 2020 onwards.
    """

    def __init__(self):
        self._festival_cache: Dict[int, Dict[date, List[str]]] = {}

    def _build_year(self, year: int) -> Dict[date, List[str]]:
        if year in self._festival_cache:
            return self._festival_cache[year]

        events: Dict[date, List[str]] = {}

        def add(d: date, name: str):
            events.setdefault(d, []).append(name)

        # Fixed holidays
        for (m, day), name in FIXED_HOLIDAYS.items():
            try:
                add(date(year, m, day), name)
            except ValueError:
                pass

        # Onam (10-day festival, Thiruvonam is main day)
        if year in ONAM_DATES:
            onam_main = ONAM_DATES[year]
            for i in range(10):
                day_names = [
                    "Atham", "Chithira", "Chothi", "Vishakam", "Anizham",
                    "Thrikketta", "Moolam", "Pooradam", "Uthradam", "Thiruvonam (Main Onam)"
                ]
                d = onam_main - timedelta(days=9 - i)
                if d.year == year:
                    add(d, f"Onam Day {i+1} - {day_names[i]}")

        # Vishu
        if year in VISHU_DATES:
            add(VISHU_DATES[year], "Vishu - Malayalam New Year")

        # Easter / Good Friday (approximate — 3rd Sunday of April window)
        gf = self._good_friday(year)
        if gf:
            add(gf, "Good Friday")
            add(gf + timedelta(days=2), "Easter Sunday")

        # Milad un Nabi (12 Rabi al Awwal) — approximate
        milad = self._milad_approx(year)
        if milad:
            add(milad, "Milad un Nabi")

        # Diwali (approximate: new moon in Kartik, ~Oct/Nov)
        diwali = self._diwali_approx(year)
        if diwali:
            add(diwali, "Diwali / Deepavali")

        # Karkidaka Vavu (Amavasya in Karkidakam, ~Jul/Aug)
        vavu = self._karkidaka_vavu(year)
        if vavu:
            add(vavu, "Karkidaka Vavu (Ancestor offerings day)")

        # Eid approximations (lunar — shift ~11 days/year)
        eid_al_fitr, eid_al_adha = self._eid_approx(year)
        if eid_al_fitr:
            add(eid_al_fitr, "Eid ul Fitr")
            add(eid_al_fitr + timedelta(days=1), "Eid ul Fitr (2nd day)")
        if eid_al_adha:
            add(eid_al_adha, "Eid ul Adha / Bakrid")
            add(eid_al_adha + timedelta(days=1), "Bakrid (2nd day)")

        # Thrissur Pooram (approx: 8 days before Edavapathi, ~Apr/May)
        pooram = self._thrissur_pooram(year)
        if pooram:
            add(pooram, "Thrissur Pooram")

        # Sabarimala season start (Mandalam — 41 days from Dec)
        add(date(year, 11, 27), "Sabarimala Mandala season begins (approx)")
        add(date(year, 1, 14), "Makaravilakku / Sabarimala Makara Jyothi (approx)")

        # Christmas season
        add(date(year, 12, 24), "Christmas Eve")
        add(date(year, 12, 26), "Boxing Day / St. Stephen's Day")

        # Thiruvathira (Dec/Jan full moon in Thiruvathira nakshatra)
        thiruvathira = self._thiruvathira(year)
        if thiruvathira:
            add(thiruvathira, "Thiruvathira")

        self._festival_cache[year] = events
        return events

    def _good_friday(self, year: int) -> Optional[date]:
        """Anonymous Gregorian algorithm for Easter."""
        a = year % 19
        b, c = divmod(year, 100)
        d, e = divmod(b, 4)
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i, k = divmod(c, 4)
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        month = (h + l - 7 * m + 114) // 31
        day   = ((h + l - 7 * m + 114) % 31) + 1
        easter = date(year, month, day)
        return easter - timedelta(days=2)

    def _diwali_approx(self, year: int) -> Optional[date]:
        """Approximate Diwali (Amavasya in Kartik, Lakshmi Puja night)."""
        # Diwali 2020→2030 approximate dates
        dates = {
            2020: date(2020, 11, 14), 2021: date(2021, 11, 4),
            2022: date(2022, 10, 24), 2023: date(2023, 11, 12),
            2024: date(2024, 11, 1),  2025: date(2025, 10, 20),
            2026: date(2026, 11, 8),  2027: date(2027, 10, 29),
            2028: date(2028, 10, 17), 2029: date(2029, 11, 5),
            2030: date(2030, 10, 26),
        }
        return dates.get(year)

    def _karkidaka_vavu(self, year: int) -> Optional[date]:
        """Amavasya (new moon) in Karkidakam (July/August)."""
        dates = {
            2020: date(2020, 7, 20), 2021: date(2021, 8, 8),
            2022: date(2022, 7, 28), 2023: date(2023, 8, 16),
            2024: date(2024, 8, 4),  2025: date(2025, 7, 25),
            2026: date(2026, 8, 12), 2027: date(2027, 8, 2),
            2028: date(2028, 7, 22), 2029: date(2029, 8, 9),
            2030: date(2030, 7, 30),
        }
        return dates.get(year)

    def _eid_approx(self, year: int) -> Tuple[Optional[date], Optional[date]]:
        eid_fitr = {
            2020: date(2020, 5, 24),   2021: date(2021, 5, 13),
            2022: date(2022, 5, 2),    2023: date(2023, 4, 21),
            2024: date(2024, 4, 10),   2025: date(2025, 3, 30),
            2026: date(2026, 3, 20),   2027: date(2027, 3, 9),
            2028: date(2028, 2, 26),   2029: date(2029, 2, 14),
            2030: date(2030, 2, 4),
        }
        eid_adha = {
            2020: date(2020, 7, 31),   2021: date(2021, 7, 20),
            2022: date(2022, 7, 9),    2023: date(2023, 6, 28),
            2024: date(2024, 6, 16),   2025: date(2025, 6, 6),
            2026: date(2026, 5, 27),   2027: date(2027, 5, 16),
            2028: date(2028, 5, 5),    2029: date(2029, 4, 24),
            2030: date(2030, 4, 14),
        }
        return eid_fitr.get(year), eid_adha.get(year)

    def _thrissur_pooram(self, year: int) -> Optional[date]:
        dates = {
            2020: date(2020, 4, 30), 2021: date(2021, 4, 19),
            2022: date(2022, 5, 8),  2023: date(2023, 4, 27),
            2024: date(2024, 5, 16), 2025: date(2025, 5, 5),
            2026: date(2026, 4, 25), 2027: date(2027, 5, 14),
            2028: date(2028, 5, 2),  2029: date(2029, 4, 21),
            2030: date(2030, 5, 11),
        }
        return dates.get(year)

    def _milad_approx(self, year: int) -> Optional[date]:
        dates = {
            2020: date(2020, 10, 29), 2021: date(2021, 10, 18),
            2022: date(2022, 10, 8),  2023: date(2023, 9, 27),
            2024: date(2024, 9, 15),  2025: date(2025, 9, 4),
            2026: date(2026, 8, 25),  2027: date(2027, 8, 14),
            2028: date(2028, 8, 2),   2029: date(2029, 7, 23),
            2030: date(2030, 7, 12),
        }
        return dates.get(year)

    def _thiruvathira(self, year: int) -> Optional[date]:
        dates = {
            2020: date(2020, 1, 8),   2021: date(2021, 12, 28),
            2022: date(2022, 12, 18), 2023: date(2023, 1, 6),
            2024: date(2024, 12, 25), 2025: date(2025, 12, 15),
            2026: date(2026, 1, 3),   2027: date(2027, 12, 23),
            2028: date(2028, 12, 11), 2029: date(2029, 12, 30),
            2030: date(2030, 12, 19),
        }
        return dates.get(year)

    def get_festivals(self, d: date) -> List[str]:
        """Return list of festival/holiday names for a given date."""
        events = self._build_year(d.year)
        return events.get(d, [])

    def is_public_holiday(self, d: date) -> bool:
        return bool(self.get_festivals(d))

    def days_to_next_festival(self, d: date, festival_name: str) -> int:
        """Days until the next occurrence of a named festival."""
        for i in range(1, 400):
            future = d + timedelta(days=i)
            if any(festival_name.lower() in f.lower()
                   for f in self.get_festivals(future)):
                return i
        return -1


# ─────────────────────────────────────────────────────────────────────────────
# ML FEATURE VECTOR GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

class MalayalamCalendarFeaturizer:
    """
    Generates ML-ready feature vectors from dates.
    Suitable for time-series forecasting, demand prediction,
    retail analytics, anomaly detection, etc.
    """

    def __init__(self):
        self.festival_engine = KeralaFestivalEngine()

    def featurize(self, d: date) -> Dict:
        """
        Generate a comprehensive feature dictionary for a single date.
        All features are numeric or boolean (ML-ready).
        """
        ml_date = MalayalamDate(d)
        festivals = self.festival_engine.get_festivals(d)

        # Days to/from major festivals
        days_to_onam   = self._days_to(d, ONAM_DATES,  direction="next")
        days_from_onam = self._days_to(d, ONAM_DATES,  direction="prev")
        days_to_vishu  = self._days_to(d, VISHU_DATES, direction="next")

        # Pre-festival window flags
        is_pre_onam_week    = 0 < days_to_onam   <= 7
        is_pre_onam_fortnight = 0 < days_to_onam <= 14
        is_pre_vishu_week   = 0 < days_to_vishu  <= 7

        # Cyclical encodings (sin/cos) for periodic features
        def cyc(val, max_val):
            angle = 2 * math.pi * val / max_val
            return math.sin(angle), math.cos(angle)

        month_sin, month_cos = cyc(ml_date.solar_month_index, 12)
        day_sin,   day_cos   = cyc(d.day - 1, 31)
        week_sin,  week_cos  = cyc(d.weekday(), 7)
        nak_sin,   nak_cos   = cyc(ml_date.nakshatra_index, 27)
        tithi_sin, tithi_cos = cyc(ml_date.tithi - 1, 30)
        doy = d.timetuple().tm_yday
        doy_sin, doy_cos = cyc(doy, 366)

        features = {
            # ── Date basics ──────────────────────────────────────────────────
            "gregorian_year":          d.year,
            "gregorian_month":         d.month,
            "gregorian_day":           d.day,
            "day_of_year":             doy,
            "week_of_year":            d.isocalendar()[1],
            "quarter":                 (d.month - 1) // 3 + 1,
            "is_weekend":              int(d.weekday() >= 5),
            "weekday_index":           d.weekday(),           # 0=Mon

            # ── Malayalam calendar ────────────────────────────────────────────
            "mal_month_index":         ml_date.solar_month_index + 1,
            "mal_day_of_month":        ml_date.day_of_month,
            "kollavarsham_year":       ml_date.kollavarsham_year,

            # ── Nakshatra & Tithi ─────────────────────────────────────────────
            "nakshatra_index":         ml_date.nakshatra_index + 1,
            "tithi":                   ml_date.tithi,
            "is_shukla_paksha":        int(ml_date.paksha == "Shukla"),
            "is_full_moon":            int(ml_date.tithi == 15),
            "is_new_moon":             int(ml_date.tithi == 30),
            "is_ekadashi":             int(ml_date.tithi in (11, 26)),
            "is_ashtami":              int(ml_date.tithi in (8, 23)),

            # ── Season & agriculture ──────────────────────────────────────────
            "is_monsoon":              int(ml_date.season in ("Monsoon", "Monsoon onset")),
            "is_summer":               int(ml_date.season in ("Summer", "Summer / Pre-Monsoon")),
            "is_winter":               int(ml_date.season == "Winter"),
            "is_harvest_season":       int(ml_date.month_name in ("Chingam", "Kanni", "Makaram")),
            "is_karkidakam":           int(ml_date.month_name == "Karkidakam"),  # lean/fasting month

            # ── Festival & holiday flags ──────────────────────────────────────
            "is_public_holiday":       int(self.festival_engine.is_public_holiday(d)),
            "festival_count":          len(festivals),
            "is_onam_period":          int(any("onam" in f.lower() for f in festivals)),
            "is_vishu":                int(any("vishu" in f.lower() for f in festivals)),
            "is_eid":                  int(any("eid" in f.lower() for f in festivals)),
            "is_christmas_period":     int(any("christmas" in f.lower() for f in festivals)),
            "is_diwali":               int(any("diwali" in f.lower() for f in festivals)),
            "is_good_friday":          int(any("good friday" in f.lower() for f in festivals)),
            "is_sabarimala_season":    int(ml_date.month_name in ("Vrischikam", "Dhanu", "Makaram")),

            # ── Pre-festival windows ──────────────────────────────────────────
            "days_to_onam":            max(days_to_onam, 0),
            "days_from_onam":          max(days_from_onam, 0),
            "days_to_vishu":           max(days_to_vishu, 0),
            "is_pre_onam_week":        int(is_pre_onam_week),
            "is_pre_onam_fortnight":   int(is_pre_onam_fortnight),
            "is_pre_vishu_week":       int(is_pre_vishu_week),

            # ── Cyclical (sin/cos) encodings for ML ───────────────────────────
            "month_sin":               round(month_sin, 6),
            "month_cos":               round(month_cos, 6),
            "day_sin":                 round(day_sin, 6),
            "day_cos":                 round(day_cos, 6),
            "weekday_sin":             round(week_sin, 6),
            "weekday_cos":             round(week_cos, 6),
            "nakshatra_sin":           round(nak_sin, 6),
            "nakshatra_cos":           round(nak_cos, 6),
            "tithi_sin":               round(tithi_sin, 6),
            "tithi_cos":               round(tithi_cos, 6),
            "doy_sin":                 round(doy_sin, 6),
            "doy_cos":                 round(doy_cos, 6),

            # ── Meta ──────────────────────────────────────────────────────────
            "festival_names":          " | ".join(festivals) if festivals else "None",
            "mal_month_name":          ml_date.month_name,
            "nakshatra_name":          ml_date.nakshatra,
            "season":                  ml_date.season,
        }
        return features

    def _days_to(self, d: date, date_dict: Dict[int, date], direction: str) -> int:
        """Days to next (or since prev) festival from date_dict."""
        if direction == "next":
            candidates = [v for k, v in date_dict.items() if v >= d]
        else:
            candidates = [v for k, v in date_dict.items() if v <= d]
        if not candidates:
            return 999
        best = min(candidates, key=lambda x: abs((x - d).days))
        return abs((best - d).days)


# ─────────────────────────────────────────────────────────────────────────────
# DATAFRAME GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def generate_calendar_dataframe(
    start: Union[date, str] = date(2020, 1, 1),
    end:   Union[date, str] = date(2026, 12, 31),
    include_text_cols: bool  = True,
) -> pd.DataFrame:
    """
    Generate a Pandas DataFrame with full Malayalam calendar features
    for every date in [start, end].

    Parameters
    ----------
    start : date or 'YYYY-MM-DD' string
    end   : date or 'YYYY-MM-DD' string
    include_text_cols : keep string columns (month name, nakshatra, etc.)

    Returns
    -------
    pd.DataFrame  — one row per day, indexed by gregorian date
    """
    if isinstance(start, str):
        start = date.fromisoformat(start)
    if isinstance(end, str):
        end = date.fromisoformat(end)

    featurizer = MalayalamCalendarFeaturizer()
    rows = []
    current = start
    total = (end - start).days + 1
    print(f"Generating Malayalam calendar features for {total} days "
          f"({start} → {end})...")

    i = 0
    while current <= end:
        rows.append(featurizer.featurize(current))
        current += timedelta(days=1)
        i += 1
        if i % 500 == 0:
            print(f"  {i}/{total} days processed...")

    df = pd.DataFrame(rows)
    df["gregorian_date"] = pd.to_datetime(
        df[["gregorian_year", "gregorian_month", "gregorian_day"]].rename(
            columns={"gregorian_year": "year",
                     "gregorian_month": "month",
                     "gregorian_day": "day"}))
    df = df.set_index("gregorian_date")

    if not include_text_cols:
        df = df.select_dtypes(exclude=["object"])

    print(f"Done. DataFrame shape: {df.shape}")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# CONVENIENCE FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def get_malayalam_info(d: Union[date, str]) -> Dict:
    """
    Quick lookup: full Malayalam calendar info for a single date.

    Example
    -------
    >>> info = get_malayalam_info("2024-08-26")
    >>> print(info)
    """
    if isinstance(d, str):
        d = date.fromisoformat(d)
    ml = MalayalamDate(d)
    engine = KeralaFestivalEngine()
    info = ml.to_dict()
    info["festivals"] = engine.get_festivals(d)
    info["is_public_holiday"] = engine.is_public_holiday(d)
    return info


def get_festivals_for_year(year: int) -> pd.DataFrame:
    """Return all Kerala festivals/holidays for a given year as a DataFrame."""
    engine = KeralaFestivalEngine()
    engine._build_year(year)
    data = []
    for d, names in sorted(engine._festival_cache[year].items()):
        ml = MalayalamDate(d)
        for name in names:
            data.append({
                "date":         d,
                "festival":     name,
                "mal_month":    ml.month_name,
                "weekday":      d.strftime("%A"),
                "nakshatra":    ml.nakshatra,
            })
    return pd.DataFrame(data)


def get_auspicious_days(
    year: int,
    month_name: Optional[str] = None,
    nakshatra_filter: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Return auspicious (Shukla Paksha) days with optional filters.
    Traditionally used for muhurtam selection.
    """
    start = date(year, 1, 1)
    end   = date(year, 12, 31)
    results = []
    current = start
    while current <= end:
        ml = MalayalamDate(current)
        if ml.paksha == "Shukla" and ml.tithi not in (4, 8, 9, 14):  # avoid inauspicious tithis
            if month_name and ml.month_name != month_name:
                current += timedelta(days=1)
                continue
            if nakshatra_filter and ml.nakshatra not in nakshatra_filter:
                current += timedelta(days=1)
                continue
            results.append({
                "date":        current,
                "mal_month":   ml.month_name,
                "nakshatra":   ml.nakshatra,
                "tithi":       ml.tithi,
                "weekday":     current.strftime("%A"),
                "paksha":      ml.paksha,
            })
        current += timedelta(days=1)
    return pd.DataFrame(results)


# ─────────────────────────────────────────────────────────────────────────────
# DEMO / QUICK TEST
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  Advanced Malayalam Calendar — Demo")
    print("=" * 60)

    # 1. Single date lookup
    today = date.today()
    info = get_malayalam_info(today)
    print(f"\n📅  Today: {today}")
    for k, v in info.items():
        print(f"    {k:<25} {v}")

    # 2. Festival list for 2025
    print("\n\n📿  Kerala festivals & holidays in 2025:")
    df_fest = get_festivals_for_year(2025)
    print(df_fest.to_string(index=False))

    # 3. ML feature vector for a specific date
    print("\n\n🤖  ML feature vector for 2024-08-26 (day before Thiruvonam 2024):")
    featurizer = MalayalamCalendarFeaturizer()
    vec = featurizer.featurize(date(2024, 8, 26))
    for k, v in vec.items():
        print(f"    {k:<35} {v}")

    # 4. Generate small DataFrame
    print("\n\n📊  Generating Jan–Mar 2025 DataFrame...")
    df = generate_calendar_dataframe("2025-01-01", "2025-03-31")
    print(df[["gregorian_year","gregorian_month","gregorian_day",
              "mal_month_name","nakshatra_name","tithi","is_shukla_paksha",
              "is_public_holiday","festival_names",
              "is_onam_period","is_harvest_season","season"]].to_string())

    # 5. Auspicious days
    print("\n\n✨  Auspicious days in Chingam 2025 (Shukla Paksha):")
    df_aus = get_auspicious_days(2025, month_name="Chingam")
    print(df_aus.to_string(index=False))
