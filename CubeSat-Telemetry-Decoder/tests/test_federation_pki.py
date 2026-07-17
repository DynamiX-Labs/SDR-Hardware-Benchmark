import pytest
import time
import struct
import base64
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from src.ground_station.federation import FederationNode

@pytest.fixture
def keys():
    # Generate an ECDSA private key for SECP256R1
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    return private_key, public_key

def test_federation_pki_signature(keys):
    priv, pub = keys
    
    # Mock node
    node = FederationNode(node_id="test_node", private_key_path=None, public_keys_dir=None)
    node.private_key = priv
    
    # Payload
    payload = b"dummy_telemetry_data"
    timestamp = time.time()
    
    # Sign
    sig_b64 = node._generate_signature(payload, timestamp)
    assert sig_b64 is not None
    assert sig_b64 != ""
    
    # Verify
    sig_bytes = base64.b64decode(sig_b64)
    msg = payload + struct.pack(">d", timestamp)
    
    # Should not raise InvalidSignature
    pub.verify(sig_bytes, msg, ec.ECDSA(hashes.SHA256()))

def test_federation_pki_verify_packet(keys):
    priv, pub = keys
    
    node = FederationNode(node_id="receiver_node", private_key_path=None, public_keys_dir=None)
    node.trust_store["sender_node"] = pub
    
    payload = b"telemetry_hello"
    timestamp = time.time()
    
    # Manual sign
    msg = payload + struct.pack(">d", timestamp)
    sig_bytes = priv.sign(msg, ec.ECDSA(hashes.SHA256()))
    sig_b64 = base64.b64encode(sig_bytes).decode('ascii')
    
    # Valid check
    assert node.verify_packet("sender_node", payload.hex(), timestamp, sig_b64) == True
    
    # Invalid sender
    assert node.verify_packet("unknown_node", payload.hex(), timestamp, sig_b64) == False
    
    # Invalid signature (tampered payload)
    tampered_payload = b"telemetry_hallo"
    assert node.verify_packet("sender_node", tampered_payload.hex(), timestamp, sig_b64) == False
