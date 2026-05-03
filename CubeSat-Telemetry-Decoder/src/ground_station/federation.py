"""
Ground Station Federation (PKI Hardened)
Decentralized MQTT packet sharing with strict timing (chronyc parsing)
and asymmetric ECDSA node authentication.
"""
import paho.mqtt.client as mqtt
import subprocess
import struct
import time
import json
import hashlib
import logging
import os
import base64

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

log = logging.getLogger("cubesat.federation")

class FederationNode:
    def __init__(self, node_id: str, private_key_path: str = None, public_keys_dir: str = None, broker: str = "mqtt.eclipseprojects.io", port: int = 1883):
        self.node_id = node_id
        self.private_key = None
        self.trust_store = {}  # Map of node_id -> public_key
        
        if private_key_path and public_keys_dir:
            self._load_keys(private_key_path, public_keys_dir)
            
        self.client = mqtt.Client(client_id=node_id)
        self.broker = broker
        self.port = port
        self.time_offset_s = 0.0
        self.uncertainty_ms = 100.0 # Default fallback
        self._sync_time()
        self.seen_hashes = set()

    def _load_keys(self, priv_path: str, pub_dir: str):
        """Load ECDSA Private Key and populate Trust Store."""
        try:
            with open(priv_path, "rb") as f:
                self.private_key = serialization.load_pem_private_key(f.read(), password=None)
            log.info("Loaded Node ECDSA Private Key.")
            
            if os.path.exists(pub_dir):
                for filename in os.listdir(pub_dir):
                    if filename.endswith(".pem"):
                        node_name = filename.replace(".pem", "")
                        with open(os.path.join(pub_dir, filename), "rb") as f:
                            pub_key = serialization.load_pem_public_key(f.read())
                            self.trust_store[node_name] = pub_key
                log.info(f"Trust Store loaded with {len(self.trust_store)} authorized nodes.")
        except Exception as e:
            log.error(f"Failed to load PKI keys: {e}")

    def _sync_time(self):
        """Enforce strict NTP synchronization by querying chronyc."""
        try:
            output = subprocess.check_output(['chronyc', 'tracking'], universal_newlines=True)
            for line in output.split('\n'):
                if 'System time' in line:
                    parts = line.split(':')
                    val_str = parts[1].strip().split(' ')[0]
                    self.time_offset_s = float(val_str)
                elif 'Root dispersion' in line:
                    val_str = line.split(':')[1].strip().split(' ')[0]
                    self.uncertainty_ms = float(val_str) * 1000.0
            log.info(f"Chrony Sync: Offset {self.time_offset_s:.6f}s, Uncertainty ±{self.uncertainty_ms:.2f}ms")
        except Exception as e:
            log.warning(f"Chrony failed/missing ({e}). Falling back to dummy uncertainty ±100ms.")
            self.uncertainty_ms = 100.0

    def connect(self):
        try:
            self.client.connect(self.broker, self.port, 60)
            self.client.loop_start()
            log.info(f"Connected to Federation Broker at {self.broker}")
        except Exception as e:
            log.error(f"Federation connection failed: {e}")

    def _generate_signature(self, payload: bytes, timestamp: float) -> str:
        """Sign the packet using ECDSA Private Key."""
        if not self.private_key:
            return ""
        msg = payload + struct.pack(">d", timestamp)
        sig = self.private_key.sign(msg, ec.ECDSA(hashes.SHA256()))
        return base64.b64encode(sig).decode('ascii')
        
    def verify_packet(self, sender_node_id: str, payload_hex: str, timestamp: float, signature_b64: str) -> bool:
        """Verify an incoming packet against the Trust Store."""
        if sender_node_id not in self.trust_store:
            log.warning(f"Rejected packet from unknown node: {sender_node_id}")
            return False
            
        try:
            pub_key = self.trust_store[sender_node_id]
            payload = bytes.fromhex(payload_hex)
            msg = payload + struct.pack(">d", timestamp)
            sig = base64.b64decode(signature_b64)
            pub_key.verify(sig, msg, ec.ECDSA(hashes.SHA256()))
            return True
        except InvalidSignature:
            log.warning(f"Invalid ECDSA signature from node: {sender_node_id}")
            return False
        except Exception as e:
            log.error(f"Verification error: {e}")
            return False

    def publish_packet(self, satellite: str, protocol: str, payload: bytes):
        """Publish an ECDSA authenticated packet to the global network."""
        packet_hash = hashlib.sha256(payload).hexdigest()
        
        if packet_hash in self.seen_hashes:
            return 
            
        self.seen_hashes.add(packet_hash)
        
        true_time = time.time() + self.time_offset_s
        signature = self._generate_signature(payload, true_time)
        
        if not signature:
            log.warning("No private key loaded, publishing unsigned packet.")
        
        msg = {
            "node_id": self.node_id,
            "satellite": satellite,
            "protocol": protocol,
            "timestamp": true_time,
            "timestamp_uncertainty_ms": round(self.uncertainty_ms, 2),
            "hash": packet_hash,
            "signature": signature,
            "payload_hex": payload.hex()
        }
        topic = f"cubesat/telemetry/{satellite}"
        self.client.publish(topic, json.dumps(msg))
        log.debug(f"Federated authenticated packet published to {topic}")
