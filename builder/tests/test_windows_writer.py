"""Regression checks for the Windows sparse writer and verifier."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WRITER = (ROOT / "scripts" / "make-usb.ps1").read_text()


def test_verifier_reuses_write_ranges_and_compares_in_native_code():
    # A normal write already discovers every non-zero range. Verification must
    # reuse that map instead of scanning a 30-60 GiB sparse image again.
    assert "$writtenRanges.Add" in WRITER
    assert "foreach ($range in $writtenRanges)" in WRITER

    # Comparing gigabytes byte-by-byte in interpreted PowerShell takes hours.
    assert "[System.Linq.Enumerable]::SequenceEqual" in WRITER
    assert "for ($i = 0; $i -lt $a; $i++)" not in WRITER
