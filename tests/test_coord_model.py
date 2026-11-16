"""坐标精度验收：实测样本、全格点、随机像素、连续转换及界面真实写入。"""
import json
import math
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from core.coord_model import (rel_to_px, px_to_rel, snap_px, raw_to_px,
                              adb_px_to_rel, landing_hint)
from core.json_editor import (calculate_coordinates_k_to_ck, get_click_keys,
                              find_range_key_values)


class CoordModelTests(unittest.TestCase):
    def test_ten_measured_keys(self):
        samples = [
            ("0", .6629098360655737, .51183970856102, 849, 369, 848.3367, 368.4875),
            ("5", .5010245901639344, .5100182149362478, 641, 367, 640.4992, 366.4903),
            ("6", .21413934426229508, .2568306010928962, 274, 185, 273.7859, 184.7431),
            ("7", .7858606557377049, .273224043715847, 1006, 197, 1005.2141, 196.7264),
            ("8", .2551229508196721, .6885245901639344, 327, 496, 326.7445, 495.3111),
            ("9", .8043032786885246, .7522768670309654, 1030, 542, 1029.1953, 541.2472),
            ("1", 0, 0, 0, 0, 0, 0),
            ("2", 1, 0, 1280, 0, 1279, 0),
            ("3", 0, 1, 0, 720, 0, 719),
            ("4", 1, 1, 1280, 720, 1279, 719),
        ]
        for key, rx, ry, nx, ny, x, y in samples:
            with self.subTest(key=key):
                self.assertEqual(math.floor(rx * 1280 + .5), nx)
                self.assertEqual(math.floor(ry * 720 + .5), ny)
                self.assertLess(abs(rel_to_px(rx, 1280) - x), 1e-3)
                self.assertLess(abs(rel_to_px(ry, 720) - y), 1e-3)

    def test_every_grid_point_roundtrip(self):
        for size in (1280, 1920, 720, 1080, 2560):
            for k in range(size + 1):
                self.assertEqual(px_to_rel(rel_to_px(k / size, size), size), k / size)

    def test_10000_random_targets_per_resolution(self):
        generator = random.Random(20261116)
        for size in (1280, 1920, 720, 1080, 2560):
            for _ in range(10000):
                px = generator.uniform(0, size - 1)
                snapped = snap_px(px, size)
                self.assertLessEqual(abs(snapped - px), (size - 1) / (2 * size) + 1e-9)
                self.assertEqual(rel_to_px(px_to_rel(px, size), size), snapped)

    def test_boundaries_and_clamping(self):
        for size in (1280, 1920, 720, 1080, 2560):
            for p, rel, actual in ((0, 0, 0), (size - 1, 1, size - 1),
                                   (-10, 0, 0), (size + 10, 1, size - 1)):
                self.assertEqual(px_to_rel(p, size), rel)
                self.assertEqual(snap_px(p, size), actual)
            self.assertEqual(rel_to_px(1, size), size - 1)
            self.assertEqual(rel_to_px(-1, size), 0)
            self.assertEqual(rel_to_px(2, size), size - 1)

    def test_half_up_with_float_tolerance(self):
        for size in (1280, 720, 1920):
            self.assertEqual(rel_to_px(848.5 / size, size), rel_to_px(849 / size, size))
            if size > 849:
                self.assertEqual(rel_to_px((848.5 - 5e-10) / size, size), 849 * (size - 1) / size)
                self.assertEqual(rel_to_px(848.4999999 / size, size), 848 * (size - 1) / size)

    def test_requested_664_examples(self):
        for px in (664.1, 664.5):
            self.assertAlmostEqual(snap_px(px, 1280), 664.48046875)
            self.assertEqual(px_to_rel(px, 1280), 665 / 1280)

    def test_invalid_values_are_rejected(self):
        for function in (rel_to_px, px_to_rel, snap_px):
            for value in (float("nan"), float("inf"), -float("inf")):
                with self.assertRaises(ValueError):
                    function(value, 1280)
            for size in (0, 1, -1, 1.5):
                with self.assertRaises(ValueError):
                    function(.5, size)

    def test_adb_math_stays_continuous(self):
        for raw, maximum, size in ((849, 1280, 1280), (369, 720, 720), (1234, 32767, 1920)):
            self.assertEqual(raw_to_px(raw, maximum, size), raw * (size - 1) / maximum)
            self.assertEqual(adb_px_to_rel(123.4567, size), 123.4567 / (size - 1))
        self.assertNotEqual(adb_px_to_rel(664.1, 1280), px_to_rel(664.1, 1280))

    def test_hint_text(self):
        self.assertEqual(landing_hint(664.5, 368.48, 1280, 720),
                         "实际落点 664.48, 368.49（偏差 −0.02, +0.01）")
        self.assertEqual(landing_hint(664.48046875, 368.4875, 1280, 720), "实际落点与输入一致")

    def test_query_and_region_use_real_landing(self):
        key = {"type": "Click", "key": {"text": "A"},
               "icon": {"rel_position": {"rel_x": 664.5 / 1279, "rel_y": .5}},
               "rel_work_position": {"rel_x": 664.5 / 1279, "rel_y": .5}}
        text = json.dumps({"keymaps": [key]})
        self.assertEqual(get_click_keys(text, 1280, 720)[0]["rel_x"], 664.48046875)
        self.assertEqual(len(find_range_key_values((664.47 / 1279, 0), (664.49 / 1279, 1), text)), 1)


