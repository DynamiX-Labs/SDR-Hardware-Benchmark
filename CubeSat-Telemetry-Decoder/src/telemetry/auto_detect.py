"""
Multi-Protocol Auto-Detection & Signal Classification
Utilizes Higher-Order Statistics (HOS) cumulants for rigorous modulation classification
and bit-level heuristics for protocol detection.
"""
import numpy as np
import logging
from typing import Dict, Optional

log = logging.getLogger("cubesat.autodetect")

class SignalClassifier:
    """Classifies modulation and protocol based on baseband characteristics."""
    
    def __init__(self, manual_override: Optional[str] = None):
        """
        manual_override: If set (e.g. 'AX.25', 'CSP'), bypasses auto-detection.
        """
        self.manual_override = manual_override

    def detect_modulation(self, iq_samples: np.ndarray) -> Dict[str, float]:
        """
        Analyze IQ samples using Higher-Order Statistics (HOS) to classify the modulation.
        Computes 4th-order cumulants (C40, C42) to distinguish BPSK, QPSK, and 8PSK.
        """
        if self.manual_override:
            return {"modulation": self.manual_override, "confidence": 1.0}

        # Normalize the signal to unit power
        power = np.mean(np.abs(iq_samples)**2)
        if power < 1e-12:
            return {"modulation": "NOISE", "confidence": 0.99}
            
        normalized_iq = iq_samples / np.sqrt(power)

        # Calculate moments
        m20 = np.mean(normalized_iq**2)
        m21 = np.mean(np.abs(normalized_iq)**2) # Should be 1.0 due to normalization
        m40 = np.mean(normalized_iq**4)
        m42 = np.mean(np.abs(normalized_iq)**4)

        # Calculate cumulants
        c20 = m20
        c21 = m21
        c40 = m40 - 3 * (m20**2)
        c42 = m42 - (np.abs(m20)**2) - 2 * (m21**2)

        abs_c40 = np.abs(c40)
        abs_c42 = np.abs(c42)

        # Decision tree based on theoretical cumulant boundaries
        # BPSK: |C40| approx 2.0
        # QPSK: |C40| approx 1.0
        # 8PSK: |C40| approx 0.0, |C42| approx 1.0
        
        confidence = 0.0
        modulation = "UNKNOWN"

        if abs_c40 > 1.5:
            modulation = "BPSK"
            confidence = min(1.0, abs_c40 / 2.0)
        elif 0.5 < abs_c40 <= 1.5:
            modulation = "QPSK"
            # Closer to 1.0 is higher confidence for QPSK
            confidence = max(0.0, 1.0 - np.abs(abs_c40 - 1.0))
        elif abs_c40 <= 0.5 and abs_c42 > 0.5:
            modulation = "8PSK"
            confidence = max(0.0, 1.0 - np.abs(abs_c42 - 1.0))
        else:
            modulation = "FSK/NOISE"
            confidence = 0.5

        log.debug(f"HOS Cumulants -> |C40|: {abs_c40:.3f}, |C42|: {abs_c42:.3f} | Mod: {modulation} ({confidence:.2f})")
        return {"modulation": modulation, "confidence": float(confidence)}

    def identify_protocol(self, deframed_bytes: bytes) -> Dict[str, float]:
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
