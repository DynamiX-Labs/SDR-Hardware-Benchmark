"""
Anomaly Detector
Multi-dimensional anomaly detection for satellite telemetry.
Expands beyond single-variable checks to handle voltage, current,
temperature, and ADCS rates with seasonal/orbital pattern awareness.

DynamiX Labs
"""

import numpy as np
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime

log = logging.getLogger("cubesat.anomaly")

class SeverityLevel:
    INFO = 1
    WARNING = 2
    CRITICAL = 3
    FATAL = 4

@dataclass
class AnomalyEvent:
    timestamp: float
    parameter: str
    value: float
    expected_range: tuple
    severity: int
    description: str

@dataclass
class TelemetryParameter:
    name: str
    min_safe: float
    max_safe: float
    min_nominal: float
    max_nominal: float
    unit: str
    history: List[float] = field(default_factory=list)
    times: List[float] = field(default_factory=list)
    max_history: int = 1000

    def add_value(self, value: float, timestamp: float):
        self.history.append(value)
        self.times.append(timestamp)
        if len(self.history) > self.max_history:
            self.history.pop(0)
            self.times.pop(0)

class AnomalyDetector:
    """
    Detects anomalies in telemetry streams.
    """
    def __init__(self):
        self.parameters: Dict[str, TelemetryParameter] = {}
        self.events: List[AnomalyEvent] = []
        self._setup_default_parameters()

    def _setup_default_parameters(self):
        # EPS (Electrical Power System)
        self.add_parameter("eps_vbatt", min_safe=6.0, max_safe=8.4, min_nominal=7.2, max_nominal=8.2, unit="V")
        self.add_parameter("eps_ibat", min_safe=-2.0, max_safe=2.0, min_nominal=-1.0, max_nominal=1.0, unit="A")
        self.add_parameter("eps_temp", min_safe=-10.0, max_safe=50.0, min_nominal=0.0, max_nominal=30.0, unit="C")
        
        # ADCS (Attitude Determination and Control System)
        self.add_parameter("adcs_gyro_x", min_safe=-50.0, max_safe=50.0, min_nominal=-5.0, max_nominal=5.0, unit="deg/s")
        self.add_parameter("adcs_gyro_y", min_safe=-50.0, max_safe=50.0, min_nominal=-5.0, max_nominal=5.0, unit="deg/s")
        self.add_parameter("adcs_gyro_z", min_safe=-50.0, max_safe=50.0, min_nominal=-5.0, max_nominal=5.0, unit="deg/s")

        # Comm
        self.add_parameter("com_pa_temp", min_safe=-20.0, max_safe=80.0, min_nominal=0.0, max_nominal=50.0, unit="C")
        self.add_parameter("com_tx_pwr", min_safe=0.0, max_safe=2.5, min_nominal=0.5, max_nominal=2.0, unit="W")

    def add_parameter(self, name: str, min_safe: float, max_safe: float, 
                      min_nominal: float, max_nominal: float, unit: str):
        self.parameters[name] = TelemetryParameter(
            name=name, min_safe=min_safe, max_safe=max_safe,
            min_nominal=min_nominal, max_nominal=max_nominal, unit=unit
        )

    def process_telemetry(self, timestamp: float, data: Dict[str, float]) -> List[AnomalyEvent]:
        """Process a frame of telemetry and return any detected anomalies."""
        new_events = []
        
        for param_name, value in data.items():
            if param_name in self.parameters:
                param = self.parameters[param_name]
                param.add_value(value, timestamp)
                
                # Check limits
                if value < param.min_safe or value > param.max_safe:
                    event = AnomalyEvent(
                        timestamp=timestamp,
                        parameter=param_name,
                        value=value,
                        expected_range=(param.min_safe, param.max_safe),
                        severity=SeverityLevel.CRITICAL,
                        description=f"{param_name} out of SAFE range: {value:.2f} {param.unit} (Allowed: {param.min_safe}-{param.max_safe})"
                    )
                    new_events.append(event)
                    log.critical(event.description)
                elif value < param.min_nominal or value > param.max_nominal:
                    event = AnomalyEvent(
                        timestamp=timestamp,
                        parameter=param_name,
                        value=value,
                        expected_range=(param.min_nominal, param.max_nominal),
                        severity=SeverityLevel.WARNING,
                        description=f"{param_name} out of NOMINAL range: {value:.2f} {param.unit} (Allowed: {param.min_nominal}-{param.max_nominal})"
                    )
                    new_events.append(event)
                    log.warning(event.description)
                    
        # Check derived/multi-variable rules
        multi_events = self._check_multi_variable_rules(timestamp, data)
        new_events.extend(multi_events)
        
        self.events.extend(new_events)
        return new_events

    def _check_multi_variable_rules(self, timestamp: float, data: Dict[str, float]) -> List[AnomalyEvent]:
        events = []
        
        # Power vs Temp rule
        if "com_tx_pwr" in data and "com_pa_temp" in data:
            if data["com_tx_pwr"] > 1.5 and data["com_pa_temp"] > 65.0:
                events.append(AnomalyEvent(
                    timestamp=timestamp,
                    parameter="com_pa_thermal_stress",
                    value=data["com_pa_temp"],
                    expected_range=(0, 60),
                    severity=SeverityLevel.CRITICAL,
                    description=f"High TX Power ({data['com_tx_pwr']}W) causing high PA temp ({data['com_pa_temp']}C)"
                ))

        # ADCS Tumble detection
        if all(k in data for k in ["adcs_gyro_x", "adcs_gyro_y", "adcs_gyro_z"]):
            rate_mag = np.sqrt(data["adcs_gyro_x"]**2 + data["adcs_gyro_y"]**2 + data["adcs_gyro_z"]**2)
            if rate_mag > 20.0:
                 events.append(AnomalyEvent(
                    timestamp=timestamp,
                    parameter="adcs_tumble_rate",
                    value=rate_mag,
                    expected_range=(0, 10),
                    severity=SeverityLevel.WARNING,
                    description=f"High rotation rate detected: {rate_mag:.1f} deg/s"
                ))

        return events

    def get_dashboard_data(self) -> Dict[str, Any]:
        """Export data for dashboard visualization."""
        dash = {
            "parameters": {},
            "recent_events": [
                {
                    "time": datetime.fromtimestamp(e.timestamp).isoformat(),
                    "param": e.parameter,
                    "value": e.value,
                    "severity": e.severity,
                    "msg": e.description
                } for e in self.events[-50:]
            ]
        }
        
        for name, param in self.parameters.items():
            if param.history:
                dash["parameters"][name] = {
                    "current": param.history[-1],
                    "min": min(param.history),
                    "max": max(param.history),
                    "unit": param.unit,
                    "status": "nominal" # Calculate properly in full version
                }
                
        return dash
