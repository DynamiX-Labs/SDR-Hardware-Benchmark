import pytest
import numpy as np
from src.telemetry.anomaly_detector import AnomalyDetector, SKLEARN_AVAILABLE

@pytest.fixture
def detector():
    # Use smaller windows for testing speed
    return AnomalyDetector(history_size=20, calibration_window=10)

def test_learning_mode(detector):
    """Test that the system properly calibrates and does not flag anomalies early."""
    for _ in range(9):
        res = detector.check_eps_anomaly(8000.0)
        assert res["anomaly"] is False
        assert res["reason"] == "calibrating"

def test_statistical_anomaly(detector):
    """Test Z-score and EWMA statistical anomalies."""
    # Calibrate with normal 8V baseline with slight noise
    np.random.seed(42)
    baseline = np.random.normal(8000, 20, 15)
    for val in baseline:
        detector.check_eps_anomaly(val)
    
    # Send normal reading (within ~2 sigma)
    res = detector.check_eps_anomaly(8020.0)
    assert res["anomaly"] is False

    # Send massive spike (12V) - should trigger Z-score or EWMA
    res = detector.check_eps_anomaly(12000.0)
    assert res["anomaly"] is True
    assert "spike" in res["reason"] or "divergence" in res["reason"]

@pytest.mark.skipif(not SKLEARN_AVAILABLE, reason="scikit-learn is not installed")
def test_ai_isolation_forest_anomaly(detector):
    """Test the AI Isolation Forest anomaly detection."""
    # Calibrate with a slightly noisy baseline to train the AI
    np.random.seed(42)
    baseline = np.random.normal(7400, 50, 15)  # Normal Li-ion voltage
    
    for val in baseline:
        detector.check_eps_anomaly(val)
        
    assert detector.model_trained is True

    # Send normal reading inside the distribution
    res = detector.check_eps_anomaly(7410.0)
    assert res["anomaly"] is False
    assert res["ai_active"] is True

    # Send an AI-detectable anomaly (e.g., sudden voltage sag under load)
    # The statistical check might not catch it if it's not a massive 3-sigma or 500mV drop instantly,
    # but the Isolation Forest should flag an out-of-distribution point.
    res = detector.check_eps_anomaly(6800.0)
    assert res["anomaly"] is True
    # If the drop is 600mV, EWMA might also catch it, so we accept either reason
    assert res["reason"] in ["AI Isolation Forest flag", "EWMA divergence", "Z-Score spike (12.00)"]
