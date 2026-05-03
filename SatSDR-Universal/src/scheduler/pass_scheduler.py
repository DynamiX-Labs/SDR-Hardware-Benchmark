"""
Automated Pass Scheduler
Predicts satellite passes using SGP4/Skyfield and queues decoder jobs.
"""
import logging
import time
from typing import List, Dict, Optional
from datetime import datetime, timedelta, timezone

log = logging.getLogger("satsdr.scheduler")


class PassScheduler:
    """
    Predicts satellite passes over a ground station and automatically
    triggers decoder jobs at Acquisition of Signal (AOS).
    """

    def __init__(self, lat: float, lon: float, alt_m: float = 0.0):
        self.lat = lat
        self.lon = lon
        self.alt_m = alt_m
        self.tle_cache: Dict[str, tuple] = {}
        self.pass_queue: List[Dict] = []

    def load_tle_from_celestrak(self, category: str = "active") -> int:
        """
        Fetch TLE data from CelesTrak.
        Returns the number of satellites loaded.
        """
        import urllib.request
        url = f"https://celestrak.org/NORAD/elements/gp.php?GROUP={category}&FORMAT=tle"
        try:
            response = urllib.request.urlopen(url, timeout=10)
            lines = response.read().decode().strip().split('\n')
            count = 0
            for i in range(0, len(lines) - 2, 3):
                name = lines[i].strip()
                line1 = lines[i + 1].strip()
                line2 = lines[i + 2].strip()
                self.tle_cache[name] = (line1, line2)
                count += 1
            log.info(f"Loaded {count} TLEs from CelesTrak ({category})")
            return count
        except Exception as e:
            log.error(f"CelesTrak fetch failed: {e}")
            return 0

    def predict_passes(self, satellite_name: str, hours_ahead: float = 24.0,
                       min_elevation_deg: float = 10.0) -> List[Dict]:
        """
        Predict visible passes for a satellite over the ground station.
        Returns list of pass events with AOS, TCA, LOS times and max elevation.
        """
        if satellite_name not in self.tle_cache:
            log.warning(f"No TLE for '{satellite_name}'")
            return []

        try:
            from skyfield.api import load, wgs84, EarthSatellite
        except ImportError:
            log.error("skyfield not installed. Cannot predict passes.")
            return []

        ts = load.timescale()
        line1, line2 = self.tle_cache[satellite_name]
        sat = EarthSatellite(line1, line2, satellite_name, ts)
        station = wgs84.latlon(self.lat, self.lon, self.alt_m)

        now = ts.now()
        end = ts.from_datetime(datetime.now(timezone.utc) + timedelta(hours=hours_ahead))

        t_events, events = sat.find_events(station, now, end, altitude_degrees=min_elevation_deg)

        passes = []
        current_pass = {}
        for ti, event in zip(t_events, events):
            if event == 0:  # AOS
                current_pass = {"satellite": satellite_name, "aos": ti.utc_datetime().isoformat()}
            elif event == 1:  # TCA (max elevation)
                alt, _, _ = (sat - station).at(ti).altaz()
                current_pass["tca"] = ti.utc_datetime().isoformat()
                current_pass["max_elevation_deg"] = round(alt.degrees, 1)
            elif event == 2:  # LOS
                current_pass["los"] = ti.utc_datetime().isoformat()
                if "aos" in current_pass:
                    passes.append(current_pass)
                current_pass = {}

        log.info(f"Predicted {len(passes)} passes for {satellite_name} in next {hours_ahead}h")
        return passes

    def queue_pass(self, pass_event: Dict, decoder: str, frequency: float):
        """Add a pass to the automated decode queue."""
        job = {
            **pass_event,
            "decoder": decoder,
            "frequency": frequency,
            "status": "queued"
        }
        self.pass_queue.append(job)
        self.pass_queue.sort(key=lambda x: x.get("aos", ""))
        log.info(f"Queued: {pass_event['satellite']} @ {pass_event.get('aos', 'unknown')}")

    def get_next_pass(self) -> Optional[Dict]:
        """Return the next queued pass."""
        for job in self.pass_queue:
            if job["status"] == "queued":
                return job
        return None
