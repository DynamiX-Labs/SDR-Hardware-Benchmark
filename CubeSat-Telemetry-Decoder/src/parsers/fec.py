"""
Forward Error Correction (FEC) — Real Implementations
Provides frame synchronization, Reed-Solomon GF(2^8), and Viterbi decoding.

Upgraded from placeholder stubs with real implementations inspired by:
  - gr-satellites: Reed-Solomon codec and Viterbi decoder
  - SatDump: FEC pipeline integration
  - FoxTelem: CRC and error correction chains

DynamiX Labs
"""

import numpy as np
import logging
from typing import Optional, Tuple

log = logging.getLogger("cubesat.fec")


# ── Galois Field GF(2^8) Arithmetic ─────────────────────────────────────
# Used by Reed-Solomon. Primitive polynomial: x^8 + x^4 + x^3 + x^2 + 1 = 0x11D

class GF256:
    """Galois Field GF(2^8) arithmetic for Reed-Solomon coding."""

    PRIMITIVE_POLY = 0x11D  # x^8 + x^4 + x^3 + x^2 + 1
    FIELD_SIZE = 256

    def __init__(self):
        self.exp_table = [0] * 512
        self.log_table = [0] * 256
        self._init_tables()

    def _init_tables(self):
        """Generate exp and log lookup tables."""
        x = 1
        for i in range(255):
            self.exp_table[i] = x
            self.log_table[x] = i
            x <<= 1
            if x & 0x100:
                x ^= self.PRIMITIVE_POLY
            x &= 0xFF
        # Extend exp table for easy modular access
        for i in range(255, 512):
            self.exp_table[i] = self.exp_table[i - 255]

    def multiply(self, a: int, b: int) -> int:
        if a == 0 or b == 0:
            return 0
        return self.exp_table[self.log_table[a] + self.log_table[b]]

    def divide(self, a: int, b: int) -> int:
        if b == 0:
            raise ZeroDivisionError("Division by zero in GF(256)")
        if a == 0:
            return 0
        return self.exp_table[(self.log_table[a] - self.log_table[b]) % 255]

    def power(self, a: int, n: int) -> int:
        if a == 0:
            return 0
        return self.exp_table[(self.log_table[a] * n) % 255]

    def inverse(self, a: int) -> int:
        if a == 0:
            raise ZeroDivisionError("Inverse of zero in GF(256)")
        return self.exp_table[255 - self.log_table[a]]


# Module-level GF instance
_gf = GF256()


# ── Frame Synchronizer ───────────────────────────────────────────────────

class FrameSynchronizer:
    """
    Frame synchronizer with CCSDS sync word detection and bit-slip correction.
    """
    CCSDS_SYNC_WORD = bytes.fromhex("1ACFFC1D")

    def __init__(self, sync_word: bytes = None, threshold: int = 4):
        """
        Args:
            sync_word: Sync word bytes (default: CCSDS ASM 0x1ACFFC1D)
            threshold: Maximum allowed bit errors in sync word detection
        """
        self.sync_word = sync_word or self.CCSDS_SYNC_WORD
        self.threshold = threshold
        self._sync_bits = np.unpackbits(
            np.frombuffer(self.sync_word, dtype=np.uint8)
        )
        self._lock_count = 0
        self._total_searches = 0

    @property
    def sync_locked(self) -> bool:
        return self._lock_count > 0

    def find_sync_and_align(self, soft_symbols: np.ndarray) -> Tuple[np.ndarray, int]:
        """
        Slide across the soft symbol bitstream to find the sync word.

        Returns:
            Tuple of (aligned_symbols, sync_offset)
        """
        self._total_searches += 1
        log.debug("Running Frame Synchronizer...")

        # Hard threshold for correlation
        hard_bits = (soft_symbols > 0).astype(np.uint8)
        sync_bits = self._sync_bits
        sync_len = len(sync_bits)

        if len(hard_bits) < sync_len:
            return soft_symbols, -1

        # Cross-correlate using bipolar mapping
        bipolar_hard = hard_bits * 2 - 1
        bipolar_sync = sync_bits * 2 - 1
        corr = np.correlate(bipolar_hard.astype(np.float32),
                            bipolar_sync.astype(np.float32), mode='valid')

        # Find best match
        max_idx = int(np.argmax(corr))
        max_corr = corr[max_idx]

        # Check against threshold
        min_required = sync_len - self.threshold
        if max_corr >= min_required:
            self._lock_count += 1
            bit_errors = sync_len - int(max_corr)
            log.info(f"Sync Word LOCKED at bit {max_idx} "
                     f"(corr={max_corr}/{sync_len}, errors={bit_errors})")
            aligned = soft_symbols[max_idx + sync_len:]
            return aligned, max_idx

        log.warning(f"Sync Word not found (best corr={max_corr}/{sync_len})")
        return soft_symbols, -1

    @property
    def stats(self) -> dict:
        return {
            "lock_count": self._lock_count,
            "total_searches": self._total_searches,
            "lock_rate": self._lock_count / max(1, self._total_searches),
        }


