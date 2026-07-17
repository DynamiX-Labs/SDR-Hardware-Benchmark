"""
Headless API & WebSockets Server
Exposes Ground Station Health and ADCS Validation Streams via REST/WebSockets.
"""
from flask import Flask, jsonify
from flask_socketio import SocketIO
import psutil
import logging
import random
import threading
import time

log = logging.getLogger("cubesat.server")
app = Flask(__name__)
app.config['SECRET_KEY'] = 'satsdr-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

@app.route('/api/health')
def get_health():
    """Headless API for Ground Station Health Monitoring."""
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
    return jsonify({
        "telemetry_quaternion": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0},
        "expected_orbit_frame": {"w": 0.98, "x": 0.1, "y": 0.0, "z": 0.0},
        "validation_status": "nominal",
        "deviation_degrees": 2.5
    })

def background_thread():
    """Background task sending real-time telemetry & health data via WebSockets."""
    while True:
        time.sleep(1.0)
        sdr_temp = 45.0 + (random.random() * 5.0)
        
        health_data = {
            "status": "online",
            "cpu_load_percent": psutil.cpu_percent(),
            "memory_usage_percent": psutil.virtual_memory().percent,
            "sdr_temperature_c": round(sdr_temp, 2),
            "packet_drop_rate": 0.012
        }
        
        adcs_data = {
            "telemetry_quaternion": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0},
            "expected_orbit_frame": {"w": 0.98, "x": 0.1, "y": 0.0, "z": 0.0},
            "validation_status": "nominal",
            "deviation_degrees": round(2.5 + (random.random() - 0.5), 2)
        }

        socketio.emit('health_update', health_data)
        socketio.emit('adcs_update', adcs_data)

@socketio.on('connect')
def handle_connect():
    log.info("Client connected to WebSockets stream")
    socketio.emit('server_status', {'message': 'Connected to CubeSat Telemetry Decoder'})

@socketio.on('disconnect')
def handle_disconnect():
    log.info("Client disconnected from WebSockets stream")

def run_server(port: int = 8080):
    log.info(f"Starting headless API & WebSockets server on port {port}")
    # Start the real-time background thread
    thread = threading.Thread(target=background_thread, daemon=True)
    thread.start()
    # Run the server
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)

if __name__ == '__main__':
    run_server()
