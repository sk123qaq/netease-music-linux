"""搜索状态（AC-2.1/2.4/2.5/2.7）：四个 tab 各自分页、旧请求丢弃、加载态。

设计要点：
- 每个 tab 独立维护 ``offset/total/loading/finished``，切 tab 不丢已加载结果；
- 用递增的 ``generation`` 丢弃过期请求的回包（连续改关键词不会串台，AC-2.7）；
- 列表行按 id 去重（分页接口偶尔会重复返回）；
- 纯 Qt 信号驱动，UI 只负责渲染，网络都走 AsyncTask（工作线程）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from .. import config
from ..model.types import Song
from ..net import endpoints as ep
from ..net.api import Api
from ..util import log
from ..util.async_task import AsyncTask

_log = log.get("state.search_state")


@dataclass
class TabState:
    """单个 tab 的分页状态。"""

    items: list = field(default_factory=list)
    total: int = 0
    offset: int = 0
    loading: bool = False
    finished: bool = False
    #: 已加载的 id 集合（去重用）
    _seen: set = field(default_factory=set)

    @property
    def count(self) -> int:
        return len(self.items)

    def has_more(self, page_size: int) -> bool:
        return not self.finished and self.offset < max(self.total, self.count)

    def reset(self) -> None:
        self.items, self.total, self.offset, self.finished = [], 0, 0, False
        self._seen.clear()


class SearchState(QObject):
    """搜索页的状态与数据来源。"""

    #: (search_type, items) —— 一页结果已追加
    page_loaded = Signal(int, list)
    #: (search_type, message) —— 搜索失败
    search_failed = Signal(int, str)
    #: (search_type, loading, finished) —— 加载态变化
    loading_changed = Signal(int, bool, bool)
    #: 关键词被记录到历史时广播
    keyword_recorded = Signal(str)

    def __init__(self, api: Api, parent: QObject | None = None, *, history=None) -> None:
        super().__init__(parent)
        self.api = api
        self.history = history
        self.keyword = ""
        self.tabs: dict[int, TabState] = {t: TabState() for t in ep.SEARCH_TYPE_LABELS}
        self._generation = 0
        self._searched_keyword = ""
        self.page_size = config.SEARCH_PAGE_SIZE

    # ============================================================ 只读
    def tab(self, search_type: int) -> TabState:
        return self.tabs.setdefault(search_type, TabState())

    def items(self, search_type: int) -> list:
        return self.tab(search_type).items

    def is_loading(self, search_type: int) -> bool:
        return self.tab(search_type).loading

    # ============================================================ 操作
    def search(self, keyword: str, search_type: int = ep.SEARCH_TYPE_SONG) -> None:
        """发起一次新搜索：清空该 tab、刷新请求号、记录历史。"""
        keyword = (keyword or "").strip()
        if not keyword:
            return
        if self.keyword != keyword:
            self.keyword = keyword
            if self.history is not None:
                self.history.add(keyword)
                self.keyword_recorded.emit(keyword)
        state = self.tab(search_type)
        if state.loading:
            return  # 同一 tab 正在加载时忽略（加载完再点一次即可）
        # 换关键词时所有 tab 一起清空（旧关键词的结果不属于新关键词）；
        # 同一关键词换 tab 只清当前 tab，保留其它 tab 已加载的结果。
        if keyword != self._searched_keyword:
            self._searched_keyword = keyword
            for tab in self.tabs.values():
                tab.reset()
        else:
            state.reset()
        self._generation += 1
        self._load_page(keyword, search_type, 0, self._generation)

    def load_more(self, search_type: int) -> None:
        """滚动到底部时加载下一页。"""
        keyword = self.keyword
        if not keyword:
            return
        state = self.tab(search_type)
        if state.loading or state.finished:
            return
        if not state.has_more(self.page_size):
            state.finished = True
            self.loading_changed.emit(search_type, False, True)
            return
        self._generation += 1
        self._load_page(keyword, search_type, state.offset, self._generation)

    def clear(self, search_type: int | None = None) -> None:
        """清空结果（换 tab/清空搜索框时用）。"""
        for tab in ([self.tabs[search_type]] if search_type else self.tabs.values()):
            tab.reset()
            self.loading_changed.emit(list(self.tabs).index(tab) if False else 0, False, False)
        if search_type:
            self.loading_changed.emit(search_type, False, False)
        self.keyword = ""

    # ============================================================ 内部
    def _load_page(self, keyword: str, search_type: int, offset: int, generation: int) -> None:
        state = self.tab(search_type)
        state.loading = True
        state.offset = offset
        self.loading_changed.emit(search_type, True, False)

        def work() -> tuple[str, int, object, object]:
            """把 (keyword, type, page, error) 一起带回主线程（见 async_task 坑 2）。"""
            try:
                return keyword, search_type, self.api.search(keyword, search_type, offset, self.page_size), None
            except Exception as exc:  # noqa: BLE001
                return keyword, search_type, None, exc

        task = AsyncTask(work)
        task.done.connect(lambda payload: self._on_page(payload, generation))
        task.start()

    def _on_page(self, payload: tuple, generation: int) -> None:
        keyword, search_type, page, exc = payload
        state = self.tab(search_type)
        state.loading = False
        if generation != self._generation:
            _log.debug("丢弃过期搜索结果：%s/%s", keyword, search_type)
            return
        if exc is not None or page is None:
            message = getattr(exc, "user_message", str(exc)) if exc else "搜索失败"
            _log.info("搜索失败：%s（%s）", keyword, message)
            state.finished = True
            self.loading_changed.emit(search_type, False, True)
            self.search_failed.emit(search_type, message)
            return
        state.total = page.total
        added = 0
        for item in page.items:
            key = getattr(item, "id", None)
            if key is None or key in state._seen:
                continue
            state._seen.add(key)
            state.items.append(item)
            added += 1
        state.offset = page.offset + len(page.items)
        state.finished = added == 0 or state.offset >= state.total
        _log.info("搜索 %s/%s：本页 %d 条，累计 %d/%d", keyword, search_type, added, state.count, state.total)
        self.loading_changed.emit(search_type, False, state.finished)
        self.page_loaded.emit(search_type, state.items)
