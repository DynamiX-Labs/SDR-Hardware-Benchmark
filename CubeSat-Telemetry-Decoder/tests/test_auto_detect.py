import pytest
import numpy as np
from src.telemetry.auto_detect import SignalClassifier

@pytest.fixture
def classifier():
    return SignalClassifier()

def test_bpsk_detection(classifier):
    # Generate synthetic BPSK signal with some noise
    n_samples = 10000
    symbols = np.random.choice([-1.0, 1.0], size=n_samples)
    noise = (np.random.randn(n_samples) + 1j * np.random.randn(n_samples)) * 0.1
    signal = symbols + noise
    
    result = classifier.detect_modulation(signal)
    assert result["modulation"] == "BPSK"
    assert result["confidence"] > 0.8

def test_qpsk_detection(classifier):
    # Generate synthetic QPSK signal with some noise
    n_samples = 10000
    symbols = (np.random.choice([-1.0, 1.0], size=n_samples) + 
               1j * np.random.choice([-1.0, 1.0], size=n_samples)) / np.sqrt(2)
    noise = (np.random.randn(n_samples) + 1j * np.random.randn(n_samples)) * 0.1
    signal = symbols + noise
    
    result = classifier.detect_modulation(signal)
    assert result["modulation"] == "QPSK"
    assert result["confidence"] > 0.8

def test_8psk_detection(classifier):
    # Generate synthetic 8PSK signal with some noise
    n_samples = 10000
    phases = np.random.choice(np.arange(8), size=n_samples) * (np.pi / 4)
    symbols = np.exp(1j * phases)
    noise = (np.random.randn(n_samples) + 1j * np.random.randn(n_samples)) * 0.05
    signal = symbols + noise
    
    result = classifier.detect_modulation(signal)
    assert result["modulation"] == "8PSK"
    assert result["confidence"] > 0.8

def test_noise_detection(classifier):
    # Pure noise
    n_samples = 10000
    noise = np.random.randn(n_samples) + 1j * np.random.randn(n_samples)
    
    result = classifier.detect_modulation(noise)
    # The current HOS logic may classify pure complex AWGN as FSK/NOISE
    assert result["modulation"] in ["FSK/NOISE", "NOISE"]

def test_manual_override():
    classifier = SignalClassifier(manual_override="AX.25")
    # Provide dummy data
    result = classifier.detect_modulation(np.zeros(10))
    assert result["modulation"] == "AX.25"
    assert result["confidence"] == 1.0

def test_protocol_identification(classifier):
    # Test AX.25 dest callsign heuristic
    # A valid AX.25 header starts with destination callsign shifted left by 1 bit.
    ax25_frame = bytes([(ord('N') << 1), (ord('O') << 1), (ord('C') << 1), 
                        (ord('A') << 1), (ord('L') << 1), (ord('L') << 1), 0x00, 0x00, 0x00, 0x00, 0x00])
    res = classifier.identify_protocol(ax25_frame)
    assert res["protocol"] == "AX.25"
    
    # Test CCSDS header (Version 0 == bits 0-2 are 0)
    # 0x08 = 0000 1000 (version 000, type 0, sec_hdr 1, apid 000)
    ccsds_frame = bytes([0x08, 0x00, 0x00, 0x00])
    res = classifier.identify_protocol(ccsds_frame)
    assert res["protocol"] == "CCSDS"

    # Test CSP / fallback
    csp_frame = bytes([0xFF, 0xFF, 0xFF, 0xFF])
    res = classifier.identify_protocol(csp_frame)
    assert res["protocol"] == "CSP"
