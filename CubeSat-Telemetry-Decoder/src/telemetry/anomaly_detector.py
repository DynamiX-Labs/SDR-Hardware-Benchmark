"""
AI & Statistical Telemetry Anomaly Detection
Uses EWMA/Z-Score for statistical baselining and an Isolation Forest (ML)
for multivariate anomaly detection. Includes a Calibration (Learning) Mode.
"""
import numpy as np
import logging

try:
    from sklearn.ensemble import IsolationForest
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

log = logging.getLogger("cubesat.anomaly")

from collections import deque

class AnomalyDetector:
    def __init__(self, history_size: int = 100, calibration_window: int = 50, ewma_threshold_mv: int = 500):
        if history_size <= 0:
            raise ValueError("history_size must be strictly positive")
        if calibration_window <= 0:
            raise ValueError("calibration_window must be strictly positive")
        if calibration_window > history_size:
            raise ValueError("calibration_window cannot exceed history_size")
            
        self.history_size = history_size
        self.calibration_window = calibration_window
        self.ewma_threshold = ewma_threshold_mv
        self.battery_voltage_history = deque(maxlen=self.history_size)
        self.ewma_alpha = 0.2
        self.current_ewma = None
        self.model_trained = False
        
        if SKLEARN_AVAILABLE:
            # AI Isolation Forest Model
            self.iso_forest = IsolationForest(contamination=0.05, random_state=42)
        else:
            self.iso_forest = None
            log.warning("scikit-learn not installed. AI Isolation Forest disabled, using statistical only.")

    def _update_ewma(self, value: float) -> float:
        if self.current_ewma is None:
            self.current_ewma = value
        else:
            self.current_ewma = (self.ewma_alpha * value) + ((1 - self.ewma_alpha) * self.current_ewma)
        return self.current_ewma

    def check_eps_anomaly(self, voltage_mv: float) -> dict:
        """
        Check for anomalies in Electrical Power System (EPS) voltage.
        Uses Statistical Z-Score, EWMA, and AI Isolation Forest.
        """
        self.battery_voltage_history.append(voltage_mv)

        ewma_val = self._update_ewma(voltage_mv)
        samples_count = len(self.battery_voltage_history)
        
        # Baseline Calibration (Learning Mode)
        if samples_count < self.calibration_window:
            log.debug(f"Learning Mode: Calibrating baseline ({samples_count}/{self.calibration_window})")
            return {"anomaly": False, "reason": "calibrating", "learning_progress": samples_count / self.calibration_window}

        # Train Isolation Forest if we just finished calibrating
        if self.iso_forest is not None and not self.model_trained and samples_count >= self.calibration_window:
            X_train = np.array(self.battery_voltage_history).reshape(-1, 1)
            self.iso_forest.fit(X_train)
            self.model_trained = True
            log.info("AI Isolation Forest model trained on calibration data.")
        elif self.iso_forest is not None and self.model_trained and samples_count % 50 == 0:
            # Retrain periodically
            X_train = np.array(self.battery_voltage_history).reshape(-1, 1)
            self.iso_forest.fit(X_train)
            log.debug("AI Isolation Forest model retrained with sliding window.")

        # Statistical Checks
        mean = np.mean(self.battery_voltage_history)
        std_dev = np.std(self.battery_voltage_history)
        if std_dev < 1e-6:
            std_dev = 1e-6

        z_score = abs(voltage_mv - mean) / std_dev
        
        is_anomaly = False
        reason = ""

        # Check AI Model first
        if self.model_trained:
            # Isolation Forest returns -1 for anomaly, 1 for normal
            prediction = self.iso_forest.predict(np.array([[voltage_mv]]))[0]
            if prediction == -1:
                is_anomaly = True
                reason = "AI Isolation Forest flag"

        # Check Statistical Thresholds
        if not is_anomaly:
            if z_score > 3.0:
                is_anomaly = True
                reason = f"Z-Score spike ({z_score:.2f})"
            elif abs(voltage_mv - ewma_val) > self.ewma_threshold: # Sudden drop/spike against EWMA
                is_anomaly = True
                reason = "EWMA divergence"

        if is_anomaly:
            log.warning(f"Telemetry Anomaly Detected: {reason} | V={voltage_mv}")

        return {
            "anomaly": is_anomaly,
            "z_score": float(z_score),
            "ewma": float(ewma_val),
            "reason": reason,
            "ai_active": self.model_trained
        }
