"""NIST SP 800-38G FF1 format-preserving encryption over AES (Prod Echo ADR-009).

Only the AES block primitive comes from `cryptography`; the Feistel rounds are written out here
following the standard step by step, and checked against the NIST sample vectors in
`tests/test_realdata_ff1.py`. Used to mask test data deterministically, not to protect secrets.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
MIN_DOMAIN = 1_000_000  # radix ** minlen must reach this (SP 800-38G rev 1)


class FF1:
    """FF1 with one AES key (16, 24 or 32 bytes) and one radix (2..36)."""

    def __init__(self, key: bytes, radix: int = 10) -> None:
        if len(key) not in (16, 24, 32):
            raise ValueError("FF1 key must be 16, 24 or 32 bytes")
        if not 2 <= radix <= len(ALPHABET):
            raise ValueError(f"radix must be 2..{len(ALPHABET)}")
        self.radix = radix
        self._alphabet = ALPHABET[:radix]
        self._aes = Cipher(algorithms.AES(key), modes.ECB()).encryptor()

    # -- public ------------------------------------------------------------------------------

    def encrypt(self, text: str, tweak: bytes = b"") -> str:
        return self._crypt(text, tweak, decrypt=False)

    def decrypt(self, text: str, tweak: bytes = b"") -> str:
        return self._crypt(text, tweak, decrypt=True)

    # -- the algorithm -----------------------------------------------------------------------

    def _ciph(self, block: bytes) -> bytes:
        return self._aes.update(block)

    def _prf(self, data: bytes) -> bytes:
        """CBC-MAC with a zero IV: the last ciphertext block."""
        y = bytes(16)
        for j in range(0, len(data), 16):
            y = self._ciph(bytes(a ^ b for a, b in zip(y, data[j : j + 16], strict=True)))
        return y

    def _num(self, numerals: list[int]) -> int:
        value = 0
        for digit in numerals:
            value = value * self.radix + digit
        return value

    def _str(self, value: int, length: int) -> list[int]:
        out = [0] * length
        for i in range(length - 1, -1, -1):
            value, out[i] = divmod(value, self.radix)
        return out

    def _crypt(self, text: str, tweak: bytes, decrypt: bool) -> str:
        radix, n, t = self.radix, len(text), len(tweak)
        try:
            x = [self._alphabet.index(ch) for ch in text.lower()]
        except ValueError as exc:
            raise ValueError(f"not a radix-{radix} numeral string") from exc
        if radix**n < MIN_DOMAIN or n < 2:
            raise ValueError(f"FF1 needs radix**len >= {MIN_DOMAIN} (got len {n})")
        u = n // 2
        v = n - u
        a, b = x[:u], x[u:]
        b_len = ((radix**v - 1).bit_length() + 7) // 8  # ceil(ceil(v * log2(radix)) / 8)
        d = 4 * ((b_len + 3) // 4) + 4
        p = (
            bytes([1, 2, 1])
            + radix.to_bytes(3, "big")
            + bytes([10, u % 256])
            + n.to_bytes(4, "big")
            + t.to_bytes(4, "big")
        )
        pad = bytes((-t - b_len - 1) % 16)
        rounds = range(9, -1, -1) if decrypt else range(10)
        for i in rounds:
            src = a if decrypt else b
            q = tweak + pad + bytes([i]) + self._num(src).to_bytes(b_len, "big")
            r = self._prf(p + q)
            s = r
            j = 1
            while len(s) < d:
                s += self._ciph(bytes(x ^ y for x, y in zip(r, j.to_bytes(16, "big"), strict=True)))
                j += 1
            y = int.from_bytes(s[:d], "big")
            m = u if i % 2 == 0 else v
            if decrypt:
                c = (self._num(b) - y) % radix**m
                b, a = a, self._str(c, m)
            else:
                c = (self._num(a) + y) % radix**m
                a, b = b, self._str(c, m)
        return "".join(self._alphabet[k] for k in a + b)