class CoordinateConversionTests(unittest.TestCase):
    def assert_grid_near(self, result, ideal, target):
        for value, expected, size in zip(result[:2], ideal, target):
            self.assertEqual(value, snap_px(value, size))
            clipped = max(0, min(size - 1, expected))
            self.assertLessEqual(abs(value - clipped), (size - 1) / (2 * size) + 1e-9)

    def test_equal_aspect_all_directions(self):
        sizes = ((1280, 720), (1920, 1080), (2560, 1440))
        for source in sizes:
            for target in sizes:
                point = (664.1, 368.48)
                ideal = tuple(value * dst / src for value, src, dst in zip(point, source, target))
                result = calculate_coordinates_k_to_ck(*source, *target, *point)
                self.assert_grid_near(result, ideal, target)

    def test_non_equal_aspect_both_directions(self):
        self.assert_grid_near(calculate_coordinates_k_to_ck(1600, 900, 1280, 800, 664.1, 368.48),
                              ((664.1 - 80) / 1.125, 368.48 / 1.125), (1280, 800))
        self.assert_grid_near(calculate_coordinates_k_to_ck(1280, 800, 1600, 900, 664.1, 368.48),
                              (664.1 * 1.125 + 80, 368.48 * 1.125), (1600, 900))

    def test_chain_only_final_stage_snaps(self):
        point = (664.1, 368.48)
        stages = ((1280, 720), (1920, 1080), (2560, 1440), (1600, 900), (1280, 800))
        for source, target in zip(stages[:-2], stages[1:-1]):
            *point, status = calculate_coordinates_k_to_ck(*source, *target, *point, snap=False)
            self.assertEqual(status, 0)
        final = calculate_coordinates_k_to_ck(*stages[-2], *stages[-1], *point)
        self.assert_grid_near(final, ((664.1 * 1.25 - 80) / 1.125, 368.48 * 1.25 / 1.125), stages[-1])

    def test_clipped_edges_stay_valid(self):
        result = calculate_coordinates_k_to_ck(1600, 900, 1280, 800, -10, 1000)
        self.assertEqual(result, (0, 799, -1))


class CoordinateUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from core.config_manager import ConfigManager
        self.temp = tempfile.TemporaryDirectory(prefix="coord_model_")
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict("os.environ", {"MOMO_DATA_DIR": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        ConfigManager.reset_shared()
        self.addCleanup(ConfigManager.reset_shared)
        self.forms = []

    def tearDown(self):
        for form in self.forms:
            form.close()
            form.deleteLater()
        self.app.processEvents()

    def test_main_input_and_relative_display(self):
        from ui.main_window import MainWindow
        with patch.object(MainWindow, "_deferred_init"):
            form = MainWindow()
            self.forms.append(form)
            form.kx_input.setText("664.5")
            form.ky_input.setText("368.48")
            self.assertEqual(float(form.jsx_input.text()), 665 / 1280)
            self.assertIn("偏差 −0.02, +0.01", form._landing_hint.text())
            self.assertEqual(form._landing_hint.toolTip(), form._landing_hint.text())
            form.set_jsxy("0.6629098360655737", "0.51183970856102")
            self.assertEqual(float(form.kx_input.text()), 848.33671875)
            self.assertEqual(float(form.ky_input.text()), 368.4875)
            self.assertTrue(form._on_quick_apply_coord("664.5", "368.48"))
            self.assertEqual(float(form.jsx_input.text()), 665 / 1280)
            form.kx_input.setText("invalid")
            self.assertEqual(form._landing_hint.text(), "请输入完整的有限数坐标")
            self.app.processEvents()

    def test_quick_manual_hint_and_real_write(self):
        from test_migration_touch_forms import FakeListener
        from ui.quick_touch_form import QuickTouchForm
        from PyQt5.QtCore import Qt
        with patch("ui.quick_touch_form.KeyboardListener", FakeListener):
            form = QuickTouchForm()
        self.forms.append(form)
        form.setAttribute(Qt.WA_DontShowOnScreen, True)
        form.set_resolution_getter(lambda: (1280, 720))
        form._inner_x.setText("664.5")
        form._inner_y.setText("368.48")
        form._on_inner_edited("")
        self.assertIn("偏差 −0.02, +0.01", form._status_perm.text())
        form._json_str, form._json_path = '{"keymaps": []}', "isolated.json"
        written = []
        form._sync_cb = lambda text: written.append(text) or True
        form.set_overwrite_guard(lambda *args: True)
        form.show()
        form._once_cb.setChecked(True)
        self.assertTrue(form._on_key_down_ui("A", 65))
        pos = json.loads(written[0])["keymaps"][0]["rel_work_position"]
        self.assertEqual(rel_to_px(pos["rel_x"], 1280), 664.48046875)
        self.assertEqual(rel_to_px(pos["rel_y"], 720), 368.4875)

    def test_region_uses_landing_and_preserves_continuous_boundary(self):
        from ui.range_clear_form import RangeClearForm
        form = RangeClearForm()
        self.forms.append(form)
        form.set_resolution(1280, 720)
        form.set_json_data(json.dumps({"keymaps": [{"key": {"text": "A"},
            "icon": {"rel_position": {"rel_x": 664.5 / 1279, "rel_y": .5}}}]}))
        for edit, value in zip((form._x1, form._y1, form._x2, form._y2), (664.47, 0, 664.49, 719)):
            edit.setText(str(value))
        self.assertEqual(form.get_pending_keys(), [(0, "A")])
        self.assertEqual(form._thumbnail.points[0][0], 664.48046875 / 1279)
        form._x1.setText("664.48046875")
        self.assertEqual(form.get_pending_keys(), [])

    def test_wide_macro_and_click_preview_match_written_landing(self):
        from test_migration_coordinate_forms import CoordinateFormsTest, document, key
        # 复用现有隔离夹具，不继承和重复运行旧测试。
        fixture = CoordinateFormsTest()
        fixture.setUp()
        try:
            form, _ = fixture.wide_form(document(key(65, "A"), key(66, "B", kind="Macro")))
            for target in ((1920, 1080), (1280, 800)):
                form.set_resolution_getter(lambda target=target: target)
                for row, info in enumerate(form._click_keys):
                    form._on_table_click(row, 1)
                    block, _ = form._convert_info(info, target)
                    obj = json.loads(block)
                    if obj["type"] == "Macro":
                        import re
                        rx, ry = map(float, re.search(r"curve_rel:mouse;\(([^,]+),([^)]*)\)", block).groups())
                    else:
                        rx, ry = (obj["rel_work_position"][axis] for axis in ("rel_x", "rel_y"))
                    self.assertAlmostEqual(rel_to_px(rx, target[0]), float(form._wide_x.text()))
                    self.assertAlmostEqual(rel_to_px(ry, target[1]), float(form._wide_y.text()))
                    self.assertTrue(form._landing_hint.text().startswith("实际落点"))
        finally:
            fixture.tearDown()
            fixture.doCleanups()


if __name__ == "__main__":
    unittest.main()
