"""
Multi-Protocol Auto-Detection & Signal Classification
Includes Confidence Scoring and Manual Override safeguards.
"""
import numpy as np
import logging

log = logging.getLogger("cubesat.autodetect")

class SignalClassifier:
    """Classifies modulation and protocol based on baseband characteristics."""
    
    def __init__(self, manual_override: str = None):
        """
        manual_override: If set (e.g. 'AX.25', 'CSP'), bypasses auto-detection.
        """
        self.manual_override = manual_override

    def detect_modulation(self, iq_samples: np.ndarray) -> dict:
        """
        Analyze IQ samples to guess the modulation.
        Returns a dictionary with 'modulation' and 'confidence' (0.0 to 1.0).
        """
        if self.manual_override:
            return {"modulation": self.manual_override, "confidence": 1.0}

        variance = np.var(np.abs(iq_samples))
        
        # Heuristic mapping variance to confidence (Placeholder logic)
        confidence = min(1.0, max(0.0, variance / 2.0))

        if variance > 0.5:
            return {"modulation": "QPSK", "confidence": confidence}
        return {"modulation": "BPSK", "confidence": confidence}

    def identify_protocol(self, deframed_bytes: bytes) -> dict:
        """
        Heuristic check to identify the protocol layer (AX.25 vs CSP vs CCSDS).
        Returns {'protocol': str, 'confidence': float}
        """
        if self.manual_override:
            return {"protocol": self.manual_override, "confidence": 1.0}

        if not deframed_bytes:
            return {"protocol": "UNKNOWN", "confidence": 0.0}
            
        # AX.25 usually starts with a destination callsign (alphanumeric shifted)
        if len(deframed_bytes) > 10:
            if 0x20 <= (deframed_bytes[0] >> 1) <= 0x7E:
                return {"protocol": "AX.25", "confidence": 0.85}
        
        if len(deframed_bytes) >= 4:
            # Check for CCSDS primary header version (bits 0-2 == 0)
            if (deframed_bytes[0] & 0xE0) == 0:
                return {"protocol": "CCSDS", "confidence": 0.90}
                
        return {"protocol": "CSP", "confidence": 0.60} # Default fallback
