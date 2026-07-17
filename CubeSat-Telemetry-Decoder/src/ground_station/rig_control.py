"""
Rig Control Module
Handles communication with Hamlib's rigctld (frequency) and rotctld (antenna rotors).
"""

import socket
import logging
from typing import Tuple, Optional

log = logging.getLogger("cubesat.rig")

class RigController:
    def __init__(self, host: str = "127.0.0.1", rig_port: int = 4532, rot_port: int = 4533):
        self.host = host
        self.rig_port = rig_port
        self.rot_port = rot_port

    def _send_cmd(self, port: int, cmd: str) -> Optional[str]:
        """Send a command via TCP and return the response."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2.0)
                s.connect((self.host, port))
                s.sendall(f"{cmd}\n".encode("ascii"))
                response = s.recv(1024).decode("ascii").strip()
                return response
        except Exception as e:
            log.error(f"Hamlib connection error on port {port}: {e}")
            return None

    def set_frequency(self, freq_hz: float) -> bool:
        """Set the radio frequency via rigctld."""
        cmd = f"F {int(freq_hz)}"
        resp = self._send_cmd(self.rig_port, cmd)
        return resp == "RPRT 0"

    def set_position(self, az: float, el: float) -> bool:
        """Set the antenna rotor position via rotctld."""
        # Ensure values are within bounds
        az = max(0.0, min(360.0, az))
        el = max(0.0, min(90.0, el))
        cmd = f"P {az:.2f} {el:.2f}"
        resp = self._send_cmd(self.rot_port, cmd)
        return resp == "RPRT 0"

    def get_position(self) -> Optional[Tuple[float, float]]:
        """Get the current antenna position from rotctld."""
        resp = self._send_cmd(self.rot_port, "p")
        if resp:
            try:
                parts = resp.split("\n")
                if len(parts) >= 2:
                    az = float(parts[0])
                    el = float(parts[1])
                    return (az, el)
            except ValueError:
                pass
        return None
