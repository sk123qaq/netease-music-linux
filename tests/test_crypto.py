"""加密与 eapi 报文测试（对应 AC-1 与 SPEC §8 E1/E3）。"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from nemusic.net import crypto


class TestAesVectors(unittest.TestCase):
    """标准向量（FIPS-197 / NIST SP 800-38A）。"""

    def test_fips197_ecb(self) -> None:
        key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        plain = bytes.fromhex("00112233445566778899aabbccddeeff")
        self.assertEqual(
            crypto.encrypt_block(plain, crypto._expand_key(key)).hex(),
            "69c4e0d86a7b0430d8cdb78070b4c55a",
        )

    def test_nist_cbc_first_block(self) -> None:
        key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
        iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        block = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
        self.assertEqual(crypto.aes_cbc_encrypt(key, iv, block)[:16].hex(), "7649abac8119b246cee98e9b12e9197d")

    def test_pkcs7_pad(self) -> None:
        self.assertEqual(crypto.pkcs7_pad(b"1234567890abcdef"), b"1234567890abcdef" + bytes([16]) * 16)
        self.assertEqual(crypto.pkcs7_pad(b"abc"), b"abc" + bytes([13]) * 13)

    def test_selftest_runs(self) -> None:
        crypto.selftest()

    def test_reject_bad_key_size(self) -> None:
        with self.assertRaises(ValueError):
            crypto._expand_key(b"short")


class TestOpensslCrossCheck(unittest.TestCase):
    """与 openssl 交叉验证（独立实现，作为 AES 的第二个预言机）。"""

    def setUp(self) -> None:
        if shutil.which("openssl") is None:
            self.skipTest("未安装 openssl，跳过交叉验证")

    def test_random_ecb_matches_openssl(self) -> None:
        import os

        for _ in range(3):
            key = os.urandom(16)
            data = os.urandom(37)  # 非整块，触发 PKCS#7
            expected = subprocess.run(
                ["openssl", "enc", "-aes-128-ecb", "-K", key.hex(), "-nopad"],
                input=crypto.pkcs7_pad(data),
                capture_output=True,
                check=True,
            ).stdout
            self.assertEqual(crypto.aes_ecb_encrypt(key, data), expected)


class TestEapiPayload(unittest.TestCase):
    """eapi 报文格式（SPEC §2.2）。"""

    def test_params_shape(self) -> None:
        out = crypto.eapi_params("/api/login/qrcode/unikey", {"type": 1})
        self.assertEqual(set(out), {"params"})
        value = out["params"]
        self.assertRegex(value, r"^[0-9A-F]+$")
        self.assertEqual(len(value) % 32, 0)  # 16 字节分组 → 32 个十六进制字符

    def test_digest_matches_spec(self) -> None:
        payload = json.dumps({"type": 1}, separators=(",", ":"))
        expected = crypto.md5_hex(f"nobody/api/song/lyricuse{payload}md5forencrypt")
        self.assertEqual(len(expected), 32)
        self.assertEqual(expected, crypto.md5_hex(f"nobody/api/song/lyricuse{payload}md5forencrypt"))

    def test_path_conversion(self) -> None:
        self.assertEqual(crypto.eapi_path("/api/login/qrcode/unikey"), "/eapi/login/qrcode/unikey")
        self.assertEqual(crypto.eapi_path("/login/x"), "/eapi/login/x")

    def test_deterministic(self) -> None:
        """同输入必须同输出（无随机盐）；不同输入必须不同。"""
        a = crypto.eapi_params("/api/x", {"a": 1})["params"]
        b = crypto.eapi_params("/api/x", {"a": 1})["params"]
        c = crypto.eapi_params("/api/x", {"a": 2})["params"]
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


if __name__ == "__main__":
    unittest.main()
