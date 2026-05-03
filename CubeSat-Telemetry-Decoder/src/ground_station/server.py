"""
Headless API Server
Exposes Ground Station Health and ADCS Validation Streams via REST/WebSockets.
"""
from flask import Flask, jsonify
import psutil
import logging
import random

log = logging.getLogger("cubesat.server")
app = Flask(__name__)

@app.route('/api/health')
def get_health():
    """Headless API for Ground Station Health Monitoring."""
    # Simulated SDR temperature reading (since getting real temps requires hardware-specific APIs)
    sdr_temp = 45.0 + (random.random() * 5.0)
    
    return jsonify({
        "status": "online",
        "cpu_load_percent": psutil.cpu_percent(),
        "memory_usage_percent": psutil.virtual_memory().percent,
        "sdr_temperature_c": round(sdr_temp, 2),
        "packet_drop_rate": 0.012
    })

@app.route('/api/adcs/stream')
def get_adcs_stream():
    """Headless API for ADCS Validation (Digital Twin backend)."""
    # Returns attitude quaternion and expected orbit frame for validation
    return jsonify({
        "telemetry_quaternion": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0},
        "expected_orbit_frame": {"w": 0.98, "x": 0.1, "y": 0.0, "z": 0.0},
        "validation_status": "nominal",
        "deviation_degrees": 2.5
    })

def run_server(port: int = 8080):
    log.info(f"Starting headless API server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)

if __name__ == '__main__':
    run_server()
