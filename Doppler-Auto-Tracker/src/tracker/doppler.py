"""
Real-Time Doppler Shift Calculator
Uses SGP4 propagation for accurate range-rate computation.
DynamiX Labs
"""

import math
from datetime import datetime, timezone
from typing import Tuple
import logging

log = logging.getLogger("doppler.tracker")

# Speed of light
C = 299_792_458.0  # m/s

# Earth parameters (WGS84)
EARTH_RADIUS_M = 6_378_137.0
EARTH_FLATTENING = 1 / 298.257223563


def geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_m: float) -> Tuple[float, float, float]:
    """Convert geodetic coordinates to ECEF (Earth-Centered Earth-Fixed)."""
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
    """
    Calculates real-time Doppler shift for a satellite pass.

    Uses SGP4 TLE propagation to compute satellite position and velocity,
    then projects the velocity onto the observer–satellite range vector
    to get the radial velocity (range-rate).

    Usage:
        calc = DopplerCalculator(
            tle_line1="1 33591U ...",
            tle_line2="2 33591 ...",
            observer_lat=13.0827,
            observer_lon=80.2707,
            observer_alt=6.0
        )
        f_corrected = calc.corrected_frequency(137.1e6)
    """

    def __init__(self, tle_line1: str, tle_line2: str,
                 observer_lat: float, observer_lon: float,
                 observer_alt: float = 0.0):
        try:
            from sgp4.api import Satrec
            self.satellite = Satrec.twoline2rv(tle_line1, tle_line2)
        except ImportError:
            log.error("sgp4 not installed: pip install sgp4")
            self.satellite = None

        self.obs_lat = observer_lat
        self.obs_lon = observer_lon
        self.obs_alt = observer_alt
        self.obs_ecef = geodetic_to_ecef(observer_lat, observer_lon, observer_alt)

    def get_satellite_state(self, dt: datetime = None) -> Tuple[Tuple, Tuple]:
        """
        Get satellite ECEF position and velocity at given time.

        Returns:
            (position_km_xyz, velocity_km_s_xyz) tuples
        """
        if self.satellite is None:
            return (0, 0, 7000), (0, 7.8, 0)  # Mock LEO orbit

        if dt is None:
            dt = datetime.now(timezone.utc)

        from sgp4.api import jday
        jd, fr = jday(dt.year, dt.month, dt.day,
                      dt.hour, dt.minute, dt.second + dt.microsecond / 1e6)
        e, r, v = self.satellite.sgp4(jd, fr)
        if e != 0:
            raise ValueError(f"SGP4 propagation error code: {e}")
        return tuple(r), tuple(v)

    def range_rate(self, dt: datetime = None) -> float:
        """
        Compute range-rate (radial velocity) from observer to satellite.

        Positive = satellite moving away (receding) → negative Doppler
        Negative = satellite moving toward observer → positive Doppler

        Returns:
            range_rate in m/s
        """
        pos_km, vel_km_s = self.get_satellite_state(dt)

        # Convert to meters
        sat_pos = tuple(x * 1000 for x in pos_km)
        sat_vel = tuple(v * 1000 for v in vel_km_s)
        obs_pos = self.obs_ecef

        # Range vector: observer → satellite
        rx = sat_pos[0] - obs_pos[0]
        ry = sat_pos[1] - obs_pos[1]
        rz = sat_pos[2] - obs_pos[2]
        range_m = math.sqrt(rx * rx + ry * ry + rz * rz)

        if range_m == 0:
            return 0.0

        # Unit range vector
        ux, uy, uz = rx / range_m, ry / range_m, rz / range_m

        # Radial velocity = dot product of velocity with unit range vector
        rr = sat_vel[0] * ux + sat_vel[1] * uy + sat_vel[2] * uz
        return rr

    def doppler_shift(self, nominal_freq: float, dt: datetime = None) -> float:
        """
        Compute Doppler shift in Hz.

        Args:
            nominal_freq: Satellite transmit frequency (Hz)
            dt: Time (default: now)

        Returns:
            Doppler shift in Hz (positive = blueshift/approaching)
        """
        rr = self.range_rate(dt)
        shift = -nominal_freq * rr / C
        return shift

    def corrected_frequency(self, nominal_freq: float, dt: datetime = None) -> float:
        """
        Return the corrected receive frequency accounting for Doppler.

        Args:
            nominal_freq: Satellite's transmit frequency (Hz)

        Returns:
            Corrected tuning frequency (Hz)
        """
        shift = self.doppler_shift(nominal_freq, dt)
        corrected = nominal_freq + shift
        log.debug(
            f"Doppler: nominal={nominal_freq/1e6:.4f}MHz "
            f"shift={shift/1e3:+.2f}kHz "
            f"corrected={corrected/1e6:.4f}MHz"
        )
        return corrected

    def get_azel(self, dt: datetime = None) -> Tuple[float, float, float]:
        """
        Compute Azimuth, Elevation, Range from observer to satellite.

        Returns:
            (azimuth_deg, elevation_deg, range_km)
        """
        pos_km, _ = self.get_satellite_state(dt)
        sat_ecef = tuple(x * 1000 for x in pos_km)
        obs = self.obs_ecef

        dx = sat_ecef[0] - obs[0]
        dy = sat_ecef[1] - obs[1]
        dz = sat_ecef[2] - obs[2]

        lat = math.radians(self.obs_lat)
        lon = math.radians(self.obs_lon)

        # Rotate to topocentric SEZ frame
        s = (math.sin(lat) * math.cos(lon) * dx +
             math.sin(lat) * math.sin(lon) * dy - math.cos(lat) * dz)
        e = (-math.sin(lon) * dx + math.cos(lon) * dy)
        z = (math.cos(lat) * math.cos(lon) * dx +
             math.cos(lat) * math.sin(lon) * dy + math.sin(lat) * dz)

        range_m = math.sqrt(s * s + e * e + z * z)
        el = math.degrees(math.asin(z / range_m)) if range_m > 0 else 0
        az = math.degrees(math.atan2(-e, s)) % 360

        return az, el, range_m / 1000
