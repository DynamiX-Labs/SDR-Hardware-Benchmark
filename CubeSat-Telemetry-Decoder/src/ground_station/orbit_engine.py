"""
Orbit Engine
Calculates real-time satellite positions and Doppler shifts using SGP4.
Includes a low-pass filter for closed-loop Doppler smoothing.
"""

import datetime
import logging
import math
from typing import Optional, Tuple

from sgp4.api import WGS84, Satrec, jday

log = logging.getLogger("cubesat.orbit")


class OrbitEngine:
    SPEED_OF_LIGHT = 299792458.0  # m/s
    EARTH_RADIUS_M = 6378137.0
    EARTH_FLATTENING = 1 / 298.257223563
    EARTH_ROTATION_RAD_S = 7.2921150e-5

    def __init__(self, lat: float, lon: float, alt_m: float = 0.0, alpha: float = 0.3):
        """
        Initialize the orbit engine with ground station coordinates.
        alpha: Smoothing factor for the Doppler EMA filter (0 < alpha <= 1).
               Lower values provide more smoothing, higher values react faster.
        """
        self.gs_lat = lat
        self.gs_lon = lon
        self.gs_alt = alt_m
        self.satrec = None

        # Doppler Control Law (EMA)
        self.alpha = alpha
        self.last_doppler_freq = None

    def load_tle(self, line1: str, line2: str) -> bool:
        """Load satellite TLE data."""
        try:
            self.satrec = Satrec.twoline2rv(line1, line2)
            log.info("TLE loaded successfully.")
            # Reset the filter on new TLE
            self.last_doppler_freq = None
            return True
        except Exception as e:
            log.error(f"Failed to load TLE: {e}")
            return False

    def get_smoothed_doppler(
        self, dt: datetime.datetime, center_freq_hz: float
    ) -> Optional[float]:
        """
        Calculate the expected Doppler shifted frequency and apply an EMA
        low-pass filter to prevent SDR tuning oscillation and overshoot.
        """
        if not self.satrec:
            log.error("No TLE loaded.")
            return None

        jd, fr = jday(
            dt.year,
            dt.month,
            dt.day,
            dt.hour,
            dt.minute,
            dt.second + dt.microsecond / 1e6,
        )

        e, r, v = self.satrec.sgp4(jd, fr)

        if e != 0:
            log.error(f"SGP4 propagation error: code {e}")
            return None

        # Greenwich Mean Sidereal Time (GMST) angle in radians
        tut1 = (jd + fr - 2451545.0) / 36525.0
        gmst_sec = (
            67310.54841
            + (876600.0 * 3600.0 + 8640184.812866) * tut1
            + 0.093104 * tut1**2
            - 6.2e-6 * tut1**3
        )
        theta = (gmst_sec * (2 * math.pi / 86400.0)) % (2 * math.pi)

        # Ground station in ECEF
        lat = math.radians(self.gs_lat)
        lon = math.radians(self.gs_lon)
        f = self.EARTH_FLATTENING
        e2 = 2 * f - f * f
        N = self.EARTH_RADIUS_M / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        obs_ecef_x = (N + self.gs_alt) * math.cos(lat) * math.cos(lon)
        obs_ecef_y = (N + self.gs_alt) * math.cos(lat) * math.sin(lon)
        obs_ecef_z = (N * (1 - e2) + self.gs_alt) * math.sin(lat)

        # Rotate observer position into TEME inertial frame
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        obs_teme_x = obs_ecef_x * cos_t - obs_ecef_y * sin_t
        obs_teme_y = obs_ecef_x * sin_t + obs_ecef_y * cos_t
        obs_teme_z = obs_ecef_z

        # Observer velocity in TEME due to Earth's rotation
        obs_vx = -self.EARTH_ROTATION_RAD_S * obs_teme_y
        obs_vy = self.EARTH_ROTATION_RAD_S * obs_teme_x
        obs_vz = 0.0

        # Satellite state in TEME (r in km -> m, v in km/s -> m/s)
        sat_x, sat_y, sat_z = r[0] * 1000.0, r[1] * 1000.0, r[2] * 1000.0
        sat_vx, sat_vy, sat_vz = v[0] * 1000.0, v[1] * 1000.0, v[2] * 1000.0

        dx = sat_x - obs_teme_x
        dy = sat_y - obs_teme_y
        dz = sat_z - obs_teme_z
        range_m = math.sqrt(dx * dx + dy * dy + dz * dz)

        if range_m > 0:
            ux, uy, uz = dx / range_m, dy / range_m, dz / range_m
            v_rel_m_s = (
                (sat_vx - obs_vx) * ux
                + (sat_vy - obs_vy) * uy
                + (sat_vz - obs_vz) * uz
            )
        else:
            v_rel_m_s = 0.0

        # Raw target Doppler calculation
        doppler_shift = center_freq_hz * (-v_rel_m_s / self.SPEED_OF_LIGHT)
        raw_target_freq = center_freq_hz + doppler_shift

        # Apply Control Law (EMA smoothing)
        if self.last_doppler_freq is None:
            self.last_doppler_freq = raw_target_freq
        else:
            self.last_doppler_freq = self.last_doppler_freq + self.alpha * (
                raw_target_freq - self.last_doppler_freq
            )

        return self.last_doppler_freq