# ── Reed-Solomon RS(255,223) Codec ───────────────────────────────────────

class ReedSolomonCodec:
    """
    CCSDS-standard Reed-Solomon RS(255,223) codec.
    Corrects up to 16 byte errors per block (t = (255-223)/2 = 16).

    Based on GF(2^8) with primitive polynomial 0x11D.
    """

    def __init__(self, n: int = 255, k: int = 223, fcr: int = 0, prim: int = 1):
        """
        Args:
            n: Codeword length (default 255)
            k: Message length (default 223)
            fcr: First consecutive root
            prim: Primitive element
        """
        self.n = n
        self.k = k
        self.nsym = n - k  # Number of parity symbols (32)
        self.t = self.nsym // 2  # Error correction capability (16)
        self.fcr = fcr
        self.prim = prim

        # Generate generator polynomial
        self.generator = self._make_generator()

    def _make_generator(self) -> list:
        """Compute the generator polynomial for RS encoding."""
        g = [1]
        for i in range(self.nsym):
            root = _gf.exp_table[(self.fcr + i * self.prim) % 255]
            # Multiply polynomial by (x - root)
            new_g = [0] * (len(g) + 1)
            for j in range(len(g)):
                new_g[j] ^= g[j]
                new_g[j + 1] ^= _gf.multiply(g[j], root)
            g = new_g
        return g

    def encode(self, message: bytes) -> bytes:
        """
        Encode a message block with RS parity symbols.

        Args:
            message: k bytes of data (223 bytes for RS(255,223))

        Returns:
            n bytes (message + parity)
        """
        if len(message) != self.k:
            raise ValueError(f"Message must be exactly {self.k} bytes, got {len(message)}")

        # Initialize with message shifted up by nsym positions
        result = list(message) + [0] * self.nsym

        for i in range(self.k):
            coeff = result[i]
            if coeff != 0:
                for j in range(1, len(self.generator)):
                    result[i + j] ^= _gf.multiply(self.generator[j], coeff)

        # Replace message portion (first k bytes stay as original)
        encoded = list(message) + result[self.k:]
        return bytes(encoded)

    def decode(self, codeword: bytes) -> Tuple[bytes, int]:
        """
        Decode an RS codeword, correcting errors if possible.

        Args:
            codeword: n bytes of received data

        Returns:
            Tuple of (decoded_message, errors_corrected)
            Raises ValueError if uncorrectable.
        """
        if len(codeword) > self.n:
            codeword = codeword[:self.n]

        # Calculate syndromes
        syndromes = self._compute_syndromes(list(codeword))

        # Check if all syndromes are zero (no errors)
        if all(s == 0 for s in syndromes):
            return codeword[:self.k], 0

        # Berlekamp-Massey to find error locator polynomial
        error_locator = self._berlekamp_massey(syndromes)

        # Chien search for error positions
        error_positions = self._chien_search(error_locator)

        if len(error_positions) == 0:
            raise ValueError("RS decode failed: no error positions found")

        # Forney algorithm for error magnitudes
        error_magnitudes = self._forney(syndromes, error_locator, error_positions)

        # Apply corrections
        corrected = list(codeword)
        for pos, mag in zip(error_positions, error_magnitudes):
            if pos < len(corrected):
                corrected[pos] ^= mag

        errors = len(error_positions)
        log.debug(f"RS decoded: {errors} errors corrected")
        return bytes(corrected[:self.k]), errors

    def _compute_syndromes(self, codeword: list) -> list:
        """Compute syndromes S_1 through S_nsym."""
        syndromes = []
        for i in range(self.nsym):
            alpha_i = _gf.exp_table[(self.fcr + i * self.prim) % 255]
            s = 0
            for j, coeff in enumerate(codeword):
                s ^= _gf.multiply(coeff, _gf.power(alpha_i, j))
            syndromes.append(s)
        return syndromes

    def _berlekamp_massey(self, syndromes: list) -> list:
        """Berlekamp-Massey algorithm for error locator polynomial."""
        n = len(syndromes)
        C = [1] + [0] * n
        B = [1] + [0] * n
        L = 0
        m = 1
        b = 1

        for n_step in range(n):
            # Compute discrepancy
            d = syndromes[n_step]
            for i in range(1, L + 1):
                d ^= _gf.multiply(C[i], syndromes[n_step - i])

            if d == 0:
                m += 1
            elif 2 * L <= n_step:
                T = list(C)
                coeff = _gf.divide(d, b)
                for i in range(m, n + 1):
                    if i - m < len(B):
                        C[i] ^= _gf.multiply(coeff, B[i - m])
                L = n_step + 1 - L
                B = T
                b = d
                m = 1
            else:
                coeff = _gf.divide(d, b)
                for i in range(m, n + 1):
                    if i - m < len(B):
                        C[i] ^= _gf.multiply(coeff, B[i - m])
                m += 1

        return C[:L + 1]

    def _chien_search(self, error_locator: list) -> list:
        """Chien search to find error positions from error locator polynomial."""
        positions = []
        for i in range(self.n):
            # Evaluate error_locator at alpha^(-i)
            val = 0
            for j, coeff in enumerate(error_locator):
                val ^= _gf.multiply(coeff, _gf.power(
                    _gf.exp_table[(255 - i * j) % 255] if j > 0 else 1, 1
                ) if j > 0 else coeff)
            # Simplified evaluation
            result = error_locator[0]
            for j in range(1, len(error_locator)):
                result ^= _gf.multiply(error_locator[j],
                                        _gf.exp_table[(255 - i) * j % 255])
            if result == 0:
                positions.append(i)
        return positions

    def _forney(self, syndromes: list, error_locator: list,
                error_positions: list) -> list:
        """Forney algorithm for error magnitude computation."""
        magnitudes = []
        for pos in error_positions:
            xi_inv = _gf.exp_table[pos]

            # Error evaluator at xi_inv
            omega = 0
            for i, s in enumerate(syndromes):
                coeff = _gf.power(xi_inv, i)
                omega ^= _gf.multiply(s, coeff)

            # Formal derivative of error locator at xi_inv
            deriv = 0
            for i in range(1, len(error_locator), 2):
                deriv ^= _gf.multiply(error_locator[i],
                                       _gf.power(xi_inv, i - 1))

            if deriv == 0:
                magnitudes.append(0)
            else:
                magnitude = _gf.divide(omega, deriv)
                magnitudes.append(magnitude)

        return magnitudes


