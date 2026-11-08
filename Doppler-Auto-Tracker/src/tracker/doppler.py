import logging
import math
from datetime import datetime, timezone
from typing import Optional, Tuple

log = logging.getLogger("doppler.tracker")

C = 299_792_458.0  # Speed of light (m/s)
EARTH_RADIUS_M = 6_378_137.0
EARTH_FLATTENING = 1 / 298.257223563
EARTH_ROTATION_RAD_S = 7.2921150e-5


def geodetic_to_ecef(
    lat_deg: float, lon_deg: float, alt_m: float
) -> Tuple[float, float, float]:
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


def _compute_gmst_rad(jd: float, fr: float) -> float:
    """Compute Greenwich Mean Sidereal Time (GMST) in radians."""
    tut1 = (jd + fr - 2451545.0) / 36525.0
    gmst_sec = (
        67310.54841
        + (876600.0 * 3600.0 + 8640184.812866) * tut1
        + 0.093104 * tut1**2
        - 6.2e-6 * tut1**3
    )
    return (gmst_sec * (2 * math.pi / 86400.0)) % (2 * math.pi)


class DopplerCalculator:

    def __init__(
        self,
        tle_line1: str,
        tle_line2: str,
        observer_lat: float,
        observer_lon: float,
        observer_alt: float = 0.0,
    ):
        try:
            from sgp4.api import Satrec

            self.satellite = Satrec.twoline2rv(tle_line1, tle_line2)
        except ImportError:
            log.error("sgp4 not installed — run: pip install sgp4")
            raise ImportError("sgp4 is required: pip install sgp4")

        self.obs_lat = observer_lat
        self.obs_lon = observer_lon
        self.obs_alt = observer_alt
        self.obs_ecef = geodetic_to_ecef(observer_lat, observer_lon, observer_alt)

    def get_satellite_state(
        self, dt: Optional[datetime] = None
    ) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], float, float]:
        """
        Propagate satellite using SGP4.
        Returns ((pos_km), (vel_km_s), jd, fr) in TEME inertial frame.
        """
        if dt is None:
            dt = datetime.now(timezone.utc)

        from sgp4.api import jday

        jd, fr = jday(
            dt.year,
            dt.month,
            dt.day,
            dt.hour,
            dt.minute,
            dt.second + dt.microsecond / 1e6,
        )

        e, r, v = self.satellite.sgp4(jd, fr)
        if e != 0:
            raise ValueError(f"SGP4 propagation failed (error code {e})")
        return tuple(r), tuple(v), jd, fr

    def range_rate(self, dt: Optional[datetime] = None) -> float:
        """
        Calculate relative range rate (m/s) between satellite and observer.
        Positive = receding, negative = approaching.
        """
        pos_km, vel_km_s, jd, fr = self.get_satellite_state(dt)

        sat_pos = tuple(x * 1000.0 for x in pos_km)
        sat_vel = tuple(v * 1000.0 for v in vel_km_s)
        obs_pos = self.obs_ecef

        theta = _compute_gmst_rad(jd, fr)
        cos_t, sin_t = math.cos(theta), math.sin(theta)

        # Rotate observer ECEF position to TEME inertial frame
        obs_teme_x = obs_pos[0] * cos_t - obs_pos[1] * sin_t
        obs_teme_y = obs_pos[0] * sin_t + obs_pos[1] * cos_t
        obs_teme_z = obs_pos[2]

        # Observer velocity in TEME due to Earth's rotation
        obs_vx = -EARTH_ROTATION_RAD_S * obs_teme_y
        obs_vy = EARTH_ROTATION_RAD_S * obs_teme_x
        obs_vz = 0.0

        rx = sat_pos[0] - obs_teme_x
        ry = sat_pos[1] - obs_teme_y
        rz = sat_pos[2] - obs_teme_z
        range_m = math.sqrt(rx * rx + ry * ry + rz * rz)

        if range_m == 0:
            return 0.0

        ux, uy, uz = rx / range_m, ry / range_m, rz / range_m

        rel_vx = sat_vel[0] - obs_vx
        rel_vy = sat_vel[1] - obs_vy
        rel_vz = sat_vel[2] - obs_vz

        rr = rel_vx * ux + rel_vy * uy + rel_vz * uz
        return rr

    def doppler_shift(
        self, nominal_freq: float, dt: Optional[datetime] = None
    ) -> float:
        rr = self.range_rate(dt)
        shift = -nominal_freq * rr / C
        return shift

    def corrected_frequency(
        self, nominal_freq: float, dt: Optional[datetime] = None
    ) -> float:
        shift = self.doppler_shift(nominal_freq, dt)
        corrected = nominal_freq + shift
        log.debug(
            f"Doppler: nominal={nominal_freq/1e6:.4f}MHz "
            f"shift={shift/1e3:+.2f}kHz "
            f"corrected={corrected/1e6:.4f}MHz"
        )
        return corrected

    def get_azel(self, dt: Optional[datetime] = None) -> Tuple[float, float, float]:
        """
        Compute azimuth (0-360 deg), elevation (-90 to +90 deg), and slant range (km).
        """
        pos_km, _, jd, fr = self.get_satellite_state(dt)
        sat_teme = tuple(x * 1000.0 for x in pos_km)
        obs = self.obs_ecef

        theta = _compute_gmst_rad(jd, fr)
        cos_t, sin_t = math.cos(theta), math.sin(theta)

        # Rotate satellite from TEME to ECEF frame
        sat_ecef_x = sat_teme[0] * cos_t + sat_teme[1] * sin_t
        sat_ecef_y = -sat_teme[0] * sin_t + sat_teme[1] * cos_t
        sat_ecef_z = sat_teme[2]

        dx = sat_ecef_x - obs[0]
        dy = sat_ecef_y - obs[1]
        dz = sat_ecef_z - obs[2]

        lat = math.radians(self.obs_lat)
        lon = math.radians(self.obs_lon)

        # Topocentric SEZ frame
        s = (
            math.sin(lat) * math.cos(lon) * dx
            + math.sin(lat) * math.sin(lon) * dy
            - math.cos(lat) * dz
        )
        e = -math.sin(lon) * dx + math.cos(lon) * dy
        z = (
            math.cos(lat) * math.cos(lon) * dx
            + math.cos(lat) * math.sin(lon) * dy
            + math.sin(lat) * dz
        )

        range_m = math.sqrt(s * s + e * e + z * z)
        el = math.degrees(math.asin(z / range_m)) if range_m > 0 else 0.0
        # Clockwise azimuth from True North towards East: North = -s, East = e
        az = math.degrees(math.atan2(e, -s)) % 360.0

        return az, el, range_m / 1000.0
