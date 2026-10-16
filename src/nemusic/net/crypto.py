"""加密原语（纯 Python，无第三方依赖）。

本模块只服务 **eapi 备用通道**（SPEC §2.2）：
``/api/`` 明文可用时不需要它；个别端点明文失败时自动改走 ``/eapi/`` 重试。

eapi 报文格式（已实测可用，见 SPEC §8 E3）::

    message = "nobody" + api_path + "use" + json + "md5forencrypt"
    digest  = md5_hex(message)
    body    = api_path + "-36cd479b6b5-" + json + "-36cd479b6b5-" + digest
    params  = HEX_UPPER(aes_128_ecb_encrypt(key="e82ckenh8dichen8", pkcs7(body)))

关于 weapi：实测该通道对 ``/weapi/login/qrcode/unikey`` 返回 200 空响应（SPEC §8 E4），
因此**本项目不使用 weapi**；AES-CBC 仍保留实现，因为它用于校验 AES 核心（NIST 向量）。
"""

from __future__ import annotations

import hashlib
import json

# ---------------------------------------------------------------- AES 核心

SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16"
)
RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _xtime(a: int) -> int:
    """GF(2^8) 上乘 2。"""
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _gmul(a: int, b: int) -> int:
    """GF(2^8) 乘法（用于 MixColumns）。"""
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


def _expand_key(key: bytes) -> list[bytes]:
    """AES-128 密钥扩展 → 11 个轮密钥（每个 16 字节）。"""
    if len(key) != 16:
        raise ValueError("AES-128 需要 16 字节密钥")
    w = [list(key[i * 4 : i * 4 + 4]) for i in range(4)]
    for i in range(4, 44):
        t = w[i - 1][:]
        if i % 4 == 0:
            t = t[1:] + t[:1]  # RotWord
            t = [SBOX[b] for b in t]  # SubWord
            t[0] ^= RCON[i // 4 - 1]
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return [bytes(sum(w[r * 4 : r * 4 + 4], [])) for r in range(11)]


def _add_round_key(state: list[int], rk: bytes) -> list[int]:
    return [state[i] ^ rk[i] for i in range(16)]


def _sub_bytes(state: list[int]) -> list[int]:
    return [SBOX[b] for b in state]


def _shift_rows(state: list[int]) -> list[int]:
    """状态按列优先存储：state[r + 4*c]。第 r 行循环左移 r。"""
    out = state[:]
    for r in range(1, 4):
        row = [state[r + 4 * c] for c in range(4)]
        row = row[r:] + row[:r]
        for c in range(4):
            out[r + 4 * c] = row[c]
    return out


def _mix_columns(state: list[int]) -> list[int]:
    out = state[:]
    for c in range(4):
        a = state[4 * c : 4 * c + 4]
        out[4 * c + 0] = _gmul(a[0], 2) ^ _gmul(a[1], 3) ^ a[2] ^ a[3]
        out[4 * c + 1] = a[0] ^ _gmul(a[1], 2) ^ _gmul(a[2], 3) ^ a[3]
        out[4 * c + 2] = a[0] ^ a[1] ^ _gmul(a[2], 2) ^ _gmul(a[3], 3)
        out[4 * c + 3] = _gmul(a[0], 3) ^ a[1] ^ a[2] ^ _gmul(a[3], 2)
    return out


def encrypt_block(block: bytes, round_keys: list[bytes]) -> bytes:
    """加密单个 16 字节分组。"""
    if len(block) != 16:
        raise ValueError("分组必须为 16 字节")
    state = _add_round_key(list(block), round_keys[0])
    for r in range(1, 10):
        state = _add_round_key(_mix_columns(_shift_rows(_sub_bytes(state))), round_keys[r])
    state = _add_round_key(_shift_rows(_sub_bytes(state)), round_keys[10])
    return bytes(state)


def pkcs7_pad(data: bytes, block: int = 16) -> bytes:
    """PKCS#7 填充（总是补 1..block 字节）。"""
    n = block - len(data) % block
    return data + bytes([n]) * n


def aes_ecb_encrypt(key: bytes, data: bytes) -> bytes:
    """AES-128-ECB + PKCS#7（eapi 用）。"""
    rk = _expand_key(key)
    padded = pkcs7_pad(data)
    return b"".join(encrypt_block(padded[i : i + 16], rk) for i in range(0, len(padded), 16))


def aes_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-128-CBC + PKCS#7（保留实现：用于 AES 核心的 NIST 向量校验）。"""
    if len(iv) != 16:
        raise ValueError("IV 必须为 16 字节")
    rk = _expand_key(key)
    padded = pkcs7_pad(data)
    out = bytearray()
    prev = bytes(iv)
    for i in range(0, len(padded), 16):
        block = bytes(a ^ b for a, b in zip(padded[i : i + 16], prev))
        prev = encrypt_block(block, rk)
        out += prev
    return bytes(out)


# ---------------------------------------------------------------- eapi


def md5_hex(text: str | bytes) -> str:
    """md5 十六进制小写。"""
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.md5(data).hexdigest()


EAPI_KEY = b"e82ckenh8dichen8"
EAPI_SEP = "-36cd479b6b5-"


def eapi_params(api_path: str, data: dict) -> dict[str, str]:
    """构造 eapi 请求体（表单参数）。

    :param api_path: 明文通道使用的路径，如 ``/api/login/qrcode/unikey``
    :param data: 请求体字典
    :return: ``{"params": "<HEX 大写>"}``
    """
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    digest = md5_hex(f"nobody{api_path}use{payload}md5forencrypt")
    body = f"{api_path}{EAPI_SEP}{payload}{EAPI_SEP}{digest}".encode("utf-8")
    return {"params": aes_ecb_encrypt(EAPI_KEY, body).hex().upper()}


def eapi_path(api_path: str) -> str:
    """明文路径 → eapi 路径。"""
    if api_path.startswith("/api/"):
        return "/eapi/" + api_path[len("/api/") :]
    return "/eapi" + api_path


# ---------------------------------------------------------------- 自检


def _expect(got: str, want: str, label: str) -> None:
    """显式 raise：``-O`` 会删掉 assert，而 app.build_app() 靠它做启动校验。"""
    if got != want:
        raise AssertionError(f"{label} 向量不匹配: {got}")


def selftest() -> None:
    """用标准向量校验 AES 实现，失败抛 AssertionError。

    向量来源：FIPS-197 附录 B（ECB）与 NIST SP 800-38A 附录 F.2.1（CBC 首块）。
    """
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    plain = bytes.fromhex("00112233445566778899aabbccddeeff")
    _expect(encrypt_block(plain, _expand_key(key)).hex(), "69c4e0d86a7b0430d8cdb78070b4c55a", "FIPS-197 ECB")

    cbc_key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    blk = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
    # 单分组 CBC = 先异或 IV 再加密；用 pkcs7 补齐后取前 16 字节比较
    _expect(aes_cbc_encrypt(cbc_key, iv, blk)[:16].hex(), "7649abac8119b246cee98e9b12e9197d", "NIST CBC")


if __name__ == "__main__":  # python -m nemusic.net.crypto
    selftest()
    print("crypto selftest: OK")
