"""
Satellite Pass Predictor
Computes AOS/LOS/Max-Elevation for upcoming satellite passes.
DynamiX Labs
"""

import math
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
import logging

log = logging.getLogger("doppler.predict")

MIN_ELEVATION = 5.0  # degrees — minimum for a "visible" pass


def _azel_from_tle(tle_line1: str, tle_line2: str,
                   obs_lat: float, obs_lon: float, obs_alt: float,
                   dt: datetime):
    """Compute Az/El/Range for satellite at given time. Returns (az, el, range_km)."""
    from .doppler import DopplerCalculator
    calc = DopplerCalculator(tle_line1, tle_line2, obs_lat, obs_lon, obs_alt)
    return calc.get_azel(dt)


class PassPredictor:
    """
    Predicts satellite passes visible from a ground station.

    Usage:
        predictor = PassPredictor(
            obs_lat=13.0827, obs_lon=80.2707, obs_alt=6.0,
            min_elevation=10.0
        )
        passes = predictor.predict(tle, hours_ahead=24)
        for p in passes:
            print(f"AOS: {p['aos']}  Max El: {p['max_el']:.1f}°  LOS: {p['los']}")
    """

    def __init__(self, obs_lat: float, obs_lon: float, obs_alt: float = 0.0,
                 min_elevation: float = MIN_ELEVATION):
        self.obs_lat = obs_lat
        self.obs_lon = obs_lon
        self.obs_alt = obs_alt
        self.min_elevation = min_elevation

    def predict(self, tle: dict, hours_ahead: float = 24.0,
                time_step_s: float = 10.0) -> List[Dict]:
        """
        Find all passes within the next `hours_ahead` hours.

        Args:
            tle: dict with name, line1, line2
            hours_ahead: How far ahead to predict
            time_step_s: Time resolution (seconds)

        Returns:
            List of pass dicts with aos, los, max_el, max_el_time, duration_s
        """
        passes = []
        now = datetime.now(timezone.utc)
        end = now + timedelta(hours=hours_ahead)

        dt = now
        step = timedelta(seconds=time_step_s)

        in_pass = False
        current_pass = {}
        max_el = 0.0

        while dt < end:
            try:
                az, el, rng = _azel_from_tle(
                    tle["line1"], tle["line2"],
                    self.obs_lat, self.obs_lon, self.obs_alt, dt
                )
            except Exception:
                dt += step
                continue

            if el >= self.min_elevation:
                if not in_pass:
                    # AOS (Acquisition of Signal)
                    in_pass = True
                    current_pass = {
                        "satellite": tle["name"],
                        "norad": tle.get("norad"),
                        "aos": dt,
                        "max_el": el,
                        "max_el_time": dt,
                        "max_el_az": az,
                    }
                    max_el = el
                else:
                    if el > max_el:
                        max_el = el
                        current_pass["max_el"] = el
                        current_pass["max_el_time"] = dt
                        current_pass["max_el_az"] = az
            else:
                if in_pass:
                    # LOS (Loss of Signal)
                    in_pass = False
                    current_pass["los"] = dt
                    duration = (dt - current_pass["aos"]).total_seconds()
                    current_pass["duration_s"] = int(duration)
                    current_pass["quality"] = "★" if current_pass["max_el"] >= 50 else ""
                    passes.append(current_pass)

            dt += step

        return passes

    def print_table(self, passes: List[Dict], station_name: str = ""):
        """Pretty-print pass prediction table."""
        if not passes:
            print("No passes found.")
            return

        name = passes[0].get("satellite", "Unknown")
        print(f"\nGround Station: {station_name} ({self.obs_lat:.4f}°N, {self.obs_lon:.4f}°E)")
        print(f"Satellite: {name}\n")
        print(f"{'#':>3}  {'AOS (UTC)':<22}  {'LOS (UTC)':<22}  {'Max El':>7}  {'Duration':>10}")
        print("─" * 75)

        for i, p in enumerate(passes, 1):
            aos = p["aos"].strftime("%Y-%m-%d %H:%M:%S")
            los = p["los"].strftime("%Y-%m-%d %H:%M:%S")
            dur_m = p["duration_s"] // 60
            dur_s = p["duration_s"] % 60
            quality = p.get("quality", "")
            print(f"{i:>3}  {aos:<22}  {los:<22}  {p['max_el']:>6.1f}°  "
                  f"{dur_m}m {dur_s:02d}s {quality}")
        print()
