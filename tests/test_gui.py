import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from token_manager.config import default_config
from token_manager.gui import TokenManagerGUI
from token_manager.gui_widgets import (
    CheckList,
    HoverTooltip,
    ModernScrollbar,
    UsageTreeview,
    center_window,
)


class LayoutTest(unittest.TestCase):
    def test_minimize_hides_transient_overlay_windows_and_restores_bars(self):
        root = tk.Tk()
        root.geometry("640x320+0+0")
        try:
            button = tk.Button(root, text="操作")
            button.pack()
            tooltip = HoverTooltip(button, "功能说明", delay=1000)

            tree = UsageTreeview(
                root,
                columns=("name", "quota7"),
                show="headings",
                height=4,
            )
            tree.heading("name", text="账号")
            tree.heading("quota7", text="7d已用")
            tree.column("name", width=320)
            tree.column("quota7", width=180)
            tree.pack(fill="both", expand=True)
            tree.insert("", "end", values=("account@example.test", "42.0%"))

            root.update()
            tree._draw_bars()
            root.update_idletasks()
            self.assertTrue(any(bar.winfo_ismapped() for bar in tree._bar_widgets))

            tooltip._enter()
            self.assertIsNotNone(tooltip.job)
            root.iconify()
            root.update()
            self.assertEqual(root.state(), "iconic")
            self.assertIsNone(tooltip.job)
            self.assertIsNone(tooltip.tip)
            self.assertFalse(any(bar.winfo_ismapped() for bar in tree._bar_widgets))

            for _ in range(3):
                root.deiconify()
                root.update()
                root.update_idletasks()
                self.assertTrue(
                    any(bar.winfo_ismapped() for bar in tree._bar_widgets)
                )
                tooltip._show()
                self.assertIsNotNone(tooltip.tip)
                root.iconify()
                root.update()
                self.assertEqual(root.state(), "iconic")
                self.assertIsNone(tooltip.tip)
                self.assertFalse(
                    any(bar.winfo_ismapped() for bar in tree._bar_widgets)
                )
        finally:
            for event in root.tk.call("after", "info"):
                root.after_cancel(event)
            root.destroy()

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
                    patch("token_manager.gui_auth.CredentialVault") as vault,
                ):
                    vault.return_value.load.return_value = {}
                    app = TokenManagerGUI(root)
                    def descendants(widget):
                        for child in widget.winfo_children():
                            yield child
                            yield from descendants(child)
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
                            "schedulable": i % 2 == 0,
                        }
                        for i in range(16)
                    ]
                    recreated_local = {
                        'email': 'account0@example.test',
                        'account_id': 'workspace-0',
                        'sub2api_recovery': {'remote_id': 999},
                    }
                    app.sub2api_records[0]['credentials'] = {
                        'email': 'account0@example.test',
                        'chatgpt_account_id': 'workspace-0',
                    }
                    self.assertEqual(app.local_remote_record(recreated_local)['id'], 1)
                    app.populate_sub2api_tree()
                    root.update_idletasks()
                    self.assertEqual(len(app.sub2api_tree.get_children()), 16)
                    self.assertEqual(app.sub2api_tree.set(app.sub2api_tree.get_children()[0], 'scheduling'), '调度中')
                    self.assertEqual(app.sub2api_tree.set(app.sub2api_tree.get_children()[1], 'scheduling'), '已关闭')
                    root.tk.call(app.sub2api_tree.heading('scheduling','command'))
                    self.assertEqual(app.sub2api_sort_column, 'scheduling')
                    root.tk.call(app.sub2api_tree.heading('id','command'))
                    self.assertNotIn('CPA', [app.right_notebook.tab(tab, 'text') for tab in app.right_notebook.tabs()])
                    app.sub2api_group_filters = {'GPT PLUS', 'GPT PRO'}
                    self.assertEqual(app.sub2api_type_filter_var.get(),'oauth')
                    mixed = [{'email':'a', 'group_names':['GPT PLUS'],'type':'oauth'}, {'email':'b','group_names':['GPT PRO'],'type':'oauth'}, {'email':'c','group_names':['Other'],'type':'oauth'}, {'email':'api','group_names':['GPT PLUS'],'type':'apikey'}]
                    self.assertEqual([r['email'] for r in app.filter_sub2api_records(mixed)], ['a','b'])
                    app.sub2api_group_filters.clear()
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
                    self.assertIn(str(app.log_panel), app.main_vertical_pane.panes())
                    self.assertFalse(hasattr(app,'info_notebook'))
                    self.assertTrue(app.log_text.winfo_ismapped())
                    self.assertTrue(app.account_heading_stats.winfo_ismapped())
                    # Header command performs a real numeric toggle, not string sorting.
                    root.tk.call(app.sub2api_tree.heading('id','command'))
                    self.assertEqual(app.filtered_sub2api_records[0]['id'],16)
                    root.tk.call(app.sub2api_tree.heading('id','command'))
                    self.assertEqual(app.filtered_sub2api_records[0]['id'],1)
                    app.sub2api_records[0]['extra']={'codex_5h_used_percent':25,'codex_7d_used_percent':80}
                    app.populate_sub2api_tree()
                    root.update_idletasks()
                    bars=[b for b in app.sub2api_tree._bar_widgets if b.winfo_ismapped()]
                    self.assertTrue(bars)
                    self.assertTrue(any(b.find_all() for b in bars))
                    app.token_tree.insert(
                        "",
                        "end",
                        iid="local-account",
                        values=(
                            "local@example.test",
                            "Plus",
                            "56.0%",
                            "未过期",
                            "1小时",
                            "已上传",
                            "正常",
                        ),
                    )
                    root.update()
                    app.token_tree.selection_set("local-account")
                    root.update()
                    local_bar = next(
                        bar
                        for bar in app.token_tree._bar_widgets
                        if bar.winfo_ismapped()
                    )
                    self.assertEqual(local_bar.cget("background"), app.palette["primary_soft"])
                    modern_scrollbars = [
                        widget
                        for widget in descendants(app.account_panel)
                        if isinstance(widget, ModernScrollbar)
                    ]
                    self.assertEqual(len(modern_scrollbars), 2)
                    self.assertEqual(
                        {widget.cget("style") for widget in modern_scrollbars},
                        {
                            "Modern.Horizontal.TScrollbar",
                            "Modern.Vertical.TScrollbar",
                        },
                    )
                    style = __import__('tkinter.ttk', fromlist=['Style']).Style(root)
                    vertical_layout = style.layout("Modern.Vertical.TScrollbar")
                    horizontal_layout = style.layout("Modern.Horizontal.TScrollbar")
                    self.assertNotIn("arrow", str(vertical_layout).lower())
                    self.assertNotIn("arrow", str(horizontal_layout).lower())
                    app.open_sub2api_upload_settings()
                    root.update_idletasks()
                    dialogs = [
                        w for w in root.winfo_children() if isinstance(w, tk.Toplevel)
                    ]
                    self.assertEqual(len(dialogs), 1)
                    owner = dialogs[0]
                    variables = {'group_ids':tk.StringVar(value='2'), 'proxy_id':tk.StringVar(value='8')}
                    app.show_sub2api_option_picker(owner, {
                        'groups':[{'id':2,'name':'PLUS','platform':'openai','status':'active'}],
                        'proxies':[{'id':8,'name':'proxy A','status':'active'},{'id':9,'name':'proxy B','status':'active'}]}, variables)
                    root.update_idletasks()
                    picker = next(w for w in owner.winfo_children() if isinstance(w,tk.Toplevel))
                    proxy_checklist = next(w for w in descendants(picker) if isinstance(w,CheckList) and 9 in w.variables)
                    proxy_checklist.variables[9].set(True)
                    next(w for w in descendants(picker) if w.winfo_class()=='TButton' and w.cget('text')=='应用选择').invoke()
                    self.assertEqual(variables['proxy_id'].get(),'8,9')
                    dialogs[0].destroy()
                    centered = tk.Toplevel(root)
                    centered.geometry("200x100+0+0")
                    center_window(centered, root)
                    root.update_idletasks()
                    expected_x = root.winfo_rootx() + (root.winfo_width() - centered.winfo_width()) // 2
                    expected_y = root.winfo_rooty() + (root.winfo_height() - centered.winfo_height()) // 2
                    self.assertAlmostEqual(centered.winfo_rootx(), expected_x, delta=25)
                    self.assertAlmostEqual(centered.winfo_rooty(), expected_y, delta=25)
                    centered.destroy()
                    app.edit_multiple_sub2api_remote([app.sub2api_records[0], app.sub2api_records[1]])
                    root.update_idletasks()
                    bulk_dialog = next(
                        w for w in root.winfo_children()
                        if isinstance(w, tk.Toplevel) and w.title().startswith("批量编辑远端账号")
                    )
                    bulk_entries = [
                        w for w in descendants(bulk_dialog)
                        if w.winfo_class() == "TEntry"
                    ]
                    self.assertEqual([entry.get() for entry in bulk_entries[:3]], ["不修改"] * 3)
                    bulk_dialog.destroy()
                    checklist = CheckList(root, [(2, 'PLUS'), (24, 'PRO')], [2])
                    checklist.variables[24].set(True)
                    self.assertEqual(checklist.selected(), [2,24])
                    checklist.variables[2].set(False)
                    self.assertEqual(checklist.selected(), [24])
                    checklist.destroy()
                    self.assertEqual(style.lookup('TCombobox', 'selectforeground', ('readonly',)), app.palette['text'])
                    sample = __import__('tkinter.ttk', fromlist=['Combobox']).Combobox(root, values=['上下文池','关闭'], state='readonly')
                    popup = root.tk.call('ttk::combobox::PopdownWindow', str(sample))
                    listbox = str(popup) + '.f.l'
                    self.assertEqual(root.tk.call(listbox,'cget','-foreground'),app.palette['text'])
                    self.assertEqual(root.tk.call(listbox,'cget','-background'),app.palette['card'])
                    sample.destroy()
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
                        self.assertGreaterEqual(app.sub2api_tree.winfo_height(), 140)
                        self.assertGreaterEqual(app.log_text.winfo_height(),100)
                        self.assertLessEqual(app.log_text.winfo_rooty()+app.log_text.winfo_height(),root.winfo_rooty()+root.winfo_height())
                        self.assertGreaterEqual(app.workspace_actions.winfo_x(), app.workspace_nav.winfo_reqwidth())
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
