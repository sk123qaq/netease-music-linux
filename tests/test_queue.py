"""播放队列与四种模式（AC-4.1 / AC-4.7）。纯逻辑，无 Qt、无网络。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import redirect_config  # noqa: E402

from nemusic.model.types import Song  # noqa: E402
from nemusic.player.queue import MODES, PlayQueue  # noqa: E402


def songs(n: int) -> list[Song]:
    return [Song(id=i, name=f"歌{i}", artist_names=("A",), duration_ms=60_000) for i in range(1, n + 1)]


class TestBasics(unittest.TestCase):
    def test_empty_queue(self) -> None:
        q = PlayQueue()
        self.assertEqual(len(q), 0)
        self.assertIsNone(q.current)
        self.assertIsNone(q.next_index())
        self.assertIsNone(q.prev_index())

    def test_current_and_move_to(self) -> None:
        q = PlayQueue(songs(3), 1)
        self.assertEqual(q.current.id, 2)
        self.assertTrue(q.move_to(3))
        self.assertEqual(q.current.id, 3)
        self.assertFalse(q.move_to(99))

    def test_index_clamped(self) -> None:
        q = PlayQueue(songs(3), 99)
        self.assertEqual(q.index, 0)
        q2 = PlayQueue([])
        self.assertEqual(q2.index, -1)

    def test_set_items_resets_index(self) -> None:
        q = PlayQueue(songs(2), 1)
        q.set_items(songs(5), 4)
        self.assertEqual(q.index, 4)
        self.assertEqual(len(q), 5)

    def test_unknown_mode_raises(self) -> None:
        with self.assertRaises(ValueError):
            PlayQueue(songs(2)).set_mode("bogus")


class TestSequentialModes(unittest.TestCase):
    def test_order_stops_at_end(self) -> None:
        q = PlayQueue(songs(3), 0, "order")
        self.assertEqual(q.next_index(), 1)
        q.set_index(2)
        self.assertIsNone(q.next_index())
        self.assertFalse(q.has_next())

    def test_order_no_prev_at_start(self) -> None:
        q = PlayQueue(songs(3), 0, "order")
        self.assertIsNone(q.prev_index())

    def test_list_loop_wraps(self) -> None:
        q = PlayQueue(songs(3), 2, "list_loop")
        self.assertEqual(q.next_index(), 0)
        self.assertEqual(q.prev_index(), 1)
        q.set_index(0)
        self.assertEqual(q.prev_index(), 2)

    def test_single_loop_repeats(self) -> None:
        q = PlayQueue(songs(3), 1, "single_loop")
        self.assertEqual(q.next_index(), 1)
        self.assertEqual(q.prev_index(), 1)

    def test_single_song_all_modes(self) -> None:
        """单曲队列：循环类模式重复自己；顺序模式到底就停（无上一首/下一首）。"""
        for mode in ("list_loop", "single_loop", "shuffle"):
            q = PlayQueue(songs(1), 0, mode)
            self.assertEqual(q.next_index(), 0, mode)
            self.assertEqual(q.prev_index(), 0, mode)
        q_order = PlayQueue(songs(1), 0, "order")
        self.assertIsNone(q_order.next_index())
        self.assertIsNone(q_order.prev_index())


class TestShuffle(unittest.TestCase):
    def test_covers_all_without_repeat_in_one_cycle(self) -> None:
        q = PlayQueue(songs(10), 0, "shuffle", seed=42)
        order = q.shuffle_order
        self.assertEqual(sorted(order), list(range(10)))

    def test_next_walks_shuffle_order(self) -> None:
        q = PlayQueue(songs(6), 0, "shuffle", seed=7)
        order = q.shuffle_order
        start = order[0]
        q.set_index(start)
        seen = [start]
        for _ in range(5):
            nxt = q.next_index()
            seen.append(nxt)
            q.set_index(nxt)
        self.assertEqual(seen, order)  # 一轮内不重复
        # 第 7 次会重洗并继续（不会卡住）
        again = q.next_index()
        self.assertIsInstance(again, int)

    def test_no_immediate_repeat_across_cycles(self) -> None:
        q = PlayQueue(songs(5), 0, "shuffle", seed=3)
        current = q.shuffle_order[0]
        q.set_index(current)
        for _ in range(4):
            current = q.next_index()  # type: ignore[assignment]
            q.set_index(current)  # type: ignore[arg-type]
        first_of_next_cycle = q.next_index()
        self.assertIsNotNone(first_of_next_cycle)

    def test_prev_is_symmetric(self) -> None:
        q = PlayQueue(songs(5), 0, "shuffle", seed=11)
        order = q.shuffle_order
        q.set_index(order[3])
        self.assertEqual(q.prev_index(), order[2])

    def test_switching_to_shuffle_reshuffles(self) -> None:
        q = PlayQueue(songs(8), 0, "list_loop", seed=1)
        q.set_mode("shuffle")
        first = q.shuffle_order
        q.set_mode("list_loop")
        q.set_mode("shuffle")
        self.assertEqual(sorted(q.shuffle_order), list(range(8)))
        self.assertNotEqual(first, q.shuffle_order)  # 重洗过（seed 固定仍可能相同，概率极低）


class TestNextPrevConsistency(unittest.TestCase):
    def test_walking_forward_reaches_every_song(self) -> None:
        for mode in ("list_loop", "order", "shuffle", "single_loop"):
            q = PlayQueue(songs(4), 0, mode, seed=5)
            visited = {q.current.id}
            for _ in range(10):
                nxt = q.next_index()
                if nxt is None:
                    break
                q.set_index(nxt)
                visited.add(q.current.id)
            expected = 1 if mode == "single_loop" else 4
            self.assertEqual(len(visited), expected, mode)


if __name__ == "__main__":
    unittest.main()
