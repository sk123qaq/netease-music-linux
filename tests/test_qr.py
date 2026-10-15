"""二维码回环测试（AC-1.1）：segno 生成 → zxing-cpp 反解，必须解出同一 URL。

依赖缺失时 skip（zxing-cpp 为可选测试依赖；segno 为运行依赖）。
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import redirect_config  # noqa: E402

from nemusic.net.api import Api  # noqa: E402

try:
    import segno

    HAS_SEGNO = True
except ImportError:  # pragma: no cover
    HAS_SEGNO = False

try:
    import zxingcpp

    HAS_ZXING = True
except ImportError:  # pragma: no cover
    HAS_ZXING = False

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:  # pragma: no cover
    HAS_PIL = False


def render_png(content: str, scale: int = 6) -> bytes:
    """按 LoginView 的方式生成二维码 PNG。"""
    buf = io.BytesIO()
    segno.make(content, error="m").save(buf, kind="png", scale=scale, border=2, dark="#111111", light="#ffffff")
    return buf.getvalue()


def decode_png(png: bytes) -> str:
    """用 zxing-cpp 解码。"""
    image = Image.open(io.BytesIO(png))
    result = zxingcpp.read_barcode(image)
    return result.text if result else ""


@unittest.skipUnless(HAS_SEGNO, "缺少 segno")
class TestQrRoundTrip(unittest.TestCase):
    def setUp(self) -> None:
        redirect_config()

    def test_login_url_round_trip(self) -> None:
        if not (HAS_ZXING and HAS_PIL):
            self.skipTest("缺少 zxing-cpp/Pillow，跳过回环校验")
        content = Api.qr_content("93aa9bbe-d362-401a-a683-ad649a53ba33")
        self.assertEqual(decode_png(render_png(content)), content)

    def test_long_key_round_trip(self) -> None:
        if not (HAS_ZXING and HAS_PIL):
            self.skipTest("缺少 zxing-cpp/Pillow，跳过回环校验")
        content = Api.qr_content("0" * 64)
        self.assertEqual(decode_png(render_png(content)), content)

    def test_chinese_payload_round_trip(self) -> None:
        if not (HAS_ZXING and HAS_PIL):
            self.skipTest("缺少 zxing-cpp/Pillow，跳过回环校验")
        content = "https://music.163.com/login?codekey=测试-汉字"
        self.assertEqual(decode_png(render_png(content)), content)

    def test_png_is_valid_image(self) -> None:
        if not HAS_PIL:
            self.skipTest("缺少 Pillow")
        png = render_png(Api.qr_content("abc"))
        image = Image.open(io.BytesIO(png))
        self.assertEqual(image.format, "PNG")
        self.assertGreater(image.size[0], 100)


if __name__ == "__main__":
    unittest.main()
