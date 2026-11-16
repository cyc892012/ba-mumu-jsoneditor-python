"""日奈独立侧栏：现有立绘、自动换行气泡和作者链接均由 paintEvent 绘制。"""
from pathlib import Path
import sys

from PyQt5.QtCore import Qt, QRectF, QSize, QUrl, pyqtSignal
from PyQt5.QtGui import (QPainter, QPixmap, QColor, QLinearGradient, QPen, QFont,
                         QTextDocument, QTextOption, QDesktopServices, QPainterPath)
from PyQt5.QtWidgets import QWidget, QSizePolicy

import theme


def image_candidates(resource_root=None):
    """不依赖工作目录；覆盖源码、onefile、onedir 及现有三种图片名称。"""
    roots = []
    if resource_root is not None:
        explicit = Path(resource_root)
        roots.extend((explicit / "ui" / "assets", explicit))
    roots.append(Path(__file__).resolve().parents[1] / "ui" / "assets")
    # 准备目录运行时复用已导入 theme 的源码资源，移入 controls 后仍有效。
    if getattr(theme, "__file__", None):
        roots.append(Path(theme.__file__).resolve().parent / "ui" / "assets")
    if getattr(sys, "_MEIPASS", None):
        roots.append(Path(sys._MEIPASS) / "ui" / "assets")
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        roots.extend((executable_dir / "ui" / "assets", executable_dir / "_internal" / "ui" / "assets"))
    names = ("hina_bg.png", "hina-pajama.png", "睡衣日奈立绘.png")
    return list(dict.fromkeys(str(root / name) for root in roots for name in names))


