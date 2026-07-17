import struct
import json
import logging
import hmac
import hashlib

def xtea_decrypt_block(key: bytes, block: bytes, num_rounds: int = 32) -> bytes:
    """Decrypts a single 64-bit block using XTEA."""
    if len(key) != 16 or len(block) != 8:
        raise ValueError("XTEA requires a 128-bit key and 64-bit block")
        
    v0, v1 = struct.unpack(">II", block)
    k = struct.unpack(">IIII", key)
    delta = 0x9E3779B9
    sum_val = (delta * num_rounds) & 0xFFFFFFFF
    
    for _ in range(num_rounds):
        v1 = (v1 - (((v0 << 4 ^ v0 >> 5) + v0) ^ (sum_val + k[(sum_val >> 11) & 3]))) & 0xFFFFFFFF
        sum_val = (sum_val - delta) & 0xFFFFFFFF
        v0 = (v0 - (((v1 << 4 ^ v1 >> 5) + v1) ^ (sum_val + k[sum_val & 3]))) & 0xFFFFFFFF
        
    return struct.pack(">II", v0, v1)

class CSPParser:
    """
    Parses CubeSat Space Protocol (CSP) frames.
    Implements advanced cryptographic decryption and verification.
    """
    def __init__(self, hmac_key: bytes = None, xtea_key: bytes = None):
        self.logger = logging.getLogger("cubesat.csp")
        self.hmac_key = hmac_key
        self.xtea_key = xtea_key
        self.last_sequence = -1  # Replay protection
        
    def parse_header(self, header_bytes: bytes) -> dict:
        if len(header_bytes) < 4:
            raise ValueError("CSP header must be 4 bytes")
            
        header_val = struct.unpack(">I", header_bytes)[0]
        
        return {
            "priority": (header_val >> 30) & 0x03,
            "source": (header_val >> 25) & 0x1F,
            "destination": (header_val >> 20) & 0x1F,
            "dest_port": (header_val >> 14) & 0x3F,
            "src_port": (header_val >> 8) & 0x3F,
            "flags": {
                "hmac": bool((header_val >> 3) & 0x01),
                "xtea": bool((header_val >> 2) & 0x01),
                "rdp": bool((header_val >> 1) & 0x01),
                "crc32": bool((header_val) & 0x01)
            }
        }
        
    def parse_frame(self, frame_bytes: bytes) -> dict:
        if len(frame_bytes) < 6:
            self.logger.warning("Frame too short for CSP (needs length + header)")
            return {}
            
        header_bytes = frame_bytes[0:4]
        payload = frame_bytes[4:]
        header_info = self.parse_header(header_bytes)
        
        # Handle CRC32 if flag is set
        if header_info["flags"]["crc32"]:
            if len(payload) >= 4:
                crc = struct.unpack(">I", payload[-4:])[0]
                payload = payload[:-4]
                header_info["crc_val"] = crc
            else:
                self.logger.error("CRC32 flag set but payload too short")
                return {}

        # Handle HMAC Authentication
        if header_info["flags"]["hmac"]:
            if not self.hmac_key:
                self.logger.error("Packet requires HMAC but no key provided.")
                return {}
            if len(payload) < 4: # CSP typically truncates HMAC to 2-4 bytes
                self.logger.error("Payload too short for HMAC")
                return {}
                
            # CSP HMAC usually covers the header + payload (minus HMAC itself)
            mac_received = payload[-4:]
            payload = payload[:-4]
            
            mac_calc = hmac.new(self.hmac_key, header_bytes + payload, hashlib.sha256).digest()[:4]
            if not hmac.compare_digest(mac_received, mac_calc):
                self.logger.error("HMAC verification failed!")
                return {"error": "HMAC_FAILED"}

        # Sequence checking for replay protection (usually prepended when crypto is on)
        if header_info["flags"]["hmac"] or header_info["flags"]["xtea"]:
            if len(payload) >= 4:
                seq = struct.unpack(">I", payload[:4])[0]
                payload = payload[4:]
                if seq <= self.last_sequence:
                    self.logger.warning(f"Replay attack detected: seq {seq} <= last {self.last_sequence}")
                    return {"error": "REPLAY_DETECTED"}
                self.last_sequence = seq
                header_info["sequence"] = seq

        # Handle XTEA Decryption
        if header_info["flags"]["xtea"]:
            if not self.xtea_key:
                self.logger.error("Packet is XTEA encrypted but no key provided.")
                return {}
            
            # Decrypt block by block
            decrypted_payload = b""
            for i in range(0, len(payload), 8):
                block = payload[i:i+8]
                if len(block) == 8:
                    decrypted_payload += xtea_decrypt_block(self.xtea_key, block)
                else:
                    # Padding or error
                    decrypted_payload += block
            payload = decrypted_payload

        result = {
            "network_protocol": "CSP",
            "header": header_info,
            "payload_hex": payload.hex(),
            "payload_len": len(payload)
        }
        
        # Basic port heuristics for CubeSats
        if header_info["dest_port"] == 1:
            result["packet_type"] = "PING"
        elif header_info["dest_port"] == 8:
            result["packet_type"] = "EPS_TELEMETRY"
            # Semantic Validation Layer (Plausibility Checks)
            # Example EPS Payload: [uint32 timestamp][uint16 vbatt_mv][int16 current_ma]
            if len(payload) >= 8:
                ts, vbatt, curr = struct.unpack(">Ihh", payload[:8])
                if vbatt < 0 or vbatt > 10000:
                    self.logger.warning(f"Semantic Validation Failed: Impossible EPS voltage {vbatt}mV")
                    result["semantic_valid"] = False
                else:
                    result["semantic_valid"] = True
                    result["telemetry"] = {"timestamp": ts, "vbatt_mv": vbatt, "current_ma": curr}

        elif header_info["dest_port"] == 9:
            result["packet_type"] = "ADCS_TELEMETRY"
            # Semantic Validation Layer
            if len(payload) >= 16:
                q = struct.unpack(">ffff", payload[:16])
                # Validate quaternion normalization (allow small floating point error)
                norm = sum(x*x for x in q)
                if abs(norm - 1.0) > 0.1:
                    self.logger.warning(f"Semantic Validation Failed: Unnormalized ADCS quaternion (norm={norm:.2f})")
                    result["semantic_valid"] = False
                else:
                    result["semantic_valid"] = True
                    result["telemetry"] = {"q": q}
            
        return result
        
    def format_telemetry(self, parsed_csp: dict) -> str:
        return json.dumps(parsed_csp, indent=2)
