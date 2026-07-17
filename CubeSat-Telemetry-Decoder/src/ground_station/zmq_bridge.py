"""
ZMQ Bridge
Provides a ZeroMQ PUB socket to send real-time control messages (like Doppler shifts)
to a running GNU Radio Companion flowgraph.
"""

import zmq
import logging

log = logging.getLogger("cubesat.zmq")

class ZMQBridge:
    def __init__(self, endpoint: str = "tcp://127.0.0.1:5555"):
        self.endpoint = endpoint
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)
        try:
            # Bind to allow GNU Radio ZMQ SUB block to connect
            self.socket.bind(self.endpoint)
            log.info(f"ZMQ Bridge active on {self.endpoint}")
        except zmq.ZMQError as e:
            log.error(f"Failed to bind ZMQ socket: {e}")

    def send_doppler_correction(self, freq_hz: float):
        """
        Send frequency correction to GNU Radio.
        The GRC flowgraph should use a ZMQ SUB Message Source connected to a Message to Variable block.
        """
        # Format might need to match GRC expectations, usually dict or raw string depending on block
        message = f"freq_offset:{freq_hz}"
        self.socket.send_string(message)
        log.debug(f"Sent ZMQ doppler correction: {freq_hz} Hz")

    def close(self):
        """Cleanup ZMQ resources."""
        self.socket.close()
        self.context.term()
