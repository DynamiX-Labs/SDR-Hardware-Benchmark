import datetime
import pytest
from src.ground_station.orbit_engine import OrbitEngine

# Example ISS TLE
TLE1 = "1 25544U 98067A   23285.45607560  .00015509  00000-0  28373-3 0  9997"
TLE2 = "2 25544  51.6416 352.5517 0004381 292.0528  46.9248 15.49887754420078"


def test_orbit_engine_initialization_and_tle_load():
    engine = OrbitEngine(lat=51.5, lon=-0.1, alt_m=50.0, alpha=0.3)
    assert engine.load_tle(TLE1, TLE2) is True
    assert engine.satrec is not None


def test_orbit_engine_doppler_calculation():
    engine = OrbitEngine(lat=51.5, lon=-0.1, alt_m=50.0, alpha=0.3)
    engine.load_tle(TLE1, TLE2)

    dt = datetime.datetime(2023, 10, 12, 11, 0, 0, tzinfo=datetime.timezone.utc)
    center_freq = 437.5e6

    freq1 = engine.get_smoothed_doppler(dt, center_freq)
    assert freq1 is not None
    # Doppler shift should be reasonable for LEO (< 25 kHz at 437.5 MHz)
    assert abs(freq1 - center_freq) < 25000.0

    # Test EMA smoothing with subsequent step
    dt2 = dt + datetime.timedelta(seconds=1)
    freq2 = engine.get_smoothed_doppler(dt2, center_freq)
    assert freq2 is not None
    assert abs(freq2 - freq1) < 1000.0
