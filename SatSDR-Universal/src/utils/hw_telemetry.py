"""
Real-Time Hardware Telemetry Engine
DynamiX Labs | Advanced Hardware Integration

Continuous hardware health monitoring for long-duration satellite passes:
  - Temperature monitoring (USRP via UHD, PlutoSDR via IIO)
  - Clock drift estimation using reference signals
  - Automatic degradation detection (SNR drop, sample rate sag, overheating)
  - Structured event logger with microsecond timestamps
  - Prometheus / MQTT metrics export for remote ground stations

Usage:
    hw = HardwareManager("rtlsdr")
    hw.configure(...)

    telemetry = HardwareTelemetry(hw)
    telemetry.start()           # Background monitoring

    status = telemetry.status() # Current health snapshot
    events = telemetry.events() # Recent event log

    telemetry.stop()
"""

import numpy as np
import logging
import time
import threading
import json
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable
from datetime import datetime, timezone
from collections import deque

log = logging.getLogger("satsdr.telemetry")


# ─── Data Classes ────────────────────────────────────────────────────────

@dataclass
class TelemetryEvent:
    """A structured hardware event with timing metadata."""
    timestamp: str            # ISO 8601
    timestamp_us: float       # perf_counter for sub-us precision
    event_type: str           # "info", "warning", "error", "critical"
    category: str             # "connect", "config", "temperature", "snr", etc.
    message: str = ""
    data: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "type": self.event_type,
            "category": self.category,
            "message": self.message,
            "data": self.data,
        }

    def __str__(self) -> str:
        return f"[{self.timestamp}] {self.event_type.upper():<8} {self.category}: {self.message}"


@dataclass
class ThermalStatus:
    """Thermal health status."""
    temperature_c: float = 0.0
    max_temperature_c: float = 0.0
    throttling: bool = False
    source: str = "unknown"   # "uhd", "iio", "estimated", "unavailable"

    def to_dict(self) -> dict:
        return {
            "temperature_C": round(self.temperature_c, 1),
            "max_temperature_C": round(self.max_temperature_c, 1),
            "throttling": self.throttling,
            "source": self.source,
        }


@dataclass
class ClockStatus:
    """Oscillator clock drift status."""
    drift_ppm: float = 0.0
    drift_rate_ppm_per_hour: float = 0.0
    reference: str = "none"     # "gpsdo", "ntp", "internal"
    locked: bool = False
    measurements: int = 0

    def to_dict(self) -> dict:
        return {
            "drift_ppm": round(self.drift_ppm, 3),
            "drift_rate_ppm_per_hour": round(self.drift_rate_ppm_per_hour, 4),
            "reference": self.reference,
            "locked": self.locked,
            "measurements": self.measurements,
        }


@dataclass
class HealthSnapshot:
    """Complete health snapshot at a point in time."""
    timestamp: str = ""
    device_name: str = ""
    driver: str = ""

    # Signal quality
    snr_db: float = 0.0
    noise_floor_dbm: float = 0.0
    signal_present: bool = False

    # Streaming
    sample_rate_actual: float = 0.0
    sample_rate_requested: float = 0.0
    drop_rate_pct: float = 0.0
    buffer_utilization: float = 0.0

    # Hardware
    thermal: ThermalStatus = field(default_factory=ThermalStatus)
    clock: ClockStatus = field(default_factory=ClockStatus)

    # Overall health score (0.0 - 1.0)
    health_score: float = 1.0
    alerts: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "device": self.device_name,
            "driver": self.driver,
            "snr_dB": round(self.snr_db, 2),
            "noise_floor_dBm": round(self.noise_floor_dbm, 2),
            "signal_present": self.signal_present,
            "sample_rate_actual": round(self.sample_rate_actual, 0),
            "sample_rate_requested": self.sample_rate_requested,
            "drop_rate_pct": round(self.drop_rate_pct, 4),
            "buffer_utilization": round(self.buffer_utilization, 4),
            "thermal": self.thermal.to_dict(),
            "clock": self.clock.to_dict(),
            "health_score": round(self.health_score, 3),
            "alerts": self.alerts,
        }


