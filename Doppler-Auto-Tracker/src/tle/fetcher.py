"""
TLE Fetcher — Downloads and caches Two-Line Elements from Celestrak / SpaceTrack
DynamiX Labs
"""

import requests
import yaml
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List

log = logging.getLogger("doppler.tle")

CELESTRAK_BASE = "https://celestrak.org/SOCRATES/query.php"
CELESTRAK_GROUPS = {
    "amateur":   "https://celestrak.org/SOCRATES/query.php",
    "weather":   "https://celestrak.org/TLE/query.php?GROUP=weather&FORMAT=tle",
    "stations":  "https://celestrak.org/TLE/query.php?GROUP=stations&FORMAT=tle",
    "gnss":      "https://celestrak.org/TLE/query.php?GROUP=gnss&FORMAT=tle",
    "visual":    "https://celestrak.org/TLE/query.php?GROUP=visual&FORMAT=tle",
}

CELESTRAK_TLE_URLS = {
    "amateur":  "https://celestrak.org/TLE/query.php?GROUP=amateur&FORMAT=tle",
    "weather":  "https://celestrak.org/TLE/query.php?GROUP=weather&FORMAT=tle",
    "stations": "https://celestrak.org/TLE/query.php?GROUP=stations&FORMAT=tle",
}


class TLEFetcher:
    """
    Downloads, parses, and caches TLE data.

    Usage:
        fetcher = TLEFetcher(cache_dir="./tle_cache")
        tles = fetcher.fetch_group("weather")
        tle = fetcher.get_satellite("NOAA 19")
        print(tle["line1"], tle["line2"])
    """

    def __init__(self, cache_dir: str = "./tle_cache", max_age_hours: int = 12):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_age = timedelta(hours=max_age_hours)
        self._catalog: Dict[str, dict] = {}

    def _cache_path(self, group: str) -> Path:
        return self.cache_dir / f"{group}.json"

    def _is_cache_valid(self, group: str) -> bool:
        p = self._cache_path(group)
        if not p.exists():
            return False
        mtime = datetime.fromtimestamp(p.stat().st_mtime)
        return (datetime.now() - mtime) < self.max_age

    def _parse_tle_text(self, text: str) -> List[dict]:
        """Parse TLE text format (3-line sets) → list of dicts."""
        entries = []
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        i = 0
        while i + 2 < len(lines):
            name = lines[i]
            line1 = lines[i + 1]
            line2 = lines[i + 2]
            if line1.startswith("1 ") and line2.startswith("2 "):
                norad = int(line1[2:7].strip())
                entries.append({
                    "name": name,
                    "norad": norad,
                    "line1": line1,
                    "line2": line2,
                    "epoch": line1[18:32].strip(),
                })
                i += 3
            else:
                i += 1
        return entries

    def fetch_group(self, group: str = "amateur") -> List[dict]:
        """Fetch TLE group (cached or fresh)."""
        if self._is_cache_valid(group):
            log.info(f"Using cached TLEs for group: {group}")
            with open(self._cache_path(group)) as f:
                entries = json.load(f)
        else:
            url = CELESTRAK_TLE_URLS.get(group)
            if not url:
                raise ValueError(f"Unknown group: {group}. Options: {list(CELESTRAK_TLE_URLS)}")
            log.info(f"Fetching TLEs from Celestrak: {group}")
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            entries = self._parse_tle_text(resp.text)
            with open(self._cache_path(group), "w") as f:
                json.dump(entries, f, indent=2)
            log.info(f"Fetched {len(entries)} TLEs for {group}")

        # Index by name
        for e in entries:
            self._catalog[e["name"].upper()] = e
        return entries

    def get_satellite(self, name: str) -> Optional[dict]:
        """Get TLE for a satellite by name (case-insensitive)."""
        key = name.upper().strip()
        if key in self._catalog:
            return self._catalog[key]
        # Try prefix match
        for k, v in self._catalog.items():
            if key in k:
                return v
        return None

    def get_by_norad(self, norad: int) -> Optional[dict]:
        """Get TLE by NORAD catalog number."""
        for v in self._catalog.values():
            if v.get("norad") == norad:
                return v
        return None

    def list_satellites(self, filter_str: str = "") -> List[str]:
        """List all loaded satellite names, optionally filtered."""
        names = sorted(self._catalog.keys())
        if filter_str:
            names = [n for n in names if filter_str.upper() in n]
        return names
