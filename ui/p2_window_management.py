"""窗口可达性辅助；拟移入 ui，不依赖 Qt、不改系统 DPI。"""


def _intersection(a, b):
    x, y, w, h = a
    bx, by, bw, bh = b
    return max(0, min(x + w, bx + bw) - max(x, bx)) * max(0, min(y + h, by + bh) - max(y, by))


def _choose_area(rect, areas):
    x, y, w, h = rect
    cx, cy = x + w / 2, y + h / 2
    # 优先中心所在屏，其次交集最多的屏；屏已拔除时取最近工作区。
    def score(area):
        ax, ay, aw, ah = area
        inside = ax <= cx < ax + aw and ay <= cy < ay + ah
        distance = max(ax - cx, 0, cx - (ax + aw)) ** 2 + max(ay - cy, 0, cy - (ay + ah)) ** 2
        return inside, _intersection(rect, area), -distance
    return max(areas, key=score)


def plan_subwindow_position(owner_rect, window_rect, workareas, *, first_open):
    """输入均为 Qt 逻辑像素 (x, y, width, height)，返回窗口左上角。

    首次相对 owner 居中；已有窗只在标题拖动区不可达时最小夹取，绝不重居中。
    负坐标按原值处理；超大窗首次将左上角放入工作区，保证标题可拖动。
    """
    areas = [tuple(area) for area in workareas if area[2] > 0 and area[3] > 0]
    x, y, width, height = window_rect
    if not areas:
        return x, y
    if not first_open:
        for ax, ay, aw, ah in areas:
            title_height = min(32, height, ah)
            visible_width = max(0, min(x + width, ax + aw) - max(x, ax))
            if (ay <= y and y + title_height <= ay + ah
                    and visible_width >= min(120, width, aw)):
                return x, y
    area = _choose_area(owner_rect if first_open else window_rect, areas)
    ax, ay, aw, ah = area
    if first_open:
        ox, oy, ow, oh = owner_rect
        x, y = ox + (ow - width) // 2, oy + (oh - height) // 2
    # 大于工作区时对齐该轴起点，不缩放窗口或更改紧凑布局尺寸。
    x = ax if width > aw else min(max(x, ax), ax + aw - width)
    y = ay if height > ah else min(max(y, ay), ay + ah - height)
    return x, y


def position_subwindow(owner, window, *, first_open):
    """只执行定位；owner、显示、事务接线和激活仍由主窗管理。"""
    screen = owner.screen()
    if screen is None:
        return
    screens = list(screen.virtualSiblings()) or [screen]
    def geometry(rect):
        return rect.x(), rect.y(), rect.width(), rect.height()
    frame = window.frameGeometry()
    x, y = plan_subwindow_position(
        geometry(owner.frameGeometry()), geometry(frame),
        [geometry(item.availableGeometry()) for item in screens], first_open=first_open,
    )
    if (x, y) != (frame.x(), frame.y()):
        window.move(window.x() + x - frame.x(), window.y() + y - frame.y())
