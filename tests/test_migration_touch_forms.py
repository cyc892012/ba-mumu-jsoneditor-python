"""触控迁移回归：硬件边界替身、真实窗口回调、隐藏渲染与事务失败保护。"""
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt5.QtCore import Qt, QRect, QEvent
from PyQt5.QtGui import QFocusEvent, QKeyEvent
from PyQt5.QtWidgets import QApplication, QAbstractItemView, QLineEdit
from PyQt5.QtTest import QTest
from ui import adb_touch_form as adb_ui
from ui import quick_touch_form as quick_ui
from core.json_editor import calculate_coordinates, CoordinateConversionDirection

APP = QApplication.instance() or QApplication([])
EVIDENCE = Path(tempfile.gettempdir()) / "touch_test_evidence"


class FakeListener:
    def __init__(self):
        self.is_listening = False
        self.callbacks = []
        self.depth = 0
        self.fail = False

    def on_key_down(self, callback):
        self.callbacks.append(callback)

    def start_listening(self):
        self.is_listening = not self.fail
        return self.is_listening

    def stop_listening(self):
        self.is_listening = False

    def pause(self):
        self.depth += 1

    def resume(self):
        self.depth = max(0, self.depth - 1)

    def emit(self, text, vk):
        if self.is_listening and self.depth == 0:
            for callback in self.callbacks:
                callback(text, vk)


class FakeHandler:
    def __init__(self):
        self.once = self.multi = False
        self.depth = 0
        self.once_callbacks = []
        self.multi_callbacks = []

    @property
    def is_key_creation_active(self):
        return self.once or self.multi

    def on_key_captured_once(self, callback):
        self.once_callbacks.append(callback)

    def on_key_captured_continuously(self, callback):
        self.multi_callbacks.append(callback)

    def set_listen_once_mode(self, value):
        self.once = value
        if value:
            self.multi = False

    def set_listen_continuously_mode(self, value):
        self.multi = value
        if value:
            self.once = False

    def stop_all_listening(self):
        self.once = self.multi = False

    def pause(self):
        self.depth += 1

    def resume(self):
        self.depth = max(0, self.depth - 1)

    def emit_once(self, text, vk):
        if self.once and not self.depth:
            self.once = False  # SH-01 在派发前消费单次
            for callback in self.once_callbacks:
                callback(text, vk)


class Stream:
    def __init__(self):
        self.lines = queue.Queue()
        self.closed = False

    def __iter__(self):
        while True:
            value = self.lines.get()
            if value is None:
                return
            if isinstance(value, Exception):
                raise value
            yield value

    def close(self):
        if not self.closed:
            self.closed = True
            self.lines.put(None)


class FakeAdb:
    instances = []
    fail = None
    size = "1280x720"

    def __init__(self, path, serial):
        self.serial = serial
        self.process = SimpleNamespace(stdout=Stream())
        self.__class__.instances.append(self)

    def connect(self):
        return self.fail != "connect"

    def get_screen_size(self):
        return self.size

    def get_mouse_max_values(self):
        return 65535, 65535

    def get_sensor_max_values(self):
        return 0, 0

    def start_getevent(self):
        return None if self.fail == "getevent" else self.process

    def stop_getevent(self):
        self.process.stdout.close()

    def disconnect(self):
        self.stop_getevent()


class Document:
    def __init__(self):
        self.text = '{"keymaps": []}'
        self.writes = []

    def write(self, text):
        self.text = text
        self.writes.append(text)
        return True


def pump():
    APP.processEvents()
    QTest.qWait(5)
    APP.processEvents()


