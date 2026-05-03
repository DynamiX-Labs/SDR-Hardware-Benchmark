"""
Statistical Telemetry Anomaly Detection
Uses EWMA and Z-Score to establish baselines before jumping to ML.
Includes a Baseline Calibration (Learning Mode) to prevent alert noise.
"""
import numpy as np
import logging

log = logging.getLogger("cubesat.anomaly")

class AnomalyDetector:
    def __init__(self, history_size: int = 100, calibration_window: int = 50):
        self.history_size = history_size
        self.calibration_window = calibration_window
        self.battery_voltage_history = []
        self.ewma_alpha = 0.2
        self.current_ewma = None

    def _update_ewma(self, value: float) -> float:
        if self.current_ewma is None:
            self.current_ewma = value
        else:
            self.current_ewma = (self.ewma_alpha * value) + ((1 - self.ewma_alpha) * self.current_ewma)
        return self.current_ewma

    def check_eps_anomaly(self, voltage_mv: float) -> dict:
        """
        Check for anomalies in Electrical Power System (EPS) voltage.
        Returns a dict with anomaly status and metrics.
        """
        self.battery_voltage_history.append(voltage_mv)
        if len(self.battery_voltage_history) > self.history_size:
            self.battery_voltage_history.pop(0)

        ewma_val = self._update_ewma(voltage_mv)
        samples_count = len(self.battery_voltage_history)
        
        # Baseline Calibration (Learning Mode)
        if samples_count < self.calibration_window:
            log.debug(f"Learning Mode: Calibrating baseline ({samples_count}/{self.calibration_window})")
            return {"anomaly": False, "reason": "calibrating", "learning_progress": samples_count / self.calibration_window}

        mean = np.mean(self.battery_voltage_history)
        std_dev = np.std(self.battery_voltage_history)
        
        # Avoid division by zero
        if std_dev < 1e-6:
            std_dev = 1e-6

        z_score = abs(voltage_mv - mean) / std_dev
        
        is_anomaly = False
        reason = ""

        # Thresholds
        if z_score > 3.0:
            is_anomaly = True
            reason = f"Z-Score spike ({z_score:.2f})"
        elif abs(voltage_mv - ewma_val) > 500: # Sudden 500mV drop/spike against EWMA
            is_anomaly = True
            reason = "EWMA divergence"

        if is_anomaly:
            log.warning(f"Telemetry Anomaly Detected: {reason} | V={voltage_mv}")

        return {
            "anomaly": is_anomaly,
            "z_score": z_score,
            "ewma": ewma_val,
            "reason": reason
        }
