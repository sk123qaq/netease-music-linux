"""LRC 解析 + 行索引（AC-4.6）。"""

import unittest

from nemusic.model.lrc import merge_translation, parse_lrc, parse_payload
from nemusic.model.lyric_index import LyricIndex
from nemusic.model.types import LyricLine


class TestParseLrc(unittest.TestCase):
    def test_dot_and_colon_fraction(self) -> None:
        """网易云两种时间戳写法都要认：``[mm:ss.xx]`` 与 ``[mm:ss:xx]``。"""
        lines, _ = parse_lrc("[00:01.50]a\n[00:02:50]b")
        self.assertEqual([line.time_ms for line in lines], [1500, 2500])
        self.assertEqual([line.text for line in lines], ["a", "b"])

    def test_millisecond_three_digits(self) -> None:
        lines, _ = parse_lrc("[01:02.345]c")
        self.assertEqual(lines[0].time_ms, 62_345)

    def test_multiple_stamps_split_into_lines(self) -> None:
        lines, _ = parse_lrc("[00:10.00][00:20.00]同一句")
        self.assertEqual([line.time_ms for line in lines], [10_000, 20_000])
        self.assertEqual({line.text for line in lines}, {"同一句"})

    def test_meta_lines_become_credits(self) -> None:
        _lines, credits = parse_lrc("[ti:歌名]\n[ar:歌手]\n[al:专辑]\n[00:01.00]词")
        self.assertIn("歌曲：歌名", credits)
        self.assertIn("歌手：歌手", credits)
        self.assertIn("专辑：专辑", credits)

    def test_leading_credit_lines_are_extracted(self) -> None:
        """真机样本：作词/作曲/编曲是**带时间戳**的行（0s/1s/2s），不该参与滚动。"""
        lines, credits = parse_lrc(
            "[00:00.00] 作词 : 宝石Gem\n[00:01.00] 作曲 : 宝石Gem\n"
            "[00:02.00] 编曲 : 王朝1982\n[00:24.94]看这山 万壑千岩"
        )
        self.assertEqual([line.text for line in lines], ["看这山 万壑千岩"])
        self.assertEqual([line.time_ms for line in lines], [24_940])
        self.assertEqual(credits, ["作词 : 宝石Gem", "作曲 : 宝石Gem", "编曲 : 王朝1982"])

    def test_credit_word_mid_song_is_kept(self) -> None:
        """正文里出现「作曲」字样（10 秒以后）不能误抽。"""
        lines, credits = parse_lrc("[00:30.00]作曲 : 我自己\n[00:35.00]下一句")
        self.assertEqual([line.text for line in lines], ["作曲 : 我自己", "下一句"])
        self.assertEqual(credits, [])

    def test_offset_is_not_a_credit(self) -> None:
        _lines, credits = parse_lrc("[offset:+500]\n[00:01.00]词")
        self.assertEqual(credits, [])

    def test_empty_and_junk(self) -> None:
        self.assertEqual(parse_lrc(""), ([], []))
        self.assertEqual(parse_lrc("没有时间戳的说明文字"), ([], []))
        self.assertEqual(parse_lrc("\n\n  \n"), ([], []))

    def test_crlf_and_sorted(self) -> None:
        lines, _ = parse_lrc("[00:05.00]后\r\n[00:01.00]先\r\n")
        self.assertEqual([line.text for line in lines], ["先", "后"])

    def test_dedup_same_time_same_text(self) -> None:
        lines, _ = parse_lrc("[00:01.00]x\n[00:01.00]x")
        self.assertEqual(len(lines), 1)


class TestTranslation(unittest.TestCase):
    def test_merge_by_time(self) -> None:
        lines, _ = parse_lrc("[00:00.00]你好\n[00:05.00]世界")
        trans, _ = parse_lrc("[00:00.00]hello\n[00:05.00]world")
        merged = merge_translation(lines, trans)
        self.assertEqual([(l.text, l.translation) for l in merged], [("你好", "hello"), ("世界", "world")])

    def test_merge_without_translation_keeps_lines(self) -> None:
        lines, _ = parse_lrc("[00:00.00]你好")
        self.assertEqual([l.translation for l in merge_translation(lines, [])], [""])

    def test_merge_tolerates_small_offset(self) -> None:
        """翻译时间戳差一点点也要能对上（差 50ms 以内）。"""
        lines, _ = parse_lrc("[00:10.00]正文")
        trans, _ = parse_lrc("[00:10.02]译文")
        self.assertEqual(merge_translation(lines, trans)[0].translation, "译文")


class TestParsePayload(unittest.TestCase):
    def test_full_payload(self) -> None:
        doc = parse_payload(42, {
            "lrc": {"lyric": "[ti:图]\n[00:00.00]第一句"},
            "tlyric": {"lyric": "[00:00.00]first"},
        })
        self.assertEqual(doc.song_id, 42)
        self.assertEqual(doc.lines[0].translation, "first")
        self.assertTrue(doc.has_translation)
        self.assertEqual(doc.credits, ["歌曲：图"])

    def test_placeholder_is_treated_as_no_lyric(self) -> None:
        for text in ("[00:00.00]纯音乐，请欣赏", "[00:00.00]暂无歌词"):
            self.assertEqual(parse_payload(1, {"lrc": {"lyric": text}}).lines, [], text)

    def test_no_payload(self) -> None:
        self.assertEqual(parse_payload(1, None).lines, [])
        self.assertEqual(parse_payload(1, {}).lines, [])

    def test_only_tlyric(self) -> None:
        doc = parse_payload(1, {"tlyric": {"lyric": "[00:00.00]只有翻译"}})
        self.assertEqual(doc.lines, [])


class TestLyricIndex(unittest.TestCase):
    def _index(self) -> LyricIndex:
        return LyricIndex(lines=[LyricLine(0, "一"), LyricLine(5000, "二"), LyricLine(10_000, "三")])

    def test_before_first_line(self) -> None:
        self.assertEqual(self._index().index_at(0), 0)
        self.assertEqual(LyricIndex(lines=[LyricLine(1000, "一")]).index_at(0), -1)

    def test_between_lines(self) -> None:
        idx = self._index()
        self.assertEqual(idx.index_at(4999), 0)
        self.assertEqual(idx.index_at(5000), 1)
        self.assertEqual(idx.index_at(9999), 1)
        self.assertEqual(idx.index_at(10_000), 2)
        self.assertEqual(idx.index_at(999_999), 2)

    def test_empty(self) -> None:
        idx = LyricIndex()
        self.assertTrue(idx.empty)
        self.assertEqual(idx.index_at(1000), -1)
        self.assertIsNone(idx.line_at(1000))

    def test_line_at(self) -> None:
        self.assertEqual(self._index().line_at(6000).text, "二")

    def test_duration_hint(self) -> None:
        self.assertEqual(self._index().duration_hint, 10_000)
        self.assertEqual(LyricIndex().duration_hint, 0)

    def test_from_payload(self) -> None:
        idx = LyricIndex.from_payload(7, {"lrc": {"lyric": "[00:03.00]三秒"}})
        self.assertEqual(idx.index_at(4000), 0)
        self.assertEqual(idx.doc.song_id, 7)


if __name__ == "__main__":
    unittest.main()