class TouchMigrationTests(unittest.TestCase):
    def setUp(self):
        self.patches = []
        for target, name, value in ((quick_ui, "KeyboardListener", FakeListener),
                                    (quick_ui.ConfigManager, "shared", lambda: SimpleNamespace(fx="1280", fy="720")),
                                    (quick_ui, "get_cursor_pos", lambda: (960, 540)),
                                    (adb_ui, "AdbClient", FakeAdb),
                                    (adb_ui.MessageDialog, "information", lambda *a, **k: True)):
            item = patch.object(target, name, value)
            item.start()
            self.patches.append(item)
        screen = SimpleNamespace(geometry=lambda: QRect(0, 0, 1920, 1080))
        item = patch.object(QApplication, "screenAt", return_value=screen)
        item.start()
        self.patches.append(item)
        FakeAdb.instances = []
        FakeAdb.fail, FakeAdb.size = None, "1280x720"
        self.adb = adb_ui.AdbTouchForm()
        self.adb.setAttribute(Qt.WA_DontShowOnScreen)
        self.adb._shown_resolution_msg = True
        self.adb._adb_path_lbl.setText(adb_ui.__file__)
        self.adb._port_edit.setText("16384")
        self.handler = FakeHandler()
        self.adb.set_keyboard_handler(self.handler)
        self.doc = Document()
        self.adb.set_main_window_bridge(lambda: self.doc.text, self.doc.write)
        self.quick = quick_ui.QuickTouchForm()
        self.quick.setAttribute(Qt.WA_DontShowOnScreen)
        self.qdoc = Document()
        self.quick.set_json_data(self.qdoc.text)
        self.quick.set_json_path(str(EVIDENCE / "runtime/in_memory.json"))
        self.quick.set_sync_callback(self.qdoc.write)
        self.adb.show()
        self.quick.show()
        pump()
        for edit in self.quick.findChildren(QLineEdit):
            QApplication.sendEvent(edit, QFocusEvent(QEvent.FocusOut))

    def tearDown(self):
        self.adb.close()
        self.quick.close()
        for client in FakeAdb.instances:
            client.stop_getevent()
        thread = getattr(self.adb, "_collection_thread", None)
        if thread:
            thread.join(timeout=1)
        pump()
        self.adb.deleteLater()
        self.quick.deleteLater()
        for item in reversed(self.patches):
            item.stop()
        pump()

    def connected(self):
        self.assertTrue(self.adb._connect_adb())
        self.assertTrue(self.adb._is_collecting)
        return self.adb._collection_thread

    def point(self, x=480.123456789123, y=230.987654321987):
        self.adb._on_touch_detected(SimpleNamespace(x=x, y=y, is_press=False))
        self.adb._drain_touch_queue()

    def test_adb_manual_port_and_validation(self):
        self.adb._port_combo.addItem("历史", adb_ui.MuMuPortItem("16384"))
        self.adb._port_edit.setText("16416")
        self.connected()
        self.assertEqual(FakeAdb.instances[-1].serial, "127.0.0.1:16416")
        self.adb._stop_collection()
        for value in ("", "abc", "0", "65536", "１２３"):
            self.adb._port_edit.setText(value)
            count = len(FakeAdb.instances)
            self.assertFalse(self.adb._connect_adb())
            self.assertEqual(len(FakeAdb.instances), count)

    def test_adb_history_clear_default_and_empty_path_save(self):
        config = SimpleNamespace(adb_path="old.exe", adb_port="16416", adb_ports_history="16384,16384,16416", save=lambda: None)
        self.adb.set_config_getter(lambda: config)
        self.adb._load_config()
        self.adb._load_config()
        self.assertEqual(self.adb._port_combo.count(), 2)
        self.assertEqual(self.adb._port_edit.text(), "16416")
        self.adb._adb_path_lbl.clear()
        self.adb._save_config()
        self.assertEqual(config.adb_path, "")
        self.adb._on_clear_settings()
        self.assertEqual(self.adb._get_selected_port(), "16384")
        self.assertEqual(config.adb_ports_history, "16384")

    def test_adb_stale_path_falls_back_and_known_instance_name(self):
        self.adb._adb_path_lbl.setText("missing_old_adb.exe")
        self.adb._cached_instances = [adb_ui.MuMuPortItem("16384", "已知实例")]
        with patch.object(self.adb, "_resolve_adb_path", return_value=adb_ui.__file__) as resolve:
            self.connected()
        resolve.assert_called_once()
        self.assertIn("已知实例", self.adb._port_combo.currentText())

    def test_adb_stop_reconnect_clears_rows_queue_and_old_eof(self):
        first = self.connected()
        old_session = self.adb._session_id
        self.point()
        self.adb._stop_collection()
        first.join(timeout=1)
        self.assertFalse(self.adb._is_connected)
        self.assertTrue(self.adb._connect_btn.isEnabled())
        self.connected()
        self.assertEqual(self.adb._tbl.rowCount(), 0)
        self.adb._on_collection_ended(old_session, 55, "旧会话异常")
        self.assertTrue(self.adb._is_collecting)
        self.assertNotIn("旧会话", self.adb._status_label.text())

    def test_adb_eof_and_read_exception_reset_real_state(self):
        for error in (None, OSError("受控读取故障")):
            thread = self.connected()
            self.adb._adapter.process.stdout.lines.put(error)
            thread.join(timeout=1)
            pump()
            self.assertFalse(self.adb._is_collecting)
            self.assertFalse(self.adb._is_connected)
            self.assertTrue(self.adb._connect_btn.isEnabled())
            self.assertIn("异常" if error else "线程已结束", self.adb._status_label.text())

    def test_adb_connect_and_start_failure_release_adapter_and_size_one(self):
        for failure in ("connect", "getevent"):
            FakeAdb.fail = failure
            self.assertFalse(self.adb._connect_adb())
            self.assertIsNone(self.adb._adapter)
            self.assertFalse(self.adb._is_connected)
        FakeAdb.fail, FakeAdb.size = None, "1x720"
        self.assertFalse(self.adb._connect_adb())
        self.assertIn("大于 1", self.adb._status_label.text())

    def test_adb_close_reopen_timer_and_mode_consistency(self):
        self.connected()
        self.adb._gen_multi_cb.setChecked(True)
        self.adb.close()
        self.adb.show()
        self.assertTrue(self.adb._ui_timer.isActive())
        self.assertFalse(self.adb._gen_multi_cb.isChecked())
        self.assertFalse(self.handler.is_key_creation_active)

    def test_adb_flat_manager_json_invalid_bytes_and_subprocess_flags(self):
        with patch.object(self.adb, "_find_mumu_manager_path", return_value="fake.exe"), \
                patch.object(self.adb, "_execute_mumu_manager", return_value=b'{"adb_port":16384,"name":"A\\ufffd"}'):
            items = self.adb._query_mumu12_instances("stub")
        self.assertEqual(items[0].port, "16384")
        result = SimpleNamespace(stdout=b"unauthorized\xff", stderr=b"\xfe")
        with patch.object(adb_ui.subprocess, "run", return_value=result) as run:
            self.assertEqual(self.adb._try_adb_connect("fake.exe", "16384"), (False, True))
        self.assertEqual(run.call_args.kwargs["errors"], "replace")
        with patch.object(adb_ui.subprocess, "run", return_value=result):
            self.assertIn("unauthorized", self.adb._execute_mumu_manager("fake.exe", "info -v all"))

    def test_adb_wmic_missing_falls_back_without_new_dependency(self):
        manager_path = os.path.join(
            tempfile.gettempdir(), "mumu", "nx_main", "MuMuNxService.exe"
        )
        result = SimpleNamespace(stdout=manager_path + "\n")
        with patch.object(adb_ui.subprocess, "run", side_effect=[FileNotFoundError(), result]) as run, \
                patch.object(adb_ui.os.path, "isfile", return_value=True):
            path = self.adb._find_adb_via_process("MuMuNxService")
        self.assertTrue(path.endswith("adb.exe"))
        self.assertEqual(run.call_args_list[1].args[0][0], "powershell.exe")

    def test_adb_unauthorized_survives_scan_finished_and_status_refresh(self):
        self.adb._on_detect_error("请允许 USB 调试", "unauth")
        self.adb._on_detect_status("尝试下一个端口")
        self.adb._on_detect_finished([adb_ui.MuMuPortItem("16384")])
        self.assertIn("允许 USB 调试", self.adb._status_label.text())

    def test_adb_row_selection_apply_precision_and_aggregation_same_sample(self):
        self.connected()
        self.point()
        row = self.adb._row_data[0]
        self.point(row.screen_x + 0.000001, row.screen_y + 0.000001)
        self.assertEqual(self.adb._tbl.rowCount(), 1)
        self.assertIs(self.adb._row_data[0], row)
        self.assertEqual(row.hit_count, 2)
        self.assertEqual(row.rel_x, row.screen_x / 1279)
        results = []
        self.adb.set_apply_coord_callback(lambda x, y: results.append((x, y)))
        self.adb._on_apply_to_main()
        self.assertIn("选择", self.adb._status_label.text())
        self.adb._tbl.selectRow(0)
        self.adb._on_apply_to_main()
        self.adb._on_cell_double_click(0, 2)
        self.assertEqual(results, [(f"{row.rel_x:.16f}", f"{row.rel_y:.16f}")] * 2)
        self.assertEqual(self.adb._tbl.selectionBehavior(), QAbstractItemView.SelectRows)

    def test_adb_clear_resets_parser_calibration_and_stale_queue(self):
        self.connected()
        self.adb._parser._seen_max_x = 999
        self.adb._touch_queue.put((1, 20, 20))
        self.adb._on_clear_table()
        self.assertEqual(self.adb._parser._seen_max_x, 0)
        self.assertTrue(self.adb._touch_queue.empty())

    def test_adb_edit_pause_nested_and_no_extra_generation(self):
        self.connected()
        self.point()
        self.adb._gen_multi_cb.setChecked(True)
        edit = self.adb._tbl.cellWidget(0, self.adb.COL_KEY)
        self.handler.pause()
        self.adb.eventFilter(edit, QFocusEvent(QEvent.FocusIn))
        self.assertEqual(self.handler.depth, 2)
        self.assertFalse(self.adb._generate_from_last_captured("G", 71))
        self.adb.eventFilter(edit, QFocusEvent(QEvent.FocusOut))
        self.assertEqual(self.handler.depth, 1)
        self.handler.resume()
        self.assertTrue(self.adb._generate_from_last_captured("G", 71))

    def test_adb_once_failure_rearms_and_success_consumes_only_once(self):
        self.adb._gen_once_cb.setChecked(True)
        self.handler.emit_once("G", 71)
        pump()
        self.assertTrue(self.handler.once)
        self.assertTrue(self.adb._gen_once_cb.isChecked())
        self.connected()
        self.point()
        self.handler.emit_once("G", 71)
        pump()
        self.assertEqual(len(self.doc.writes), 1)
        self.assertFalse(self.adb._gen_once_cb.isChecked())
        self.assertIn("已生成按键", self.adb._status_label.text())
        self.adb._drain_touch_queue()
        self.assertIn("已生成按键", self.adb._status_label.text())

    def test_adb_generate_false_exception_absent_and_row_types(self):
        self.connected()
        self.point()
        data = self.adb._row_data[0]
        data.checked, data.captured_key, data.vk_code = True, "G", 71
        for callback in (None, lambda _: False, lambda _: (_ for _ in ()).throw(OSError("拒绝写盘"))):
            self.adb._write_callback = callback
            self.assertFalse(self.adb._on_generate_selected())
            self.assertNotIn("成功写入", self.adb._status_label.text())
        self.adb._write_callback = self.doc.write
        self.assertTrue(self.adb._on_generate_selected())
        maps = json.loads(self.doc.text)["keymaps"]
        self.assertEqual(maps[0]["icon"]["rel_position"]["rel_x"], float(f"{data.rel_x:.16f}"))

    def test_adb_live_type_getter_and_size_one(self):
        self.connected()
        self.point()
        state = ["Click"]
        self.adb.set_key_type_getter(lambda: state[0])
        state[0] = "Macro"
        self.assertTrue(self.adb._generate_from_last_captured("G", 71))
        self.assertEqual(json.loads(self.doc.text)["keymaps"][0]["type"], "Macro")
        self.adb._screen_width = 1
        self.assertFalse(self.adb._generate_from_last_captured("H", 72))

    def test_adb_queued_once_during_edit_rearms_and_clear_guides(self):
        self.connected()
        self.point()
        self.adb._gen_once_cb.setChecked(True)
        self.handler.emit_once("G", 71)
        edit = self.adb._tbl.cellWidget(0, self.adb.COL_KEY)
        self.adb.eventFilter(edit, QFocusEvent(QEvent.FocusIn))
        pump()
        self.assertTrue(self.handler.once)
        self.assertEqual(self.doc.writes, [])
        self.adb.eventFilter(edit, QFocusEvent(QEvent.FocusOut))
        self.adb._on_clear_table()
        self.assertIn("请先捕获坐标", self.adb._status_label.text())
        self.adb._stop_collection()
        self.assertIn("已停止", self.adb._collection_status_label.text())

    def test_adb_clear_settings_does_not_hide_save_failure(self):
        config = SimpleNamespace(adb_path="", adb_port="16384", adb_ports_history="", save=lambda: False)
        self.adb.set_config_getter(lambda: config)
        self.adb._on_clear_settings()
        self.assertIn("保存失败", self.adb._status_label.text())

    def test_adb_failed_mode_switch_clears_both_modes(self):
        self.adb._gen_multi_cb.setChecked(True)
        with patch.object(self.handler, "set_listen_once_mode", side_effect=OSError("监听拒绝")):
            self.adb._gen_once_cb.setChecked(True)
        self.assertFalse(self.adb._gen_once_cb.isChecked())
        self.assertFalse(self.adb._gen_multi_cb.isChecked())
        self.assertFalse(self.handler.is_key_creation_active)

    def test_quick_no_file_empty_key_no_callback_and_creation_failure(self):
        self.quick._once_cb.setChecked(True)
        self.quick.set_json_path("")
        self.assertFalse(self.quick._on_key_down_ui("G", 71))
        self.assertTrue(self.quick._once_cb.isChecked())
        self.quick.set_json_path("memory.json")
        self.assertFalse(self.quick._on_key_down_ui("", 0))
        self.quick.set_sync_callback(None)
        self.assertFalse(self.quick._on_key_down_ui("G", 71))
        self.quick.set_sync_callback(self.qdoc.write)
        with patch.object(quick_ui, "create_key", return_value=None):
            self.assertFalse(self.quick._on_key_down_ui("G", 71))
        self.assertNotIn("已生成", self.quick._status_perm.text())

    def test_quick_write_failure_keeps_local_json_and_no_apply(self):
        self.quick._once_cb.setChecked(True)
        results = []
        self.quick.set_apply_coord_callback(lambda *args: results.append(args))
        before = self.quick._json_str
        for callback in (lambda _: False, lambda _: (_ for _ in ()).throw(OSError("磁盘拒绝"))):
            self.quick.set_sync_callback(callback)
            self.assertFalse(self.quick._on_key_down_ui("G", 71))
            self.assertEqual(self.quick._json_str, before)
            self.assertTrue(self.quick._once_cb.isChecked())
            self.assertEqual(results, [])

    def test_quick_success_once_stale_queue_continuous_duplicate(self):
        self.quick._once_cb.setChecked(True)
        epoch = self.quick._listen_epoch
        self.assertTrue(self.quick._on_key_down_ui("G", 71, epoch))
        self.assertFalse(self.quick._on_key_down_ui("H", 72, epoch))
        self.assertFalse(self.quick._once_cb.isChecked())
        self.assertEqual(len(self.qdoc.writes), 1)
        self.quick._multi_cb.setChecked(True)
        self.assertFalse(self.quick._on_key_down_ui("G", 71))
        self.assertIn("已存在", self.quick._status_perm.text())

    def test_quick_manual_internal_precision_and_timer_does_not_overwrite(self):
        self.quick._inner_x.setText("12.12345678912345")
        self.quick._inner_y.setText("34.98765432198765")
        self.quick._inner_x.textEdited.emit(self.quick._inner_x.text())
        self.quick._on_timer_tick()
        self.assertEqual(self.quick._inner_x.text(), "12.12345678912345")
        results = []
        self.quick.set_apply_coord_callback(lambda *args: results.append(args))
        self.quick._once_cb.setChecked(True)
        self.assertTrue(self.quick._on_key_down_ui("G", 71))
        self.assertEqual(results, [(f"{12.12345678912345:.16f}", f"{34.98765432198765:.16f}")])

    def test_quick_live_resolution_type_and_local_override(self):
        dimensions, kind = [1280, 720], ["Click"]
        self.quick.set_resolution_getter(lambda: tuple(dimensions))
        self.quick.set_key_type_getter(lambda: kind[0])
        dimensions[:], kind[:] = [1600, 900], ["Macro"]
        self.quick._once_cb.setChecked(True)
        self.assertTrue(self.quick._on_key_down_ui("G", 71))
        self.assertEqual(json.loads(self.qdoc.text)["keymaps"][0]["type"], "Macro")
        self.quick._type_cb.setCurrentIndex(0)
        self.quick._once_cb.setChecked(True)
        self.assertTrue(self.quick._on_key_down_ui("H", 72))
        self.assertEqual(json.loads(self.qdoc.text)["keymaps"][1]["type"], "Click")

    def test_quick_negative_monitor_origin_is_localized(self):
        screen = SimpleNamespace(geometry=lambda: QRect(-1920, -1080, 1920, 1080))
        with patch.object(QApplication, "screenAt", return_value=screen), patch.object(quick_ui, "get_cursor_pos", return_value=(-960, -540)):
            self.quick._once_cb.setChecked(True)
            self.assertTrue(self.quick._on_key_down_ui("G", 71))
        x, y, _ = calculate_coordinates(1920, 1080, 1280, 720, 960, 540, CoordinateConversionDirection.MOUSE_TO_SIMULATOR)
        position = json.loads(self.qdoc.text)["keymaps"][0]["icon"]["rel_position"]
        self.assertEqual(position, {"rel_x": float(f"{x/1279:.16f}"), "rel_y": float(f"{y/719:.16f}")})

    def test_quick_invalid_dimensions_and_tick_error_stop_no_stale_data(self):
        for value in ("1", "", "NaN", "inf", "1920.5"):
            self.quick._manual_desktop = True
            self.quick._res_x.setText(value)
            self.quick._once_cb.setChecked(True)
            self.assertFalse(self.quick._on_timer_tick())
            self.assertIsNone(self.quick._exact_inner)
            self.assertFalse(self.quick._once_cb.isChecked())
        self.quick._res_x.setText("1920.0")
        self.assertTrue(self.quick._on_timer_tick())
        self.quick._once_cb.setChecked(True)
        with patch.object(quick_ui, "get_cursor_pos", side_effect=OSError("鼠标读取失败")):
            self.assertFalse(self.quick._on_timer_tick())
        self.assertFalse(self.quick._keyboard_listener.is_listening)
        self.assertIn("已停止", self.quick._status_perm.text())

    def test_quick_edit_pause_and_start_failure_and_reopen(self):
        self.quick._once_cb.setChecked(True)
        edit = self.quick._inner_x
        self.quick.eventFilter(edit, QFocusEvent(QEvent.FocusIn))
        self.assertFalse(self.quick._on_key_down_ui("G", 71))
        self.assertEqual(self.quick._keyboard_listener.depth, 1)
        self.quick.eventFilter(edit, QFocusEvent(QEvent.FocusOut))
        self.assertEqual(self.quick._keyboard_listener.depth, 0)
        self.quick.close()
        self.quick.show()
        self.assertFalse(self.quick._once_cb.isChecked())
        self.quick._keyboard_listener.fail = True
        self.quick._multi_cb.setChecked(True)
        self.assertFalse(self.quick._multi_cb.isChecked())
        self.assertIn("无法开启", self.quick._status_perm.text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