class HinaAside(QWidget):
    linkActivated = pyqtSignal(str)
    linkOpenFailed = pyqtSignal(str)
    AUTHOR_URL = "https://space.bilibili.com/251589"

    def __init__(self, parent=None, *, resource_root=None, width=132,
                 hint="把 .json 拖进窗口\n也能直接加载哦", open_external_links=True):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._image_path = ""
        self._hint = hint
        self._author_text = "By：漆黑人形"
        self._author_url = self.AUTHOR_URL
        self._open_external_links = open_external_links
        self._link_pressed = False
        self._text_font = QFont(theme.FONT_FAMILY)
        self._text_font.setPixelSize(11)
        self.setFont(self._text_font)
        self.setMinimumWidth(120)
        self.setMaximumWidth(132)
        self.resize(max(120, min(132, width)), 416)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("日奈提示侧栏，作者：漆黑人形")
        self.setAccessibleDescription(hint)
        for path in image_candidates(resource_root):
            if self.set_image_path(path):
                break
        self._update_minimum()

    def set_pixmap(self, pixmap):
        """兼容原背景面板命名；空图清除图片，仍保留提示和作者。"""
        self._pixmap = QPixmap(pixmap)
        self._image_path = ""
        self.update()

    def set_image_path(self, path):
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return False
        self._pixmap = pixmap
        self._image_path = str(path)
        self.update()
        return True

    def imagePath(self):
        return self._image_path

    def hasImage(self):
        return not self._pixmap.isNull()

    def setHintText(self, text):
        self._hint = str(text)
        self.setAccessibleDescription(self._hint)
        self._update_minimum()
        self.update()

    def hintText(self):
        return self._hint

    def setAuthor(self, text, url):
        self._author_text, self._author_url = str(text), str(url)
        self._update_minimum()
        self.update()

    def setOpenExternalLinks(self, enabled):
        self._open_external_links = bool(enabled)

    def _document(self, text, width):
        document = QTextDocument()
        document.setDefaultFont(self._text_font)
        document.setDocumentMargin(0)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        document.setDefaultTextOption(option)
        document.setPlainText(text)
        document.setTextWidth(max(1, width))
        return document

    def _text_heights(self, width):
        return (self._document(self._hint, width - 32).size().height(),
                self._document(self._author_text, width - 16).size().height())

    def _update_minimum(self):
        hint_height, author_height = self._text_heights(self.width())
        # 提示再长也必须为立绘保留 64px，并让父布局获知真实最小高度。
        self.setMinimumHeight(int(hint_height + author_height + 114))
        self.updateGeometry()

    def sizeHint(self):
        return QSize(132, max(416, self.minimumHeight()))

    def minimumSizeHint(self):
        hint_height, author_height = self._text_heights(120)
        return QSize(120, int(hint_height + author_height + 114))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_minimum()

    def layoutMetrics(self):
        """返回真实绘图区，供截图检查和布局断言；不依赖任何覆盖子控件。"""
        width, height = self.width(), self.height()
        hint_height, author_height = self._text_heights(width)
        author = QRectF(8, height - 10 - author_height, width - 16, author_height)
        bubble = QRectF(8, author.top() - 8 - hint_height - 14, width - 16, hint_height + 14)
        portrait = QRectF(1, 0, width - 1, max(0, bubble.top() - 12))
        return {"portrait": portrait, "bubble": bubble,
                "hint": bubble.adjusted(8, 7, -8, -7), "author": author}

    def _draw_document(self, painter, text, rect, color, centered=False):
        document = self._document(text, rect.width())
        if centered:
            option = document.defaultTextOption()
            option.setAlignment(Qt.AlignHCenter)
            document.setDefaultTextOption(option)
        document.setDefaultStyleSheet(f"body {{ color: {color}; }}")
        # QTextDocument 的纯文本颜色通过默认格式设置，不把用户文字当 HTML。
        from PyQt5.QtGui import QAbstractTextDocumentLayout
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette.setColor(context.palette.Text, QColor(color))
        painter.save()
        painter.translate(rect.topLeft())
        document.documentLayout().draw(painter, context)
        painter.restore()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        gradient = QLinearGradient(0, 0, 0, self.height())
        for position, color in ((0, "#FDE6EE"), (.6, "#FFF2EC"), (1, "#FFF6E2")):
            gradient.setColorAt(position, QColor(color))
        painter.fillRect(self.rect(), gradient)
        painter.setPen(QPen(QColor(theme.LINE), 1))
        painter.drawLine(0, 0, 0, self.height())
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 180))
        for y in range(8, self.height(), 16):
            for x in range(8, self.width(), 16):
                painter.drawEllipse(QRectF(x - 1.5, y - 1.5, 3, 3))
        areas = self.layoutMetrics()
        portrait = areas["portrait"]
        if self.hasImage() and portrait.height() > 0:
            # 放大上半身，保留头顶和面部；侧边与下摆仅在独立立绘区裁切。
            image_height = max(portrait.height() * 1.28, portrait.width() * 3.2)
            image_width = image_height * self._pixmap.width() / self._pixmap.height()
            target = QRectF((self.width() - image_width) / 2, 0, image_width, image_height)
            painter.save()
            painter.setClipRect(portrait)
            painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))
            painter.restore()
        elif not self.hasImage():
            self._draw_document(painter, "日奈正在休息\n（立绘暂不可用）",
                                portrait.adjusted(10, 18, -10, -10), theme.INK_2)
        bubble = areas["bubble"]
        painter.setBrush(QColor(theme.CARD))
        painter.setPen(QPen(QColor(theme.LINE_STRONG), 1))
        path = QPainterPath()
        path.addRoundedRect(bubble, 8, 8)
        tail = QPainterPath()
        middle = bubble.center().x()
        tail.moveTo(middle - 5, bubble.top() + 1)
        tail.lineTo(middle, bubble.top() - 5)
        tail.lineTo(middle + 5, bubble.top() + 1)
        tail.closeSubpath()
        painter.drawPath(path.united(tail))
        self._draw_document(painter, self._hint, areas["hint"], theme.INK_2)
        self._draw_document(painter, self._author_text, areas["author"], theme.VIOLET_700, centered=True)
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(theme.VIOLET), 1, Qt.DotLine))
            painter.drawRoundedRect(areas["author"].adjusted(-2, -2, 2, 2), 3, 3)

    def mouseMoveEvent(self, event):
        over_link = self.layoutMetrics()["author"].contains(event.pos())
        self.setCursor(Qt.PointingHandCursor if over_link else Qt.ArrowCursor)
        self.setToolTip(self._author_url if over_link else self._hint)
        event.ignore()

    def mousePressEvent(self, event):
        self._link_pressed = event.button() == Qt.LeftButton and self.layoutMetrics()["author"].contains(event.pos())
        if self._link_pressed:
            self.setFocus(Qt.MouseFocusReason)
            event.accept()
        else:
            event.ignore()

    def mouseReleaseEvent(self, event):
        activate = self._link_pressed and event.button() == Qt.LeftButton and self.layoutMetrics()["author"].contains(event.pos())
        self._link_pressed = False
        if activate:
            self._activate_author()
            event.accept()
        else:
            event.ignore()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self._activate_author()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _activate_author(self):
        if self.isEnabled():
            self.linkActivated.emit(self._author_url)
            if self._open_external_links:
                if not QDesktopServices.openUrl(QUrl(self._author_url)):
                    self.linkOpenFailed.emit(self._author_url)
