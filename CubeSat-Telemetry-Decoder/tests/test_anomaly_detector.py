import pytest
from src.telemetry.anomaly_detector import AnomalyDetector, SeverityLevel

@pytest.fixture
def detector():
    return AnomalyDetector()

def test_nominal_telemetry(detector):
    """Test that nominal values do not trigger anomalies."""
    data = {
        "eps_vbatt": 7.5,
        "eps_ibat": 0.5,
        "eps_temp": 15.0,
        "adcs_gyro_x": 1.0,
        "com_tx_pwr": 1.0,
        "com_pa_temp": 30.0
    }
    events = detector.process_telemetry(1000.0, data)
    assert len(events) == 0

def test_out_of_safe_range(detector):
    """Test that a value outside the safe range triggers a critical anomaly."""
    data = {
        "eps_vbatt": 5.0,  # Below safe min of 6.0
    }
    events = detector.process_telemetry(1001.0, data)
    assert len(events) == 1
    assert events[0].parameter == "eps_vbatt"
    assert events[0].severity == SeverityLevel.CRITICAL
    assert "SAFE range" in events[0].description

def test_out_of_nominal_range(detector):
    """Test that a value outside nominal but within safe triggers a warning."""
    data = {
        "eps_vbatt": 6.5,  # Between safe (6.0) and nominal (7.2)
    }
    events = detector.process_telemetry(1002.0, data)
    assert len(events) == 1
    assert events[0].parameter == "eps_vbatt"
    assert events[0].severity == SeverityLevel.WARNING
    assert "NOMINAL range" in events[0].description

def test_multi_variable_anomaly_power_temp(detector):
    """Test multi-variable rules: High TX Power + High PA Temp."""
    data = {
        "com_tx_pwr": 2.0,
        "com_pa_temp": 70.0
    }
    events = detector.process_telemetry(1003.0, data)
    
    # We should get individual warnings for com_pa_temp (nominal is 50.0)
    # AND the multi-variable anomaly
    multi_events = [e for e in events if e.parameter == "com_pa_thermal_stress"]
    assert len(multi_events) == 1
    assert multi_events[0].severity == SeverityLevel.CRITICAL
    assert "High TX Power" in multi_events[0].description

def test_multi_variable_anomaly_adcs_tumble(detector):
    """Test ADCS tumble detection."""
    data = {
        "adcs_gyro_x": 15.0,
        "adcs_gyro_y": 15.0,
        "adcs_gyro_z": 0.0
    }
    events = detector.process_telemetry(1004.0, data)
    
    # Magnitude = sqrt(225 + 225) = sqrt(450) = ~21.2 > 20.0
    multi_events = [e for e in events if e.parameter == "adcs_tumble_rate"]
    assert len(multi_events) == 1
    assert multi_events[0].severity == SeverityLevel.WARNING
    assert "High rotation rate" in multi_events[0].description

def test_dashboard_data(detector):
    """Test dashboard data generation."""
    data = {"eps_vbatt": 7.5}
    detector.process_telemetry(1005.0, data)
    
    dash = detector.get_dashboard_data()
    assert "eps_vbatt" in dash["parameters"]
    assert dash["parameters"]["eps_vbatt"]["current"] == 7.5
