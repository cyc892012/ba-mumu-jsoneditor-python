"""依据16544实机的增量坐标事件回归触摸生命周期。"""
import unittest

from adb.parser import TouchEventParser


class AdbParserContactTests(unittest.TestCase):
    def setUp(self):
        self.parser = TouchEventParser()
        self.parser.set_screen_size(1280, 720)
        self.parser.set_sensor_scale(720, 1280)
        self.released = []
        self.parser.on_touch_detected(lambda point: self.released.append(point) if not point.is_press else None)

    def feed(self, *events):
        for event in events:
            self.parser.parse_line("/dev/input/event4: " + event)

    def contact(self, x=None, y=None):
        self.feed("EV_ABS ABS_MT_TRACKING_ID 00000001", "EV_KEY BTN_TOUCH DOWN")
        if x is not None:
            self.feed(f"EV_ABS ABS_MT_POSITION_X {x:08x}")
        if y is not None:
            self.feed(f"EV_ABS ABS_MT_POSITION_Y {y:08x}")
        self.feed("EV_SYN SYN_REPORT ffffffff", "EV_ABS ABS_MT_TRACKING_ID 0000c350",
                  "EV_KEY BTN_TOUCH UP", "EV_SYN SYN_REPORT ffffffff")

    def test_real_incremental_event_sequence_keeps_unchanged_axis(self):
        self.contact(0x22a, 0x3b4)
        self.contact(y=0x42a)
        self.assertEqual(len(self.released), 2)
        self.assertAlmostEqual(self.released[0].y, self.released[1].y)
        self.assertGreater(self.released[1].x, self.released[0].x)

    def test_repeated_position_without_axis_events_is_recorded(self):
        self.contact(360, 640)
        self.contact()
        self.contact()
        self.assertEqual(len(self.released), 3)
        self.assertEqual([(p.x, p.y) for p in self.released], [(639.5, 359.5)] * 3)

    def test_single_axis_vertical_change_uses_previous_other_axis(self):
        self.contact(360, 640)
        self.contact(x=180)
        self.assertEqual(len(self.released), 2)
        self.assertAlmostEqual(self.released[1].x, 639.5)
        self.assertAlmostEqual(self.released[1].y, 539.25)

    def test_stray_up_does_not_duplicate_previous_contact(self):
        self.contact(360, 640)
        self.feed("EV_KEY BTN_TOUCH UP", "EV_KEY BTN_TOUCH UP", "EV_ABS ABS_MT_TRACKING_ID ffffffff")
        self.assertEqual(len(self.released), 1)

    def test_standard_tracking_release_and_btn_up_only_emit_once(self):
        self.feed("EV_ABS ABS_MT_TRACKING_ID 00000001", "EV_ABS ABS_MT_POSITION_X 00000168",
                  "EV_ABS ABS_MT_POSITION_Y 00000280", "EV_SYN SYN_REPORT ffffffff",
                  "EV_ABS ABS_MT_TRACKING_ID ffffffff", "EV_KEY BTN_TOUCH UP")
        self.assertEqual(len(self.released), 1)

    def test_unknown_initial_axes_do_not_invent_coordinate(self):
        self.contact()
        self.assertEqual(self.released, [])
