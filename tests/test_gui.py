import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from token_manager.config import default_config
from token_manager.gui import TokenManagerGUI, _apply_window_geometry
from token_manager.gui_widgets import (
    CheckList,
    CheckmarkOption,
    HoverTooltip,
    ModernScrollbar,
    UsageTreeview,
    center_window,
)


class LayoutTest(unittest.TestCase):
    def test_main_window_geometry_uses_centered_work_area(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with patch(
                "token_manager.gui._window_work_area",
                return_value=(100, 40, 1500, 840),
            ):
                _apply_window_geometry(root)
            root.update_idletasks()
            self.assertEqual(root.winfo_width(), 1280)
            self.assertEqual(root.winfo_height(), 752)
            self.assertEqual(root.winfo_x(), 160)
            self.assertEqual(root.winfo_y(), 64)
        finally:
            root.destroy()

    def test_modern_scrollbar_remains_available_when_content_fits(self):
        root = tk.Tk()
        try:
            scrollbar = ModernScrollbar(root, orient="vertical")
            scrollbar.grid(row=0, column=0, sticky="ns")
            root.update()
            scrollbar.set("0", "1")
            root.update_idletasks()
            self.assertTrue(scrollbar.winfo_ismapped())
            scrollbar.set("0", "0.6")
            root.update_idletasks()
            self.assertTrue(scrollbar.winfo_ismapped())
            scrollbar.set("0", "1")
            root.update_idletasks()
            self.assertTrue(scrollbar.winfo_ismapped())
        finally:
            root.destroy()

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
            cfg["auth_proxy"] = "socks5://authorization.test:1080"
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
                    self.assertEqual(app.token_tree.heading("recovery", "text"), "监控")
                    self.assertLessEqual(app.token_tree.column("status", "width"), 60)
                    self.assertLessEqual(app.token_tree.column("recovery", "width"), 80)
                    self.assertGreater(
                        app.main_horizontal_pane.sashpos(0),
                        app.main_horizontal_pane.winfo_width() // 2,
                    )
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
                    self.assertEqual(
                        app.current_settings()["auth_proxy"],
                        "socks5://authorization.test:1080",
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
                    self.assertIn("thumb", str(vertical_layout).lower())
                    self.assertIn("thumb", str(horizontal_layout).lower())
                    vertical_scrollbar = next(
                        widget
                        for widget in modern_scrollbars
                        if "Vertical" in widget.cget("style")
                    )
                    horizontal_scrollbar = next(
                        widget
                        for widget in modern_scrollbars
                        if "Horizontal" in widget.cget("style")
                    )
                    self.assertGreaterEqual(vertical_scrollbar.winfo_width(), 8)
                    self.assertGreaterEqual(horizontal_scrollbar.winfo_height(), 8)
                    remote_scrollbars = [
                        widget
                        for widget in descendants(app.sub2api_tab)
                        if isinstance(widget, ModernScrollbar)
                    ]
                    self.assertEqual(len(remote_scrollbars), 2)
                    remote_vertical = next(
                        widget
                        for widget in remote_scrollbars
                        if "Vertical" in widget.cget("style")
                    )
                    remote_horizontal = next(
                        widget
                        for widget in remote_scrollbars
                        if "Horizontal" in widget.cget("style")
                    )
                    self.assertGreaterEqual(remote_vertical.winfo_width(), 8)
                    self.assertGreaterEqual(remote_horizontal.winfo_height(), 8)
                    self.assertLess(app.sub2api_tree.yview()[1], 1.0)
                    app.sub2api_url_var.set("https://sub2api.example.test")
                    app.sub2api_key_var.set("admin-key")
                    app.sub2api_group_ids_var.set("2,24")
                    app.config["integrations"]["sub2api"]["proxy_id"] = "8"
                    original_run_background = app.run_background

                    def run_inline(_status, worker, on_done):
                        on_done(worker())

                    app.run_background = run_inline
                    with (
                        patch(
                            "token_manager.gui_sub2api_settings.fetch_sub2api_groups",
                            return_value=[
                                {"id": 2, "name": "GPT PLUS", "platform": "openai", "status": "active"},
                                {"id": 24, "name": "GPT PRO", "platform": "openai", "status": "active"},
                            ],
                        ) as fetch_groups,
                        patch(
                            "token_manager.gui_sub2api_settings.fetch_sub2api_proxies",
                            return_value=[
                                {"id": 8, "name": "Plus", "status": "active"},
                                {"id": 9, "name": "Free", "status": "active"},
                            ],
                        ) as fetch_proxies,
                    ):
                        app.open_sub2api_upload_settings()
                        root.update_idletasks()
                    app.run_background = original_run_background
                    fetch_groups.assert_called_once()
                    fetch_proxies.assert_called_once()
                    app.sub2api_url_var.set("")
                    app.sub2api_key_var.set("")
                    dialogs = [
                        w for w in root.winfo_children() if isinstance(w, tk.Toplevel)
                    ]
                    self.assertEqual(len(dialogs), 1)
                    owner = dialogs[0]
                    settings_buttons = [
                        widget
                        for widget in descendants(owner)
                        if widget.winfo_class() == "TButton"
                    ]
                    save_button = next(
                        widget for widget in settings_buttons
                        if widget.cget("text") == "保存配置"
                    )
                    cancel_button = next(
                        widget for widget in settings_buttons
                        if widget.cget("text") == "取消"
                    )
                    for button in (save_button, cancel_button):
                        self.assertTrue(button.winfo_ismapped())
                        self.assertLessEqual(
                            button.winfo_rooty() + button.winfo_height(),
                            owner.winfo_rooty() + owner.winfo_height(),
                        )
                    upload_settings_labels = [
                        str(widget.cget("text"))
                        for widget in descendants(owner)
                        if widget.winfo_class() == "TLabel"
                    ]
                    self.assertIn("添加到分组", upload_settings_labels)
                    self.assertIn("账号是否使用代理", upload_settings_labels)
                    self.assertIn("账号到期时自动暂停", upload_settings_labels)
                    self.assertNotIn("上传分组 ID（逗号分隔）", upload_settings_labels)
                    settings_button_texts = [
                        str(widget.cget("text"))
                        for widget in descendants(owner)
                        if widget.winfo_class() == "TButton"
                    ]
                    self.assertNotIn("读取分组 / 代理", settings_button_texts)
                    self.assertNotIn("多选分组 / 多选代理…", settings_button_texts)
                    dropdowns = [
                        widget
                        for widget in descendants(owner)
                        if widget.winfo_class() == "TMenubutton"
                    ]
                    self.assertEqual(len(dropdowns), 2)
                    dropdown_texts = [str(widget.cget("text")) for widget in dropdowns]
                    self.assertTrue(any("2 个分组" in text for text in dropdown_texts))
                    self.assertTrue(any("是 · #8" in text for text in dropdown_texts))
                    yes_no_combos = [
                        widget
                        for widget in descendants(owner)
                        if widget.winfo_class() == "TCombobox"
                        and tuple(widget.cget("values")) == ("是", "否")
                    ]
                    self.assertEqual(len(yes_no_combos), 1)
                    checkmark_options = [
                        widget
                        for widget in descendants(owner)
                        if isinstance(widget, CheckmarkOption)
                    ]
                    self.assertEqual(len(checkmark_options), 4)
                    self.assertTrue(
                        all(option.indicator.cget("text") == "✓" for option in checkmark_options)
                    )
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
                    auth_proxy_entries = [
                        widget
                        for widget in descendants(app.auth2fa_tab)
                        if widget.winfo_class() == "TEntry"
                        and str(widget.cget("textvariable")) == str(app.auth_proxy_var)
                    ]
                    self.assertEqual(auth_proxy_entries, [])
                    settings_auth_proxy_entries = [
                        widget
                        for widget in descendants(app.settings_tab)
                        if widget.winfo_class() == "TEntry"
                        and str(widget.cget("textvariable")) == str(app.auth_proxy_var)
                    ]
                    self.assertEqual(settings_auth_proxy_entries, [])
                    auth_proxy_settings_buttons = [
                        widget
                        for widget in descendants(app.settings_tab)
                        if widget.winfo_class() == "TButton"
                        and widget.cget("text") == "授权代理设置"
                    ]
                    self.assertEqual(len(auth_proxy_settings_buttons), 1)
                    settings_labels = [
                        str(widget.cget("text"))
                        for widget in descendants(app.settings_tab)
                        if widget.winfo_class() == "TLabel"
                    ]
                    self.assertNotIn(
                        "软件接口代理（仅Sub2API管理接口）",
                        settings_labels,
                    )
                    self.assertIn(
                        "Sub2API 管理接口固定直连；OAuth/2FA 使用的代理请点击下方“授权代理设置”单独维护。",
                        settings_labels,
                    )
                    self.assertNotIn("Group IDs", settings_labels)
                    self.assertIn(
                        "授权代理设置",
                        [
                            str(widget.cget("text"))
                            for widget in descendants(app.settings_tab)
                            if widget.winfo_class() == "TButton"
                        ],
                    )
                    settings_button_texts = [
                        str(widget.cget("text"))
                        for widget in descendants(app.settings_tab)
                        if widget.winfo_class() == "TButton"
                    ]
                    self.assertLess(
                        settings_button_texts.index("授权代理设置"),
                        settings_button_texts.index("检查更新"),
                    )
                    app.auth_proxy_var.set(
                        "http://authorization-a.test:8080\n"
                        "socks5://authorization-b.test:1080"
                    )
                    auth_proxy_settings_buttons[0].invoke()
                    root.update_idletasks()
                    proxy_dialog = next(
                        widget
                        for widget in root.winfo_children()
                        if isinstance(widget, tk.Toplevel)
                        and widget.title() == "OAuth/2FA 授权代理设置"
                    )
                    proxy_editor = next(
                        widget
                        for widget in descendants(proxy_dialog)
                        if widget.winfo_class() == "Text"
                    )
                    self.assertIn(
                        "\n",
                        proxy_editor.get("1.0", tk.END).strip(),
                    )
                    original_tokens_dir = app.config["tokens_dir"]
                    app.tokens_dir_var.set(folder + "/unsaved-token-path")
                    next(
                        widget
                        for widget in descendants(proxy_dialog)
                        if widget.winfo_class() == "TButton"
                        and widget.cget("text") == "保存代理"
                    ).invoke()
                    self.assertEqual(app.config["tokens_dir"], original_tokens_dir)
                    self.assertIn("\n", app.config["auth_proxy"])
                    app.tokens_dir_var.set(original_tokens_dir)
                    convert_labels = [
                        str(widget.cget("text"))
                        for widget in descendants(app.convert_tab)
                        if widget.winfo_class() == "TLabel"
                    ]
                    self.assertNotIn("预览格式", convert_labels)
                    self.assertEqual(
                        [
                            widget
                            for widget in descendants(app.convert_tab)
                            if widget.winfo_class() == "TCombobox"
                        ],
                        [],
                    )
                    self.assertEqual(
                        [
                            str(widget.cget("text"))
                            for widget in descendants(app.sub2api_tab)
                            if widget.winfo_class() == "TMenubutton"
                        ],
                        [],
                    )
                    add_auth2fa_buttons = [
                        widget
                        for widget in descendants(app.auth2fa_tab)
                        if widget.winfo_class() == "TButton"
                        and widget.cget("text") == "添加到左侧凭据"
                    ]
                    self.assertEqual(len(add_auth2fa_buttons), 1)
                    credential_buttons = [
                        widget
                        for widget in descendants(app.auth2fa_tab)
                        if widget.winfo_class() == "TButton"
                        and widget.cget("text")
                        in (
                            "添加到左侧凭据",
                            "加密保存资料",
                            "载入已存资料",
                            "管理已存资料",
                        )
                    ]
                    self.assertEqual(len(credential_buttons), 4)
                    self.assertEqual(
                        [widget.cget("text") for widget in credential_buttons],
                        [
                            "添加到左侧凭据",
                            "加密保存资料",
                            "载入已存资料",
                            "管理已存资料",
                        ],
                    )
                    self.assertEqual(
                        {str(widget.master) for widget in credential_buttons},
                        {str(add_auth2fa_buttons[0].master)},
                    )
                    self.assertEqual(
                        [int(widget.grid_info()["column"]) for widget in credential_buttons],
                        [0, 1, 2, 3],
                    )
                    mode_hint_labels = [
                        widget
                        for widget in descendants(app.auth2fa_tab)
                        if widget.winfo_class() == "TLabel"
                        and str(widget.cget("textvariable"))
                        == str(app.auth2fa_mode_hint_var)
                    ]
                    vault_status_labels = [
                        widget
                        for widget in descendants(app.auth2fa_tab)
                        if widget.winfo_class() == "TLabel"
                        and str(widget.cget("textvariable"))
                        == str(app.auth2fa_vault_var)
                    ]
                    self.assertEqual(len(mode_hint_labels), 1)
                    self.assertEqual(len(vault_status_labels), 1)
                    self.assertIs(mode_hint_labels[0].master, vault_status_labels[0].master)
                    self.assertGreater(
                        int(mode_hint_labels[0].master.grid_info()["row"]),
                        int(add_auth2fa_buttons[0].master.grid_info()["row"]),
                    )
                    auth_result_buttons = [
                        widget
                        for widget in descendants(app.auth2fa_tab)
                        if widget.winfo_class() == "TButton"
                        and widget.cget("text") == "查看本次结果"
                    ]
                    self.assertEqual(len(auth_result_buttons), 1)
                    self.assertIn("disabled", auth_result_buttons[0].state())
                    app.auth2fa_last_result = {
                        "title": "智能补授权结果",
                        "success_count": 1,
                        "fail_count": 1,
                        "skipped_count": 1,
                        "input_error_count": 0,
                        "summary_path": "batch.json",
                        "rows": [
                            {
                                "status": "失败",
                                "email": "failed@example.test",
                                "message": "登录验证失败",
                                "report_path": "failed.json",
                            },
                            {
                                "status": "跳过",
                                "email": "healthy@example.test",
                                "message": "授权状态正常，无需重复登录",
                                "report_path": "",
                            },
                        ],
                    }
                    app.show_auth2fa_results()
                    root.update_idletasks()
                    result_dialog = next(
                        widget
                        for widget in root.winfo_children()
                        if isinstance(widget, tk.Toplevel)
                        and widget.title() == "智能补授权结果"
                    )
                    result_tree = next(
                        widget
                        for widget in descendants(result_dialog)
                        if widget.winfo_class() == "Treeview"
                    )
                    self.assertEqual(
                        [result_tree.item(row, "values")[0] for row in result_tree.get_children()],
                        ["失败", "跳过"],
                    )
                    self.assertTrue(any(
                        widget.winfo_class() == "Text"
                        and "登录验证失败" in widget.get("1.0", "end")
                        for widget in descendants(result_dialog)
                    ))
                    result_dialog.destroy()
                    sample = __import__('tkinter.ttk', fromlist=['Combobox']).Combobox(root, values=['上下文池','关闭'], state='readonly')
                    popup = root.tk.call('ttk::combobox::PopdownWindow', str(sample))
                    listbox = str(popup) + '.f.l'
                    self.assertEqual(root.tk.call(listbox,'cget','-foreground'),app.palette['text'])
                    self.assertEqual(root.tk.call(listbox,'cget','-background'),app.palette['card'])
                    sample.destroy()
                    self.assertIn(
                        str(app.account_panel), app.main_horizontal_pane.panes()
                    )
                    self.assertFalse(hasattr(app, "toggle_account_panel"))
                    self.assertNotIn(
                        "远端 / 双栏",
                        [
                            str(widget.cget("text"))
                            for widget in descendants(app.workspace_actions)
                            if widget.winfo_class() == "TButton"
                        ],
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
                    self.assertNotIn('access_token', app.current_settings()['integrations']['sub2api'])
            finally:
                for event in root.tk.call("after", "info"):
                    root.after_cancel(event)
                root.destroy()


if __name__ == "__main__":
    unittest.main()
