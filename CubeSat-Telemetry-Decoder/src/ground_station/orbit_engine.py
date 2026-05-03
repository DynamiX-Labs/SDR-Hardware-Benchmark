"""
Orbit Engine
Calculates real-time satellite positions and Doppler shifts using SGP4.
Includes a low-pass filter for closed-loop Doppler smoothing.
"""

from sgp4.api import Satrec, WGS84
import logging
from typing import Tuple, Optional
import datetime

log = logging.getLogger("cubesat.orbit")

class OrbitEngine:
    SPEED_OF_LIGHT = 299792458.0  # m/s

    def __init__(self, lat: float, lon: float, alt_m: float, alpha: float = 0.3):
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

    def get_smoothed_doppler(self, dt: datetime.datetime, center_freq_hz: float) -> Optional[float]:
        """
        Calculate the expected Doppler shifted frequency and apply an EMA 
        low-pass filter to prevent SDR tuning oscillation and overshoot.
        """
        if not self.satrec:
            log.error("No TLE loaded.")
            return None

        # SGP4 expects date components
        e, r, v = self.satrec.sgp4(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second + dt.microsecond / 1e6)
        
        if e != 0:
            log.error(f"SGP4 propagation error: code {e}")
            return None

        # Placeholder for actual relative velocity calculation
        # Let's assume a dummy relative velocity (v_rel in m/s) based on z-axis velocity
        v_rel_m_s = v[2] * 1000.0  

        # Raw target Doppler calculation
        doppler_shift = center_freq_hz * (-v_rel_m_s / self.SPEED_OF_LIGHT)
        raw_target_freq = center_freq_hz + doppler_shift

        # Apply Control Law (EMA smoothing)
        if self.last_doppler_freq is None:
            self.last_doppler_freq = raw_target_freq
        else:
            self.last_doppler_freq = self.last_doppler_freq + self.alpha * (raw_target_freq - self.last_doppler_freq)

        return self.last_doppler_freq