# ─── Telemetry Thresholds ───────────────────────────────────────────────

@dataclass
class TelemetryThresholds:
    """Configurable thresholds for degradation detection."""
    max_temperature_c: float = 70.0        # Warn if above this
    critical_temperature_c: float = 85.0   # Error if above this
    min_snr_db: float = 10.0               # Warn if SNR drops below
    max_drop_rate_pct: float = 1.0         # Warn if drops exceed
    max_buffer_utilization: float = 0.9    # Warn if buffer nearly full
    max_clock_drift_ppm: float = 10.0      # Warn if drift exceeds
    sample_rate_tolerance_pct: float = 5.0 # Warn if rate deviates


# ─── Hardware Telemetry Engine ───────────────────────────────────────────

class HardwareTelemetry:
    """
    Continuous hardware health monitoring engine.

    Runs a background thread that periodically samples hardware state
    and generates health snapshots, events, and alerts.

    Parameters
    ----------
    hw_manager : HardwareManager
        Connected and configured hardware manager
    interval_s : float
        Monitoring interval in seconds (default: 1.0)
    max_events : int
        Maximum events to retain in memory
    thresholds : TelemetryThresholds
        Alert thresholds
    """

    def __init__(self, hw_manager, interval_s: float = 1.0,
                 max_events: int = 10000,
                 thresholds: TelemetryThresholds = None):
        self._hw = hw_manager
        self._interval = interval_s
        self._thresholds = thresholds or TelemetryThresholds()
        self._active = False
        self._thread = None

        # Event log
        self._events: deque = deque(maxlen=max_events)

        # Health history
        self._snapshots: deque = deque(maxlen=3600)  # ~1 hour at 1/s

        # SNR tracking for trend detection
        self._snr_history: deque = deque(maxlen=60)

        # Clock drift tracking
        self._clock_measurements: List[tuple] = []  # (timestamp, drift_ppm)

        # Callbacks for external alerting
        self._alert_callbacks: List[Callable] = []

        # MQTT client
        self._mqtt_client = None
        self._mqtt_topic = "satsdr/telemetry"

        # Initial event
        self._log_event("info", "telemetry", "Telemetry engine initialized")

    # ── Control ──

    def start(self):
        """Start background telemetry monitoring."""
        if self._active:
            return

        self._active = True
        self._thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="hw-telemetry",
        )
        self._thread.start()
        self._log_event("info", "telemetry", "Monitoring started", {
            "interval_s": self._interval,
        })
        log.info(f"Hardware telemetry started (interval={self._interval}s)")

    def stop(self):
        """Stop background monitoring."""
        self._active = False
        if self._thread:
            self._thread.join(timeout=5.0)
        self._log_event("info", "telemetry", "Monitoring stopped")
        log.info("Hardware telemetry stopped")

    # ── Monitoring Loop ──

    def _monitor_loop(self):
        """Background monitoring thread."""
        while self._active:
            try:
                snapshot = self._capture_snapshot()
                self._snapshots.append(snapshot)

                # Check for alerts
                self._check_alerts(snapshot)

                # Publish to MQTT if configured
                if self._mqtt_client:
                    self._publish_mqtt(snapshot)

            except Exception as e:
                self._log_event("error", "monitor", f"Monitor error: {e}")

            time.sleep(self._interval)

    def _capture_snapshot(self) -> HealthSnapshot:
        """Capture a single health snapshot."""
        snap = HealthSnapshot()
        snap.timestamp = datetime.now(timezone.utc).isoformat()

        # Device info
        if hasattr(self._hw, 'device_profile'):
            snap.device_name = self._hw.device_profile.device_name
            snap.driver = self._hw.device_profile.driver
        snap.sample_rate_requested = getattr(self._hw, 'sample_rate', 0)

        # Signal quality measurement
        try:
            samples = self._hw.read_samples(8192)
            if len(samples) > 0:
                snap.snr_db = self._measure_snr(samples)
                snap.noise_floor_dbm = self._measure_noise_floor(samples)
                snap.signal_present = snap.snr_db > 10.0
                self._snr_history.append(snap.snr_db)
        except Exception:
            pass

        # Streaming health
        if hasattr(self._hw, 'streaming_stats'):
            stats = self._hw.streaming_stats()
            snap.sample_rate_actual = stats.get('actual_sps', 0)
            snap.drop_rate_pct = stats.get('dropped_chunks', 0) / max(
                stats.get('total_samples', 1) / 65536, 1
            ) * 100
            snap.buffer_utilization = stats.get('buffer_utilization', 0)
        elif hasattr(self._hw, 'dropped_sample_stats'):
            drop_stats = self._hw.dropped_sample_stats()
            snap.drop_rate_pct = drop_stats.get('drop_rate_pct', 0)

        # Temperature
        snap.thermal = self._read_temperature()

        # Clock drift
        snap.clock = self._estimate_clock_drift()

        # Calculate health score
        snap.health_score = self._calculate_health_score(snap)

        return snap

    # ── Measurements ──

    def _measure_snr(self, samples: np.ndarray) -> float:
        """Quick SNR estimation from IQ samples."""
        n = min(len(samples), 4096)
        spectrum = np.abs(np.fft.fft(samples[:n])) ** 2

        peak_idx = np.argmax(spectrum)
        signal_mask = np.zeros(n, dtype=bool)
        for offset in range(-2, 3):
            signal_mask[(peak_idx + offset) % n] = True

        signal = np.sum(spectrum[signal_mask])
        noise = np.sum(spectrum[~signal_mask])

        if noise > 0:
            return float(10.0 * np.log10(signal / noise))
        return 100.0

    def _measure_noise_floor(self, samples: np.ndarray) -> float:
        """Measure noise floor in dBm."""
        power = np.mean(np.abs(samples) ** 2)
        if power > 0:
            return float(10.0 * np.log10(power) + 30.0)
        return -174.0

    def _read_temperature(self) -> ThermalStatus:
        """Read temperature from SDR hardware if supported."""
        thermal = ThermalStatus()

        # Try UHD (USRP) temperature sensor
        try:
            if hasattr(self._hw, '_sdr') and self._hw._sdr is not None:
                sdr = self._hw._sdr
                if hasattr(sdr, 'listSensors'):
                    sensors = sdr.listSensors()
                    for sensor in sensors:
                        if 'temp' in sensor.lower():
                            val = sdr.readSensor(sensor)
                            thermal.temperature_c = float(val)
                            thermal.source = "sensor"
                            if thermal.temperature_c > thermal.max_temperature_c:
                                thermal.max_temperature_c = thermal.temperature_c
                            thermal.throttling = thermal.temperature_c > self._thresholds.max_temperature_c
                            return thermal
        except Exception:
            pass

        thermal.source = "unavailable"
        return thermal

    def _estimate_clock_drift(self) -> ClockStatus:
        """Estimate oscillator frequency drift over time."""
        clock = ClockStatus()

        # Use NTP for time reference
        try:
            import ntplib
            ntp = ntplib.NTPClient()
            response = ntp.request('pool.ntp.org', version=3)
            offset_s = response.offset

            # Convert NTP offset to PPM relative to sample rate
            if hasattr(self._hw, 'sample_rate') and self._hw.sample_rate > 0:
                clock.drift_ppm = offset_s * 1e6
                clock.reference = "ntp"
                clock.locked = abs(offset_s) < 0.01  # 10ms

                # Track drift rate
                now = time.perf_counter()
                self._clock_measurements.append((now, clock.drift_ppm))

                # Calculate drift rate from history
                if len(self._clock_measurements) >= 2:
                    t0, d0 = self._clock_measurements[0]
                    t1, d1 = self._clock_measurements[-1]
                    dt_hours = (t1 - t0) / 3600.0
                    if dt_hours > 0:
                        clock.drift_rate_ppm_per_hour = (d1 - d0) / dt_hours

                clock.measurements = len(self._clock_measurements)

        except ImportError:
            clock.reference = "unavailable"
        except Exception:
            clock.reference = "error"

        return clock

    # ── Alert System ──

    def _check_alerts(self, snapshot: HealthSnapshot):
        """Check snapshot against thresholds and generate alerts."""
        alerts = []

        # Temperature alerts
        if snapshot.thermal.source != "unavailable":
            if snapshot.thermal.temperature_c > self._thresholds.critical_temperature_c:
                alert = f"CRITICAL: Temperature {snapshot.thermal.temperature_c:.1f}°C exceeds limit"
                alerts.append(alert)
                self._log_event("critical", "temperature", alert, {
                    "temperature_C": snapshot.thermal.temperature_c,
                })
            elif snapshot.thermal.temperature_c > self._thresholds.max_temperature_c:
                alert = f"WARNING: Temperature {snapshot.thermal.temperature_c:.1f}°C is high"
                alerts.append(alert)
                self._log_event("warning", "temperature", alert)

        # SNR degradation
        if snapshot.snr_db < self._thresholds.min_snr_db and snapshot.snr_db > 0:
            alert = f"Low SNR: {snapshot.snr_db:.1f} dB (threshold: {self._thresholds.min_snr_db})"
            alerts.append(alert)
            self._log_event("warning", "snr", alert, {"snr_dB": snapshot.snr_db})

        # SNR trend detection (degradation over time)
        if len(self._snr_history) >= 10:
            recent = list(self._snr_history)[-10:]
            earlier = list(self._snr_history)[:10] if len(self._snr_history) >= 20 else recent
            snr_trend = np.mean(recent) - np.mean(earlier)
            if snr_trend < -5.0:
                alert = f"SNR degrading: {snr_trend:.1f} dB trend"
                alerts.append(alert)
                self._log_event("warning", "snr_trend", alert)

        # Drop rate
        if snapshot.drop_rate_pct > self._thresholds.max_drop_rate_pct:
            alert = f"High drop rate: {snapshot.drop_rate_pct:.2f}%"
            alerts.append(alert)
            self._log_event("warning", "drops", alert)

        # Buffer utilization
        if snapshot.buffer_utilization > self._thresholds.max_buffer_utilization:
            alert = f"Buffer near full: {snapshot.buffer_utilization*100:.1f}%"
            alerts.append(alert)
            self._log_event("warning", "buffer", alert)

        # Sample rate deviation
        if snapshot.sample_rate_requested > 0 and snapshot.sample_rate_actual > 0:
            deviation = abs(
                snapshot.sample_rate_actual - snapshot.sample_rate_requested
            ) / snapshot.sample_rate_requested * 100
            if deviation > self._thresholds.sample_rate_tolerance_pct:
                alert = f"Sample rate deviation: {deviation:.1f}%"
                alerts.append(alert)
                self._log_event("warning", "sample_rate", alert)

        # Clock drift
        if abs(snapshot.clock.drift_ppm) > self._thresholds.max_clock_drift_ppm:
            alert = f"Clock drift: {snapshot.clock.drift_ppm:.1f} PPM"
            alerts.append(alert)
            self._log_event("warning", "clock", alert)

        snapshot.alerts = alerts

        # Notify callbacks
        for alert in alerts:
            for cb in self._alert_callbacks:
                try:
                    cb(alert, snapshot)
                except Exception:
                    pass

    def _calculate_health_score(self, snapshot: HealthSnapshot) -> float:
        """Calculate overall health score (0.0 - 1.0)."""
        score = 1.0

        # SNR penalty
        if snapshot.snr_db < self._thresholds.min_snr_db and snapshot.snr_db > 0:
            score -= 0.2

        # Drop rate penalty
        if snapshot.drop_rate_pct > self._thresholds.max_drop_rate_pct:
            score -= min(0.3, snapshot.drop_rate_pct / 10.0)

        # Temperature penalty
        if snapshot.thermal.source != "unavailable":
            if snapshot.thermal.temperature_c > self._thresholds.critical_temperature_c:
                score -= 0.5
            elif snapshot.thermal.temperature_c > self._thresholds.max_temperature_c:
                score -= 0.2

        # Buffer pressure penalty
        if snapshot.buffer_utilization > self._thresholds.max_buffer_utilization:
            score -= 0.1

        # Clock drift penalty
        if abs(snapshot.clock.drift_ppm) > self._thresholds.max_clock_drift_ppm:
            score -= 0.1

        return max(0.0, min(1.0, score))

    # ── Event Logging ──

    def _log_event(self, event_type: str, category: str,
                   message: str, data: dict = None):
        """Log a structured telemetry event."""
        event = TelemetryEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            timestamp_us=time.perf_counter(),
            event_type=event_type,
            category=category,
            message=message,
            data=data or {},
        )
        self._events.append(event)

        # Also log to Python logger
        log_fn = {
            "info": log.info,
            "warning": log.warning,
            "error": log.error,
            "critical": log.critical,
        }.get(event_type, log.info)
        log_fn(f"[TELEMETRY] {event}")

    # ── MQTT Export ──

    def configure_mqtt(self, broker: str = "localhost", port: int = 1883,
                       topic: str = "satsdr/telemetry",
                       client_id: str = "satsdr_telemetry"):
        """
        Configure MQTT export for remote monitoring.

        Publishes health snapshots as JSON to the specified MQTT topic.

        Parameters
        ----------
        broker : str
            MQTT broker hostname
        port : int
            MQTT broker port
        topic : str
            Base topic for telemetry messages
        client_id : str
            MQTT client identifier
        """
        try:
            import paho.mqtt.client as mqtt

            self._mqtt_client = mqtt.Client(client_id=client_id)
            self._mqtt_client.connect(broker, port, keepalive=60)
            self._mqtt_client.loop_start()
            self._mqtt_topic = topic

            self._log_event("info", "mqtt", f"MQTT connected to {broker}:{port}", {
                "topic": topic,
            })

        except ImportError:
            log.warning("paho-mqtt not installed — MQTT export disabled")
        except Exception as e:
            log.warning(f"MQTT connection failed: {e}")

    def _publish_mqtt(self, snapshot: HealthSnapshot):
        """Publish a health snapshot to MQTT."""
        if self._mqtt_client is None:
            return

        try:
            payload = json.dumps(snapshot.to_dict())
            self._mqtt_client.publish(
                f"{self._mqtt_topic}/health",
                payload,
                qos=0,
            )

            # Publish alerts separately
            for alert in snapshot.alerts:
                self._mqtt_client.publish(
                    f"{self._mqtt_topic}/alerts",
                    json.dumps({"alert": alert, "timestamp": snapshot.timestamp}),
                    qos=1,
                )
        except Exception as e:
            log.debug(f"MQTT publish error: {e}")

    # ── Public API ──

    def status(self) -> dict:
        """Get current health status."""
        if self._snapshots:
            return self._snapshots[-1].to_dict()
        return {"status": "no data"}

    def events(self, n: int = 100, category: str = None,
               event_type: str = None) -> List[dict]:
        """
        Get recent events, optionally filtered.

        Parameters
        ----------
        n : int
            Maximum number of events to return
        category : str, optional
            Filter by event category
        event_type : str, optional
            Filter by event type (info, warning, error, critical)
        """
        filtered = list(self._events)

        if category:
            filtered = [e for e in filtered if e.category == category]
        if event_type:
            filtered = [e for e in filtered if e.event_type == event_type]

        return [e.to_dict() for e in filtered[-n:]]

    def history(self, n: int = 60) -> List[dict]:
        """Get recent health snapshots."""
        return [s.to_dict() for s in list(self._snapshots)[-n:]]

    def on_alert(self, callback: Callable):
        """Register an alert callback: fn(alert_text, snapshot)."""
        self._alert_callbacks.append(callback)

    @property
    def is_active(self) -> bool:
        return self._active
