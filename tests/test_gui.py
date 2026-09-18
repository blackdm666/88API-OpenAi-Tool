import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from token_manager.config import default_config
from token_manager.gui import TokenManagerGUI


class LayoutTest(unittest.TestCase):
    def test_remote_rows_have_space_and_configuration_roundtrips(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = default_config()
            cfg.update(tokens_dir=folder + "/tokens", outputs_dir=folder + "/outputs")
            cfg["integrations"]["sub2api"].update(
                concurrency=17, priority=0, codex_fingerprint_mode="device"
            )
            root = tk.Tk()
            root.withdraw()
            errors = []
            root.report_callback_exception = lambda *args: errors.append(args)
            try:
                with (
                    patch("token_manager.gui.load_app_config", return_value=cfg),
                    patch("token_manager.gui_common.save_app_config"),
                ):
                    app = TokenManagerGUI(root)
                    root.geometry("1280x800+0+0")
                    root.deiconify()
                    root.update()
                    app._apply_initial_pane_layout()
                    app.sub2api_groups = [{"id": 2, "name": "GPT PLUS"}]
                    app.sub2api_records = [
                        {
                            "id": i + 1,
                            "email": f"account{i}@example.test",
                            "platform": "openai",
                            "type": "oauth",
                            "group_ids": [2],
                            "group_names": ["GPT PLUS"],
                            "status": "active",
                        }
                        for i in range(16)
                    ]
                    app.populate_sub2api_tree()
                    root.update_idletasks()
                    self.assertEqual(len(app.sub2api_tree.get_children()), 16)
                    self.assertGreaterEqual(app.sub2api_tree.winfo_height(), 200)
                    self.assertTrue(
                        app.sub2api_tree.bbox(app.sub2api_tree.get_children()[0])
                    )
                    self.assertEqual(app.sub2api_tree.xview()[0], 0)
                    settings = app.current_settings()["integrations"]["sub2api"]
                    self.assertEqual(
                        (
                            settings["concurrency"],
                            settings["priority"],
                            settings["codex_fingerprint_mode"],
                        ),
                        (17, 0, "device"),
                    )
                    app.toggle_log_panel()
                    app.toggle_log_panel()
                    root.update_idletasks()
                    self.assertNotIn(str(app.log_panel), app.main_vertical_pane.panes())
                    app.open_sub2api_upload_settings()
                    root.update_idletasks()
                    dialogs = [
                        w for w in root.winfo_children() if isinstance(w, tk.Toplevel)
                    ]
                    self.assertEqual(len(dialogs), 1)
                    dialogs[0].destroy()
                    app.toggle_account_panel()
                    root.update_idletasks()
                    self.assertNotIn(
                        str(app.account_panel), app.main_horizontal_pane.panes()
                    )
                    app.toggle_account_panel()
                    root.update_idletasks()
                    self.assertIn(
                        str(app.account_panel), app.main_horizontal_pane.panes()
                    )
                    for width, height in [(1100, 660), (1440, 900)]:
                        root.geometry(f"{width}x{height}+0+0")
                        root.update()
                        app._apply_initial_pane_layout()
                        root.update_idletasks()
                        self.assertGreaterEqual(app.sub2api_tree.winfo_height(), 180)
                        self.assertTrue(
                            app.sub2api_tree.bbox(app.sub2api_tree.get_children()[0])
                        )
                    self.assertEqual(errors, [])
                    app.config['integrations']['sub2api'].update(api_url='https://old.test', access_token='old-session', refresh_token='old-session-refresh')
                    app.sub2api_url_var.set('https://new.test')
                    self.assertEqual(app.current_settings()['integrations']['sub2api']['access_token'], '')
            finally:
                for event in root.tk.call("after", "info"):
                    root.after_cancel(event)
                root.destroy()


if __name__ == "__main__":
    unittest.main()
