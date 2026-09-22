import unittest
from types import SimpleNamespace
from unittest.mock import patch

from token_manager.constants import DEFAULT_ACCOUNT_PURCHASE_URL
from token_manager.gui_common import GUICommonMixin


class PurchaseEntryTest(unittest.TestCase):
    def test_purchase_entry_uses_fixed_url_and_updates_status(self):
        status = SimpleNamespace(value="")
        status.set = lambda value: setattr(status, "value", value)
        app = SimpleNamespace(
            status_var=status,
            log=lambda message, level="info": setattr(app, "last_log", (message, level)),
        )

        with patch("token_manager.gui_common.webbrowser.open", return_value=True) as opened:
            GUICommonMixin.open_account_purchase_page(app)

        opened.assert_called_once_with(DEFAULT_ACCOUNT_PURCHASE_URL, new=2)
        self.assertEqual(status.value, "已打开速刷号购买页面")
        self.assertIn(DEFAULT_ACCOUNT_PURCHASE_URL, app.last_log[0])
        self.assertEqual(app.last_log[1], "info")

    def test_visible_navigation_uses_purchase_label_but_internal_preview_remains(self):
        from pathlib import Path

        layout = Path(__file__).parents[1] / "token_manager" / "gui_layout.py"
        text = layout.read_text(encoding="utf-8")
        self.assertIn('action("速刷号购买", self.open_account_purchase_page, 2, 2)', text)
        self.assertIn("text='速刷号购买'", text)
        self.assertIn("command=self.open_account_purchase_page", text)
        self.assertIn('self.convert_tab = ttk.Frame', text)
        self.assertIn('("导出为文件", self.export_preview_file)', text)


if __name__ == "__main__":
    unittest.main()
