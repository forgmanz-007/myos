"""The two custom widgets: a list that updates its rows in place, and the graph."""
from collections import deque
from dataclasses import dataclass, field

from PyQt6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QAbstractItemView, QHeaderView, QLabel, QTreeWidget, QTreeWidgetItem,
                             QWidget)

SORT_ROLE = Qt.ItemDataRole.UserRole + 1
KEY_ROLE = Qt.ItemDataRole.UserRole + 2
ORDER_ROLE = Qt.ItemDataRole.UserRole + 3
HEAT_ROLE = Qt.ItemDataRole.UserRole + 4
HISTORY = 60        # points in a graph (one per update, 60 seconds at normal speed)

_HEAT_ALPHA = {1: 26, 2: 70, 3: 125, 4: 185}


def heat_brush(level: int) -> QBrush:
    return QBrush(QColor(255, 170, 0, _HEAT_ALPHA.get(level, 0))) if level else QBrush()


def _descending(item) -> bool:
    tree = item.treeWidget()
    return tree is not None and tree.header().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder


class SortItem(QTreeWidgetItem):
    """Sorts by the number behind a cell (not its text); equal rows stay in A-Z order."""

    def __lt__(self, other):
        tree = self.treeWidget()
        col = tree.sortColumn() if tree is not None else 0
        a, b = self.data(col, SORT_ROLE), other.data(col, SORT_ROLE)
        if not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
            a, b = self.text(col).lower(), other.text(col).lower()
        if a != b:
            return a < b
        mine, theirs = self.text(0).lower(), other.text(0).lower()
        return (mine > theirs) if _descending(self) else (mine < theirs)


class SectionItem(QTreeWidgetItem):
    """A heading row ("Apps (3)"); headings keep their order however the list is sorted."""

    def __lt__(self, other):
        mine, theirs = self.data(0, ORDER_ROLE) or 0, other.data(0, ORDER_ROLE) or 0
        return (mine > theirs) if _descending(self) else (mine < theirs)


@dataclass
class Spec:
    """One row as it should look now."""
    key: str
    texts: list
    sort: list = field(default_factory=list)    # value to sort by per column (None: by text)
    parent: str = None                          # key of the row above it (None: top level)
    icon: object = None
    heat: dict = field(default_factory=dict)    # column → tint level 1..4
    section: int = None                         # heading rows: their fixed position
    tooltip: str = ""


class TotalsHeader(QHeaderView):
    """Column headers with a live total above the column's name ("12%" over "CPU").
    The total is drawn by the header itself: changing a header text the normal way
    makes the whole list lay itself out and repaint, every second."""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.totals = {}
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def set_total(self, column: int, text: str) -> None:
        if self.totals.get(column) != text:
            self.totals[column] = text
            self.viewport().update()

    def paintSection(self, painter, rect, logical_index):
        painter.save()
        super().paintSection(painter, rect, logical_index)
        painter.restore()
        text = self.totals.get(logical_index)
        if text:
            top = QRect(rect.left() + 6, rect.top() + 2, rect.width() - 12, rect.height() // 2)
            painter.drawText(top, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), text)


