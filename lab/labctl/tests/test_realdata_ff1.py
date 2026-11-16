"""FF1 against the NIST SP 800-38G sample vectors (FF1samples.pdf)."""

import pytest

from labctl.realdata.ff1 import FF1

K128 = "2B7E151628AED2A6ABF7158809CF4F3C"
K192 = K128 + "EF4359D8D580AA4F"
K256 = K192 + "7F036D6F04FC6A94"
T10 = "39383736353433323130"
T36 = "3737373770717273373737"

VECTORS = [
    # (sample, key, radix, tweak, plaintext, ciphertext)
    (1, K128, 10, "", "0123456789", "2433477484"),
    (2, K128, 10, T10, "0123456789", "6124200773"),
    (3, K128, 36, T36, "0123456789abcdefghi", "a9tv40mll9kdu509eum"),
    (4, K192, 10, "", "0123456789", "2830668132"),
    (5, K192, 10, T10, "0123456789", "2496655549"),
    (6, K192, 36, T36, "0123456789abcdefghi", "xbj3kv35jrawxv32ysr"),
    (7, K256, 10, "", "0123456789", "6657667009"),
    (8, K256, 10, T10, "0123456789", "1001623463"),
    (9, K256, 36, T36, "0123456789abcdefghi", "xs8a0azh2avyalyzuwd"),
]


@pytest.mark.parametrize("sample,key,radix,tweak,pt,ct", VECTORS, ids=lambda v: str(v)[:12])
def test_nist_sample(sample, key, radix, tweak, pt, ct):
    ff1 = FF1(bytes.fromhex(key), radix)
    assert ff1.encrypt(pt, bytes.fromhex(tweak)) == ct
    assert ff1.decrypt(ct, bytes.fromhex(tweak)) == pt


@pytest.mark.parametrize("text", ["000000", "1234567", "98765432109876543210", "4000001234567"])
def test_round_trip_keeps_length_and_digits(text):
    ff1 = FF1(bytes(range(32)))
    out = ff1.encrypt(text, b"t")
    assert len(out) == len(text) and out.isdigit()
    assert ff1.decrypt(out, b"t") == text


def test_domain_too_small_is_refused():
    with pytest.raises(ValueError):
        FF1(bytes(16)).encrypt("12345")
