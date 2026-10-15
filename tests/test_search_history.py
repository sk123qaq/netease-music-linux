"""搜索历史（AC-2.6）。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import redirect_config  # noqa: E402

from nemusic.state.search_history import SearchHistory  # noqa: E402


class TestSearchHistory(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = redirect_config()
        self.history = SearchHistory()

    def test_add_and_order(self) -> None:
        self.history.add("周杰伦")
        self.history.add("山河图")
        self.assertEqual(self.history.items, ["山河图", "周杰伦"])

    def test_dedupe_moves_to_front(self) -> None:
        self.history.add("a")
        self.history.add("b")
        self.history.add("a")
        self.assertEqual(self.history.items, ["a", "b"])

    def test_limit(self) -> None:
        for i in range(60):
            self.history.add(f"kw{i}")
        self.assertEqual(len(self.history), 50)
        self.assertEqual(self.history.items[0], "kw59")
        self.assertEqual(self.history.items[-1], "kw10")

    def test_blank_keyword_ignored(self) -> None:
        self.history.add("")
        self.history.add("   ")
        self.assertEqual(len(self.history), 0)

    def test_keyword_is_trimmed(self) -> None:
        self.history.add("  周杰伦  ")
        self.assertEqual(self.history.items, ["周杰伦"])

    def test_remove_and_clear(self) -> None:
        self.history.add("a")
        self.history.add("b")
        self.history.remove("a")
        self.assertEqual(self.history.items, ["b"])
        self.history.clear()
        self.assertEqual(self.history.items, [])

    def test_remove_missing_is_noop(self) -> None:
        self.history.add("a")
        self.history.remove("zzz")
        self.assertEqual(self.history.items, ["a"])

    def test_persistence_reload(self) -> None:
        self.history.add("周杰伦")
        self.history.add("山河图")
        again = SearchHistory()
        self.assertEqual(again.items, ["山河图", "周杰伦"])
        self.assertTrue((self.tmp / "search_history.json").exists())

    def test_corrupt_file_tolerated(self) -> None:
        (self.tmp / "search_history.json").write_text("{oops", encoding="utf-8")
        history = SearchHistory()
        self.assertEqual(history.items, [])

    def test_wrong_shape_tolerated(self) -> None:
        (self.tmp / "search_history.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
        self.assertEqual(SearchHistory().items, [])

    def test_changed_signal(self) -> None:
        hits: list[int] = []
        self.history.changed.connect(lambda: hits.append(1))
        self.history.add("x")
        self.history.remove("x")
        self.history.clear()  # 已空，不再触发
        self.assertEqual(len(hits), 2)

    def test_custom_limit(self) -> None:
        history = SearchHistory(limit=3)
        for i in range(5):
            history.add(str(i))
        self.assertEqual(history.items, ["4", "3", "2"])


if __name__ == "__main__":
    unittest.main()