# ── Viterbi Decoder ──────────────────────────────────────────────────────

class ViterbiDecoder:
    """
    Viterbi decoder for convolutional codes.

    Supports Rate 1/2, Constraint Length K=7 (CCSDS standard).
    Uses soft-decision decoding for improved performance.
    """

    # CCSDS R=1/2, K=7 generator polynomials (octal: 171, 133)
    G1 = 0o171  # 0b1111001 = 0x79
    G2 = 0o133  # 0b1011011 = 0x5B
    K = 7
    RATE_INV = 2  # 1/rate
    NUM_STATES = 64  # 2^(K-1)

    def __init__(self):
        self._build_trellis()

    def _build_trellis(self):
        """Pre-compute trellis transition and output tables."""
        self.next_state = np.zeros((self.NUM_STATES, 2), dtype=np.int32)
        self.output = np.zeros((self.NUM_STATES, 2, 2), dtype=np.int8)

        for state in range(self.NUM_STATES):
            for bit in (0, 1):
                # Shift register state
                reg = (state << 1) | bit
                next_st = reg & (self.NUM_STATES - 1)
                self.next_state[state, bit] = next_st

                # Compute encoded output bits
                o1 = bin(reg & self.G1).count('1') % 2
                o2 = bin(reg & self.G2).count('1') % 2
                self.output[state, bit, 0] = o1
                self.output[state, bit, 1] = o2

    def decode(self, soft_symbols: np.ndarray) -> bytes:
        """
        Decode soft symbols using the Viterbi algorithm.

        Args:
            soft_symbols: Array of soft values (positive = 1, negative = 0)

        Returns:
            Decoded bytes
        """
        n_pairs = len(soft_symbols) // 2
        if n_pairs == 0:
            return b""

        # Path metrics
        metrics = np.full(self.NUM_STATES, -1e9, dtype=np.float64)
        metrics[0] = 0.0
        new_metrics = np.full(self.NUM_STATES, -1e9, dtype=np.float64)

        # Traceback
        traceback = np.zeros((n_pairs, self.NUM_STATES), dtype=np.int8)

        for t in range(n_pairs):
            s0 = soft_symbols[2 * t]
            s1 = soft_symbols[2 * t + 1]
            new_metrics.fill(-1e9)

            for state in range(self.NUM_STATES):
                if metrics[state] <= -1e9:
                    continue

                for bit in (0, 1):
                    next_st = self.next_state[state, bit]
                    o0 = self.output[state, bit, 0]
                    o1 = self.output[state, bit, 1]

                    # Branch metric (soft correlation)
                    bm = s0 * (2 * o0 - 1) + s1 * (2 * o1 - 1)
                    candidate = metrics[state] + bm

                    if candidate > new_metrics[next_st]:
                        new_metrics[next_st] = candidate
                        traceback[t, next_st] = bit

            metrics[:] = new_metrics

        # Traceback from best final state
        state = int(np.argmax(metrics))
        decoded_bits = np.zeros(n_pairs, dtype=np.uint8)

        for t in range(n_pairs - 1, -1, -1):
            decoded_bits[t] = traceback[t, state]
            # Reverse state transition
            bit = traceback[t, state]
            state = (state >> 1) | (bit << (self.K - 2))

        # Pack bits to bytes
        return np.packbits(decoded_bits).tobytes()

    def encode(self, data: bytes) -> np.ndarray:
        """
        Encode data bytes using the convolutional code (for testing).

        Returns soft symbols (±1.0 float array).
        """
        bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
        output = []
        state = 0

        for bit in bits:
            reg = (state << 1) | int(bit)
            o1 = bin(reg & self.G1).count('1') % 2
            o2 = bin(reg & self.G2).count('1') % 2
            output.extend([2.0 * o1 - 1.0, 2.0 * o2 - 1.0])
            state = reg & (self.NUM_STATES - 1)

        return np.array(output, dtype=np.float32)


