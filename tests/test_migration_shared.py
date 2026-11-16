"""迁移共享契约回归；不启动真实监听或窗口。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import json_editor as editor
from core.backup_manager import BackupManager
from input.keyboard_handler import KeyboardBindingHandler
from input.keyboard_listener import KeyboardListener


class FakeListener:
    def __init__(self):
        self.is_listening = False
        self.depth = 0

    def on_key_down(self, callback):
        self.callback = callback

    def start_listening(self):
        self.is_listening = True

    def stop_listening(self):
        self.is_listening = False

    def pause(self):
        self.depth += 1

    def resume(self):
        self.depth -= 1


def click(vk, text="A", x=0, y=0):
    return {"type": "Click", "key": {"virtual_key": vk, "text": text},
            "rel_work_position": {"rel_x": x, "rel_y": y}}


class SharedMigrationTests(unittest.TestCase):
    def test_optional_coordinates_distinguish_zero_and_missing(self):
        value = json.dumps({"keymaps": [click(65)]})
        self.assertEqual(editor.try_read_key_coordinates(value, vk=65), ("0", "0"))
        self.assertIsNone(editor.try_read_key_coordinates(value, vk=66))
        self.assertIsNone(editor.try_read_key_coordinates(json.dumps({"keymaps": [click(65, x=float("nan"))]}), vk=65))

    def test_read_preset_by_vk_preserves_alias_and_macro(self):
        key = {"type": "Macro", "key": {"virtual_key": 49, "text": "D1"},
               "press_actions": ["curve_rel:mouse;(0,0)"], "extra": 12}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preset.json"
            path.write_text(json.dumps({"keymaps": [key]}), encoding="utf-8-sig")
            self.assertEqual(json.loads(editor.read_key_from_file_by_virtual_key(str(path), 49)), key)
            self.assertIsNone(editor.read_key_from_file_by_virtual_key(str(path), 50))

    def test_delete_by_index_keeps_outside_duplicate_vk(self):
        keys = [click(65, x=.1), click(65, x=.9), click(0)]
        value = json.dumps({"keymaps": keys})
        self.assertEqual(json.loads(editor.delete_keys_by_indexes(value, [0, 0, -1, 90]))["keymaps"], keys[1:])

    def test_history_revision_and_notifications_are_reentrant(self):
        manager = BackupManager()
        seen = []
        manager.subscribe_history_changed(lambda: seen.append(manager.get_revision()))
        manager.record_initial("a")
        manager.record_change("b")
        self.assertEqual(manager.peek_snapshot(0), "a")
        self.assertEqual(manager.get_revision(), 2)
        self.assertEqual(manager.undo(), "a")
        self.assertEqual(manager.redo(), "b")
        self.assertEqual(manager.jump_to(0), "a")
        self.assertEqual(seen, [1, 2, 3, 4, 5])
        self.assertIsNone(manager.peek_snapshot(1))
        self.assertIsNone(manager.jump_to(9))
        self.assertEqual(manager.get_revision(), 5)

    def new_handler(self):
        with mock.patch("input.keyboard_handler.KeyboardListener", FakeListener):
            return KeyboardBindingHandler()

    def test_once_consumed_before_callback(self):
        handler = self.new_handler()
        called = []
        handler.on_key_captured_once(lambda *args: called.append(handler.is_key_creation_active))
        handler.set_listen_once_mode(True)
        handler._fire_once("A", 65)
        handler._fire_once("A", 65)
        self.assertEqual(called, [False])

    def test_nested_pause_and_stop_do_not_revive(self):
        handler = self.new_handler()
        called = []
        handler.on_key_captured_continuously(lambda *args: called.append(args))
        handler.set_listen_continuously_mode(True)
        handler.pause()
        handler.pause()
        handler.resume()
        handler._fire_continuously("A", 65)
        self.assertEqual(called, [])
        handler.resume()
        handler._fire_continuously("A", 65)
        self.assertEqual(called, [("A", 65)])
        handler.pause()
        handler.stop_all_listening()
        handler.resume()
        handler.resume()
        self.assertFalse(handler.is_key_creation_active)

    def test_failed_listener_start_is_not_active(self):
        handler = self.new_handler()
        handler._listener_once.start_listening = lambda: None
        handler.set_listen_once_mode(True)
        self.assertFalse(handler.is_key_creation_active)

    def test_physical_hold_and_modal_keys_suppressed_until_release(self):
        listener = KeyboardListener()
        listener._is_listening = True
        called = []
        listener.on_key_down(lambda *args: called.append(args))
        key = SimpleNamespace(vk=65, char="a")
        enter = SimpleNamespace(vk=13, char=None)
        listener._on_press(key)
        listener.pause()
        listener._on_press(enter)
        listener.resume()
        listener._on_press(key)
        listener._on_press(enter)
        self.assertEqual(called, [("A", 65)])
        listener._on_release(key)
        listener._on_press(key)
        self.assertEqual(called, [("A", 65), ("A", 65)])


if __name__ == "__main__":
    unittest.main()
