"""Real-time Doppler shift calculator for SDR frequency correction."""

import math
from datetime import datetime, timezone
from typing import Tuple
import logging

log = logging.getLogger("doppler.tracker")

C = 299_792_458.0  # Speed of light
EARTH_RADIUS_M = 6_378_137.0
EARTH_FLATTENING = 1 / 298.257223563


def geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_m: float) -> Tuple[float, float, float]:
    """Convert geodetic coordinates to ECEF frame."""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)

    f = EARTH_FLATTENING
    e2 = 2 * f - f * f
    N = EARTH_RADIUS_M / math.sqrt(1 - e2 * math.sin(lat) ** 2)

    x = (N + alt_m) * math.cos(lat) * math.cos(lon)
    y = (N + alt_m) * math.cos(lat) * math.sin(lon)
    z = (N * (1 - e2) + alt_m) * math.sin(lat)
    return x, y, z


class DopplerCalculator:
    """Real-time Doppler correction for satellite tracking."""

    def __init__(self, tle_line1: str, tle_line2: str,
                 observer_lat: float, observer_lon: float,
                 observer_alt: float = 0.0):
        try:
            from sgp4.api import Satrec
            self.satellite = Satrec.twoline2rv(tle_line1, tle_line2)
        except ImportError:
            log.error("sgp4 not installed — run: pip install sgp4")
            self.satellite = None

        self.obs_lat = observer_lat
        self.obs_lon = observer_lon
        self.obs_alt = observer_alt
        self.obs_ecef = geodetic_to_ecef(observer_lat, observer_lon, observer_alt)

    def get_satellite_state(self, dt: datetime = None) -> Tuple[Tuple, Tuple]:
        """Get satellite position and velocity at given time."""
        if self.satellite is None:
            return (0, 0, 7000), (0, 7.8, 0)

        if dt is None:
            dt = datetime.now(timezone.utc)

        from sgp4.api import jday
        jd, fr = jday(dt.year, dt.month, dt.day,
                      dt.hour, dt.minute, dt.second + dt.microsecond / 1e6)

        e, r, v = self.satellite.sgp4(jd, fr)
        if e != 0:
            raise ValueError(f"SGP4 propagation failed (error code {e})")
        return tuple(r), tuple(v)

    def range_rate(self, dt: datetime = None) -> float:
        """Compute radial velocity (range rate) in m/s."""
        pos_km, vel_km_s = self.get_satellite_state(dt)

        sat_pos = tuple(x * 1000 for x in pos_km)
        sat_vel = tuple(v * 1000 for v in vel_km_s)
        obs_pos = self.obs_ecef

        rx = sat_pos[0] - obs_pos[0]
        ry = sat_pos[1] - obs_pos[1]
        rz = sat_pos[2] - obs_pos[2]
        range_m = math.sqrt(rx * rx + ry * ry + rz * rz)

        if range_m == 0:
            return 0.0

        ux, uy, uz = rx / range_m, ry / range_m, rz / range_m
        rr = sat_vel[0] * ux + sat_vel[1] * uy + sat_vel[2] * uz
        return rr

    def doppler_shift(self, nominal_freq: float, dt: datetime = None) -> float:
        """Calculate Doppler shift in Hz."""
        rr = self.range_rate(dt)
        shift = -nominal_freq * rr / C
        return shift

    def corrected_frequency(self, nominal_freq: float, dt: datetime = None) -> float:
        """Calculate frequency adjusted for Doppler shift."""
        shift = self.doppler_shift(nominal_freq, dt)
        corrected = nominal_freq + shift
        log.debug(
            f"Doppler: nominal={nominal_freq/1e6:.4f}MHz "
            f"shift={shift/1e3:+.2f}kHz "
            f"corrected={corrected/1e6:.4f}MHz"
        )
        return corrected

    def get_azel(self, dt: datetime = None) -> Tuple[float, float, float]:
        """Get azimuth, elevation, and range for rotator tracking."""
        pos_km, _ = self.get_satellite_state(dt)
        sat_ecef = tuple(x * 1000 for x in pos_km)
        obs = self.obs_ecef

        dx = sat_ecef[0] - obs[0]
        dy = sat_ecef[1] - obs[1]
        dz = sat_ecef[2] - obs[2]

        lat = math.radians(self.obs_lat)
        lon = math.radians(self.obs_lon)

        s = (math.sin(lat) * math.cos(lon) * dx +
             math.sin(lat) * math.sin(lon) * dy - math.cos(lat) * dz)
        e = (-math.sin(lon) * dx + math.cos(lon) * dy)
        z = (math.cos(lat) * math.cos(lon) * dx +
             math.cos(lat) * math.sin(lon) * dy + math.sin(lat) * dz)

        range_m = math.sqrt(s * s + e * e + z * z)
        el = math.degrees(math.asin(z / range_m)) if range_m > 0 else 0
        az = math.degrees(math.atan2(-e, s)) % 360

        return az, el, range_m / 1000
