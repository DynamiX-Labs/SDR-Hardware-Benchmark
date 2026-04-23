import struct
import json
import logging

class CSPParser:
    """
    Parses CubeSat Space Protocol (CSP) frames.
    CSP is a network-layer protocol used heavily by GomSpace and modern 
    university CubeSats, operating similarly to TCP/IP but optimized for space.
    """
    
    # CSP Header structure (32 bits)
    # Priority (2 bits) | Source (5 bits) | Dest (5 bits) | Dest Port (6 bits) | Src Port (6 bits) | Reserved (4 bits) | HMAC/RDP/CRC/XTEA (4 flags)
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
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
        """
        Takes an underlying deframed packet (e.g. from AX.25 or raw KISS)
        and parses the CSP network layer.
        """
        if len(frame_bytes) < 6:
            self.logger.warning("Frame too short for CSP (needs length + header)")
            return {}
            
        # First 2 bytes are usually length (if over I2C/KISS, though KISS handles framing)
        # Assuming standard raw CSP: 4 byte header + payload
        
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
                
        result = {
            "network_protocol": "CSP",
            "header": header_info,
            "payload_hex": payload.hex(),
            "payload_len": len(payload)
        }
        
        # Basic port heuristics for CubeSats (e.g. Ping, Telemetry)
        if header_info["dest_port"] == 1:
            result["packet_type"] = "PING"
        elif header_info["dest_port"] == 8:
            result["packet_type"] = "EPS_TELEMETRY"
        elif header_info["dest_port"] == 9:
            result["packet_type"] = "ADCS_TELEMETRY"
            
        return result
        
    def format_telemetry(self, parsed_csp: dict) -> str:
        return json.dumps(parsed_csp, indent=2)
