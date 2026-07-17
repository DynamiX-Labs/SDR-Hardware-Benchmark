import pytest
from datetime import datetime, timezone
from src.tracker.doppler import DopplerCalculator

# Example: ISS TLE
TLE1 = "1 25544U 98067A   23285.45607560  .00015509  00000-0  28373-3 0  9997"
TLE2 = "2 25544  51.6416 352.5517 0004381 292.0528  46.9248 15.49887754420078"

def test_doppler_shift_iss():
    calc = DopplerCalculator(TLE1, TLE2, observer_lat=51.5, observer_lon=-0.1)
    
    KNOWN_DT = datetime(2023, 10, 12, 11, 0, 0, tzinfo=timezone.utc)
    
    shift = calc.doppler_shift(137.9e6, dt=KNOWN_DT)
    
    assert -6000 < shift < 6000  # LEO bound
    
    corrected = calc.corrected_frequency(137.9e6, dt=KNOWN_DT)
    assert corrected == 137.9e6 + shift