class KeyedTree(QTreeWidget):
    """A tree whose rows are addressed by key. sync() makes it show the given rows,
    changing only what differs, so the selection, scroll position and open groups stay.

    Sorting is done here, and only when the order really changed: Qt's own sorting
    lays out and repaints the whole list every time a value changes."""

    def __init__(self, headers, numeric=(), parent=None, header=None):
        super().__init__(parent)
        if header is not None:
            self.setHeader(header)
        self.setColumnCount(len(headers))
        self.setHeaderLabels(headers)
        self.numeric = set(numeric)
        self._items = {}
        self._parents = {}
        self._sorting = False
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSortingEnabled(False)
        self.setAllColumnsShowFocus(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        head = self.header()
        head.setSectionsClickable(True)
        head.setSortIndicatorShown(True)
        head.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        head.sortIndicatorChanged.connect(self._sort_changed)
        for col in self.numeric:
            self.headerItem().setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def _sort_changed(self, column: int, order) -> None:
        """A column header was clicked (or the order was set from code)."""
        if self._sorting:
            return
        self._sorting = True
        try:
            if column != getattr(self, "_sorted_by", 0) and column in self.numeric \
                    and order == Qt.SortOrder.AscendingOrder and self.sender() is self.header() \
                    and not getattr(self, "_from_code", False):
                # First click on a number column: biggest first, like Windows.
                self.header().setSortIndicator(column, Qt.SortOrder.DescendingOrder)
            self._sorted_by = column
            self.sort_now()
        finally:
            self._sorting = False

    def sortByColumn(self, column: int, order) -> None:
        self._from_code = True
        try:
            self.header().setSortIndicator(column, order)
            self._sorted_by = column
            self.sort_now()
        finally:
            self._from_code = False

    def sort_now(self) -> None:
        head = self.header()
        self.sortItems(head.sortIndicatorSection(), head.sortIndicatorOrder())

    def _out_of_order(self) -> bool:
        """Is any row above a row it should be below (in the current sort order)?"""
        descending = self.header().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
        stack = [self.invisibleRootItem()]
        while stack:
            parent = stack.pop()
            previous = None
            for i in range(parent.childCount()):
                child = parent.child(i)
                if previous is not None and ((previous < child) if descending else (child < previous)):
                    return True
                previous = child
                if child.childCount():
                    stack.append(child)
        return False

    def current_key(self):
        item = self.currentItem()
        return item.data(0, KEY_ROLE) if item is not None else None

    def item_for(self, key):
        return self._items.get(key)

    def select(self, key) -> bool:
        item = self._items.get(key)
        if item is None:
            return False
        self.setCurrentItem(item)
        self.scrollToItem(item)
        return True

    def _new_item(self, spec: Spec):
        if spec.section is not None:
            item = SectionItem()
            item.setData(0, ORDER_ROLE, spec.section)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = self.font()
            font.setBold(True)
            item.setFont(0, font)
        else:
            item = SortItem()
            for col in self.numeric:
                item.setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        item.setData(0, KEY_ROLE, spec.key)
        return item

    def sync(self, specs) -> None:
        """Show exactly these rows. A row's parent must come before it in the list."""
        wanted = {s.key for s in specs}
        vanished = [k for k in self._items if k not in wanted]
        current = self.current_key()
        root = self.invisibleRootItem()
        try:
            for spec in specs:
                parent = self._items.get(spec.parent, root) if spec.parent else root
                item = self._items.get(spec.key)
                if item is None:
                    item = self._items[spec.key] = self._new_item(spec)
                    parent.addChild(item)
                    self._parents[spec.key] = spec.parent
                    if spec.section is not None:
                        item.setExpanded(True)
                        item.setFirstColumnSpanned(True)
                elif self._parents.get(spec.key) != spec.parent:
                    expanded = item.isExpanded()
                    (item.parent() or root).removeChild(item)
                    parent.addChild(item)
                    item.setExpanded(expanded)
                    self._parents[spec.key] = spec.parent
                for col, text in enumerate(spec.texts):
                    if item.text(col) != text:
                        item.setText(col, text)
                    value = spec.sort[col] if col < len(spec.sort) else None
                    if item.data(col, SORT_ROLE) != value:
                        item.setData(col, SORT_ROLE, value)
                    level = spec.heat.get(col, 0)
                    if (item.data(col, HEAT_ROLE) or 0) != level:
                        item.setData(col, HEAT_ROLE, level)
                        item.setBackground(col, heat_brush(level))
                if spec.icon is not None and item.icon(0).cacheKey() != spec.icon.cacheKey():
                    item.setIcon(0, spec.icon)
                if item.toolTip(0) != spec.tooltip:
                    item.setToolTip(0, spec.tooltip)
            # Rows that are gone leave last, so rows moving out of them are safe.
            gone = set(vanished)
            for key in vanished:
                item = self._items.pop(key)
                parent_key = self._parents.pop(key, None)
                if parent_key in gone:
                    continue                    # removed together with its parent
                (self._items.get(parent_key, root) if parent_key else root).removeChild(item)
        finally:
            if self._out_of_order():
                self.sort_now()
            if current is not None and current in self._items and self.current_key() != current:
                self.setCurrentItem(self._items[current])


class ElidedLabel(QLabel):
    """A label that shortens its text with "…" when there is no room for all of it."""

    def minimumSizeHint(self) -> QSize:
        return QSize(40, super().minimumSizeHint().height())

    def paintEvent(self, _event):
        p = QPainter(self)
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideMiddle, self.width())
        p.drawText(self.rect(), int(self.alignment()), text)
        p.end()


class Graph(QWidget):
    """A scrolling line graph of the last HISTORY values, newest on the right.
    A second line (dashed) can share the scale."""

    def __init__(self, color: QColor, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.series = [deque(maxlen=HISTORY)]
        self.maximum = 100.0
        self.setMinimumSize(120, 60)

    def sizeHint(self) -> QSize:
        return QSize(420, 170)

    def set_data(self, series, maximum: float) -> None:
        self.series = [list(s) for s in series]
        self.maximum = maximum if maximum > 0 else 1.0
        self.update()

    def _path(self, values, rect: QRectF, closed: bool) -> QPainterPath:
        path = QPainterPath()
        if not values:
            return path
        step = rect.width() / (HISTORY - 1)
        start = HISTORY - len(values)
        points = []
        for i, v in enumerate(values):
            share = max(0.0, min(1.0, v / self.maximum))
            points.append(QPointF(rect.left() + (start + i) * step, rect.bottom() - share * rect.height()))
        if closed:
            path.moveTo(points[0].x(), rect.bottom())
            for pt in points:
                path.lineTo(pt)
            path.lineTo(points[-1].x(), rect.bottom())
            path.closeSubpath()
        else:
            path.moveTo(points[0])
            for pt in points[1:]:
                path.lineTo(pt)
        return path

    def paintEvent(self, _event):
        p = QPainter(self)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.fillRect(rect, self.palette().base())
        grid = QColor(self.color)
        grid.setAlpha(45)
        p.setPen(QPen(grid, 1))
        for i in range(1, 6):
            x = rect.left() + rect.width() * i / 6
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        for i in range(1, 4):
            y = rect.top() + rect.height() * i / 4
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        for n, values in enumerate(self.series):
            values = list(values)[-HISTORY:]
            if n == 0:
                fill = QColor(self.color)
                fill.setAlpha(40)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                p.fillPath(self._path(values, rect, True), fill)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(self.color, 1.5)
            if n > 0:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(self._path(values, rect, False))
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setPen(QPen(self.color, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        p.end()