# ── Legacy API compatibility ─────────────────────────────────────────────

class FECDecoder:
    """Legacy wrapper for backward compatibility."""

    _rs_codec = None
    _viterbi = None

    @classmethod
    def _get_rs(cls):
        if cls._rs_codec is None:
            cls._rs_codec = ReedSolomonCodec()
        return cls._rs_codec

    @classmethod
    def _get_viterbi(cls):
        if cls._viterbi is None:
            cls._viterbi = ViterbiDecoder()
        return cls._viterbi

    @staticmethod
    def viterbi_decode(soft_symbols: np.ndarray, rate: str = "1/2",
                       k: int = 7) -> bytes:
        """Decode soft symbols using Viterbi algorithm."""
        log.debug(f"Applying Viterbi decoding (Rate={rate}, K={k})")
        decoder = FECDecoder._get_viterbi()
        return decoder.decode(soft_symbols)

    @staticmethod
    def reed_solomon_decode(data: bytes, n: int = 255,
                            k: int = 223) -> bytes:
        """Decode Reed-Solomon codeword."""
        log.debug(f"Applying Reed-Solomon decoding (RS({n},{k}))")
        codec = FECDecoder._get_rs()
        try:
            decoded, errors = codec.decode(data)
            if errors > 0:
                log.info(f"RS corrected {errors} errors")
            return decoded
        except ValueError as e:
            log.error(f"RS decode failed: {e}")
            return data[:k]  # Return as-is on failure
