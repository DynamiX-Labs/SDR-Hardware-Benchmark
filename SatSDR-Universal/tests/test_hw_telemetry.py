"""
Tests for Real-Time Hardware Telemetry Engine
DynamiX Labs | Advanced Hardware Integration
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from utils.hardware import HardwareManager
from utils.hw_telemetry import (ClockStatus, HardwareTelemetry, HealthSnapshot,
                                TelemetryEvent, TelemetryThresholds,
                                ThermalStatus)


class TestHardwareTelemetry:
    """Test suite for HardwareTelemetry."""

    @pytest.fixture
    def telemetry(self):
        """Create a HardwareTelemetry instance with simulated HardwareManager."""
        hw = HardwareManager("rtlsdr")
        hw.configure(frequency=100e6, sample_rate=2.4e6, gain=30.0)
        t = HardwareTelemetry(hw, interval_s=0.2)
        yield t
        t.stop()
        hw.close()

    def test_telemetry_event_dataclass(self):
        """Verify TelemetryEvent serialization."""
        evt = TelemetryEvent(
            timestamp="2026-09-15T12:00:00Z",
            timestamp_us=12345.67,
            event_type="warning",
            category="temperature",
            message="Temperature elevated",
            data={"temp_C": 65.2},
        )
        d = evt.to_dict()
        assert d["type"] == "warning"
        assert d["category"] == "temperature"
        assert d["data"]["temp_C"] == 65.2

    def test_thermal_and_clock_dataclasses(self):
        """Verify ThermalStatus and ClockStatus serialization."""
        ts = ThermalStatus(
            temperature_c=48.5, max_temperature_c=52.0, throttling=False, source="uhd"
        )
        d_ts = ts.to_dict()
        assert d_ts["temperature_C"] == 48.5
        assert d_ts["throttling"] is False

        cs = ClockStatus(
            drift_ppm=1.25, drift_rate_ppm_per_hour=0.05, reference="gpsdo", locked=True
        )
        d_cs = cs.to_dict()
        assert d_cs["drift_ppm"] == 1.25
        assert d_cs["locked"] is True

    def test_thresholds_dataclass(self):
        """Verify TelemetryThresholds defaults."""
        th = TelemetryThresholds()
        assert th.max_temperature_c == 70.0
        assert th.critical_temperature_c == 85.0
        assert th.min_snr_db == 10.0

    def test_telemetry_lifecycle(self, telemetry):
        """Verify start and stop monitoring loop."""
        telemetry.start()
        assert telemetry.is_active is True

        import time

        time.sleep(0.3)

        status = telemetry.status()
        assert isinstance(status, dict)

        events = telemetry.events()
        assert isinstance(events, list)
        assert len(events) >= 1

        telemetry.stop()
        assert telemetry.is_active is False

    def test_alert_callback(self, telemetry):
        """Verify alert callback registration and invocation."""
        alerts = []
        telemetry.on_alert(lambda alert, snap: alerts.append(alert))

        # Manually create a snapshot with high temperature and run alert check
        snap = HealthSnapshot()
        snap.thermal.temperature_c = 88.0  # Critical
        snap.thermal.source = "test"
        telemetry._check_alerts(snap)

        assert len(alerts) >= 1
        assert "CRITICAL" in alerts[0]
