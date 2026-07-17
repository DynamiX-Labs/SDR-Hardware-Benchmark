import os
import urllib.request
import logging
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger("doppler.tle")

CELESTRAK_BASE = "https://celestrak.org/NORAD/elements/gp.php?GROUP={}&FORMAT=tle"

class TLEFetcher:
    """
    Fetches and caches TLE (Two-Line Element) data from multiple sources.
    Features age validation and offline fallback.
    """
    
    def __init__(self, cache_dir: str = "cache/tle", cache_ttl_hours: float = 24.0):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl = timedelta(hours=cache_ttl_hours)
        self._satellites: Dict[str, Dict] = {}

    def fetch_group(self, group_name: str) -> bool:
        """Fetch a group of TLEs (e.g., 'amateur', 'weather', 'stations')."""
        cache_file = self.cache_dir / f"{group_name}.txt"
        
        # Check cache freshness
        if cache_file.exists():
            mtime = datetime.fromtimestamp(cache_file.stat().st_mtime, tz=timezone.utc)
            age = datetime.now(timezone.utc) - mtime
            if age < self.cache_ttl:
                log.debug(f"Using cached TLE for {group_name} (age: {age.total_seconds()/3600:.1f}h)")
                with open(cache_file, "r") as f:
                    self._parse_tle_data(f.read())
                return True

        url = CELESTRAK_BASE.format(group_name)
        log.info(f"Downloading TLE group: {group_name}")
        
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'DynamiX-Labs-Tracker/2.0'})
            with urllib.request.urlopen(req, timeout=10.0) as response:
                data = response.read().decode('utf-8')
                
                if not data.strip():
                    raise ValueError(f"Empty response from {url}")
                    
                # Cache the new data
                with open(cache_file, "w") as f:
                    f.write(data)
                    
                self._parse_tle_data(data)
                return True
        except Exception as e:
            log.warning(f"Failed to fetch {group_name}: {e}")
            # Offline fallback
            if cache_file.exists():
                log.info(f"Falling back to stale cache for {group_name}")
                try:
                    with open(cache_file, "r") as f:
                        self._parse_tle_data(f.read())
                    return True
                except Exception as ce:
                    log.error(f"Failed to read cache: {ce}")
            return False

    def load_file(self, filepath: str) -> bool:
        """Load TLEs from a local file."""
        path = Path(filepath)
        if not path.exists():
            log.error(f"File not found: {filepath}")
            return False
            
        try:
            with open(path, "r") as f:
                self._parse_tle_data(f.read())
            log.info(f"Loaded TLEs from {filepath}")
            return True
        except Exception as e:
            log.error(f"Error loading {filepath}: {e}")
            return False

    def _parse_tle_data(self, data: str):
        lines = [l.strip() for l in data.split('\n') if l.strip()]
        for i in range(0, len(lines)-2, 3):
            name = lines[i]
            line1 = lines[i+1]
            line2 = lines[i+2]
            
            # Basic validation
            if len(line1) >= 68 and len(line2) >= 68 and line1.startswith("1 ") and line2.startswith("2 "):
                try:
                    norad_id = int(line1[2:7].strip())
                    self._satellites[name.upper()] = {
                        "name": name,
                        "line1": line1,
                        "line2": line2,
                        "norad": norad_id
                    }
                except ValueError:
                    log.debug(f"Invalid NORAD ID in TLE for {name}")

    def get_satellite(self, name: str) -> Optional[Dict]:
        """Get TLE for a specific satellite by name (case-insensitive)."""
        return self._satellites.get(name.upper())

    def get_by_norad(self, norad_id: int) -> Optional[Dict]:
        """Get TLE for a specific satellite by NORAD catalog ID."""
        for sat in self._satellites.values():
            if sat.get("norad") == norad_id:
                return sat
        return None
