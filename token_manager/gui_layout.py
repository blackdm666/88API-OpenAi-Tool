from __future__ import annotations

import tkinter as tk
from tkinter import scrolledtext, ttk

from .constants import MAX_REFRESH_WORKERS, MAX_UPLOAD_WORKERS


class GUILayoutMixin:
    def setup_ui(self) -> None:
        shell = ttk.Frame(self.root, padding=(14, 14, 14, 12), style="Shell.TFrame")
        shell.pack(fill=tk.BOTH, expand=True)

        self._build_header(shell)
        left, right, bottom = self._build_main_panes(shell)
        self._build_token_panel(left)
        self._build_right_panel(right)
        self._build_log_panel(bottom)
        self.main_vertical_pane.forget(bottom)
        self.root.after(120, self._apply_initial_pane_layout)

    def _build_header(self, parent) -> None:
        header = ttk.Frame(parent, padding=(14, 12), style="Panel.TFrame")
        header.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(header, text="OpenAI Token Manager", style="Hero.TLabel").pack(side=tk.LEFT)
        ttk.Button(header, text="专注远端 / 双栏", command=self.toggle_account_panel).pack(side=tk.RIGHT, padx=8)
        ttk.Button(header, text="自动维护规则", command=self.show_maintenance_rules).pack(side=tk.RIGHT, padx=8)
        ttk.Button(header, text="展开 / 收起日志", command=self.toggle_log_panel).pack(side=tk.RIGHT, padx=8)
        ttk.Label(header, textvariable=self.status_var, style="StatusChip.TLabel").pack(side=tk.RIGHT)

    def _build_main_panes(self, parent):
        main = ttk.Frame(parent, style="Shell.TFrame")
        main.pack(fill=tk.BOTH, expand=True)

        self.main_vertical_pane = ttk.PanedWindow(main, orient=tk.VERTICAL)
        self.main_vertical_pane.pack(fill=tk.BOTH, expand=True)

        top_host = ttk.Frame(self.main_vertical_pane, style="Shell.TFrame")
        top_host.columnconfigure(0, weight=1)
        top_host.rowconfigure(0, weight=1)

        self.main_horizontal_pane = ttk.PanedWindow(top_host, orient=tk.HORIZONTAL)
        self.main_horizontal_pane.grid(row=0, column=0, sticky="nsew")

        bottom = ttk.LabelFrame(self.main_vertical_pane, text="运行日志", padding=8, style="Card.TLabelframe")
        bottom.configure(height=120)
        self.log_panel = bottom

        left = ttk.LabelFrame(self.main_horizontal_pane, text="账号列表", padding=8, style="Card.TLabelframe")
        right = ttk.LabelFrame(self.main_horizontal_pane, text="功能区", padding=8, style="Card.TLabelframe")
        self.account_panel = left
        self.main_horizontal_pane.add(left, weight=1)
        self.main_horizontal_pane.add(right, weight=3)
        self.main_vertical_pane.add(top_host, weight=5)
        self.main_vertical_pane.add(bottom, weight=2)
        return left, right, bottom

    def _apply_initial_pane_layout(self) -> None:
        try:
            self.root.update_idletasks()
            total_height = max(1, self.main_vertical_pane.winfo_height())
            total_width = max(1, self.main_horizontal_pane.winfo_width())
            log_height = 100
            if str(self.log_panel) in self.main_vertical_pane.panes():
                self.main_vertical_pane.sashpos(0, max(420, total_height - log_height))
            self.main_horizontal_pane.sashpos(0, min(430, int(total_width * 0.34)))
        except (AttributeError, tk.TclError):
            return

    def _build_token_panel(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)

        action_frame = ttk.Frame(parent, style="Card.TFrame")
        action_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for column in range(3):
            action_frame.columnconfigure(column, weight=1)
        ttk.Button(action_frame, text="刷新列表", command=self.reload_tokens, style="Primary.TButton").grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(action_frame, text="刷新选中", command=self.refresh_selected).grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(action_frame, text="刷新当前", command=self.refresh_all).grid(row=0, column=2, sticky="ew", padx=3, pady=3)
        ttk.Button(action_frame, text="同步标签", command=self.sync_selected_labels).grid(row=1, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(action_frame, text="删除账号", command=self.delete_selected).grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        self.auto_refresh_button = ttk.Button(action_frame, text="启动自动维护", command=self.toggle_auto_refresh)
        self.auto_refresh_button.grid(row=1, column=2, sticky="ew", padx=3, pady=3)

        upload_frame = ttk.Frame(parent, style="Card.TFrame")
        upload_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        for column in range(3):
            upload_frame.columnconfigure(column, weight=1)
        for row, column, label, command in [
            (0, 0, '上传选中', self.upload_selected),
            (0, 1, '上传当前', self.upload_all),
            (0, 2, '上传配置', self.open_sub2api_upload_settings),
            (1, 0, '监控选中', lambda: self.set_recovery_selected(True)),
            (1, 1, '取消监控', lambda: self.set_recovery_selected(False)),
            (1, 2, '导入 / 导出', lambda: self.right_notebook.select(self.convert_tab)),
        ]:
            ttk.Button(upload_frame, text=label, command=command).grid(row=row, column=column, sticky='ew', padx=3, pady=3)

        filter_frame = ttk.Frame(parent, style="Card.TFrame")
        filter_frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        for column in range(6):
            filter_frame.columnconfigure(column, weight=1, minsize=0)
        ttk.Label(filter_frame, text="搜索", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=3, pady=3)
        search_entry = ttk.Entry(filter_frame, textvariable=self.search_var)
        search_entry.grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        self._search_debounce_id = None

        def _debounced_search(_e=None):
            if self._search_debounce_id is not None:
                self.root.after_cancel(self._search_debounce_id)
            self._search_debounce_id = self.root.after(300, lambda: self.reload_tokens(save_first=False))

        search_entry.bind("<KeyRelease>", _debounced_search)
        ttk.Label(filter_frame, text="标签", style="Card.TLabel").grid(row=0, column=2, sticky="w", padx=3, pady=3)
        plan_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.plan_filter_var,
            values=["全部标签", "Team", "Plus", "Free", "Pro", "Enterprise", "Unknown"],
            state="readonly",
        )
        plan_combo.grid(row=0, column=3, sticky="ew", padx=3, pady=3)
        plan_combo.bind("<<ComboboxSelected>>", lambda _e: self.reload_tokens(save_first=False))
        ttk.Label(filter_frame, text="状态", style="Card.TLabel").grid(row=0, column=4, sticky="w", padx=3, pady=3)
        status_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.status_filter_var,
            values=["全部状态", "有效", "已过期", "上传异常"],
            state="readonly",
        )
        status_combo.grid(row=0, column=5, sticky="ew", padx=3, pady=3)
        status_combo.bind("<<ComboboxSelected>>", lambda _e: self.reload_tokens(save_first=False))
        ttk.Button(filter_frame, text="全部", command=lambda: self.apply_quick_filter("全部标签")).grid(row=1, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(filter_frame, text="Team", command=lambda: self.apply_quick_filter("Team")).grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(filter_frame, text="Plus", command=lambda: self.apply_quick_filter("Plus")).grid(row=1, column=2, sticky="ew", padx=3, pady=3)
        ttk.Button(filter_frame, text="Free", command=lambda: self.apply_quick_filter("Free")).grid(row=1, column=3, sticky="ew", padx=3, pady=3)
        ttk.Button(filter_frame, text="异常", command=lambda: self.apply_quick_filter("全部标签", "上传异常")).grid(row=1, column=4, sticky="ew", padx=3, pady=3)
        ttk.Button(filter_frame, text="清空筛选", command=self.clear_filters).grid(row=1, column=5, sticky="ew", padx=3, pady=3)
        ttk.Label(filter_frame, textvariable=self.stats_var, style="Stats.TLabel").grid(row=2, column=0, columnspan=6, sticky="w", padx=3, pady=(4, 0))

        list_frame = ttk.Frame(parent, style="Card.TFrame")
        list_frame.grid(row=3, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        columns = ("email", "plan", "status", "remaining", "upload", "recovery")
        self.token_tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="extended")
        self.token_tree.heading("email", text="邮箱")
        self.token_tree.heading("plan", text="标签")
        self.token_tree.heading("status", text="状态")
        self.token_tree.heading("remaining", text="剩余时间")
        self.token_tree.heading("upload", text="上传状态")
        self.token_tree.heading("recovery", text="Sub2API监控")
        self.token_tree.column("recovery", width=120, stretch=False)
        self.token_tree.column("email", width=190, stretch=True)
        self.token_tree.column("plan", width=88, stretch=False, anchor=tk.CENTER)
        self.token_tree.column("status", width=96, stretch=False, anchor=tk.CENTER)
        self.token_tree.column("remaining", width=120, stretch=False, anchor=tk.CENTER)
        self.token_tree.column("upload", width=150, stretch=True)
        scrollbar_y = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.token_tree.yview)
        scrollbar_x = ttk.Scrollbar(list_frame, orient=tk.HORIZONTAL, command=self.token_tree.xview)
        self.token_tree.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        self.token_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar_y.grid(row=0, column=1, sticky="ns")
        scrollbar_x.grid(row=1, column=0, sticky="ew")
        self.token_tree.bind("<<TreeviewSelect>>", self.on_selection_changed)
        self.token_tree.tag_configure("expired", foreground="#a94438")
        self.token_tree.tag_configure("warning", foreground=self.palette["accent"])

    def _build_right_panel(self, parent) -> None:
        self.right_notebook = ttk.Notebook(parent)
        self.right_notebook.pack(fill=tk.BOTH, expand=True)

        self.detail_tab = ttk.Frame(self.right_notebook, padding=8, style="Card.TFrame")
        self.auth_tab = ttk.Frame(self.right_notebook, padding=8, style="Card.TFrame")
        self.auth2fa_tab = ttk.Frame(self.right_notebook, padding=8, style="Card.TFrame")
        self.convert_tab = ttk.Frame(self.right_notebook, padding=8, style="Card.TFrame")
        self.sub2api_tab = ttk.Frame(self.right_notebook, padding=8, style="Card.TFrame")
        self.settings_tab = ttk.Frame(self.right_notebook, padding=8, style="Card.TFrame")

        self.right_notebook.add(self.detail_tab, text="详情")
        self.right_notebook.add(self.auth_tab, text="授权")
        self.right_notebook.add(self.auth2fa_tab, text="2FA授权")
        self.right_notebook.add(self.convert_tab, text="导入 / 导出")
        self.right_notebook.add(self.sub2api_tab, text="Sub2API")
        self.right_notebook.add(self.settings_tab, text="设置")

        self._build_detail_tab(self.detail_tab)
        self._build_auth_tab(self.auth_tab)
        self._build_auth2fa_tab(self.auth2fa_tab)
        self._build_convert_tab(self.convert_tab)
        self._build_sub2api_tab(self.sub2api_tab)
        self._build_settings_tab(self.settings_tab)
        self.right_notebook.select(self.sub2api_tab)

    def _build_detail_tab(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        self.detail_text = tk.Text(
            parent,
            wrap=tk.WORD,
            font=("Consolas", 9),
            bg=self.palette["card"],
            fg=self.palette["text"],
            relief="flat",
            insertbackground=self.palette["text"],
            highlightthickness=1,
            highlightbackground=self.palette["border"],
            padx=10,
            pady=10,
        )
        self.detail_text.grid(row=0, column=0, sticky="nsew")
        self.detail_text.config(state=tk.DISABLED)

        detail_actions = ttk.Frame(parent, style="Card.TFrame")
        detail_actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        for column in range(4):
            detail_actions.columnconfigure(column, weight=1)
        ttk.Button(detail_actions, text="复制 AT", command=self.copy_access_token).grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(detail_actions, text="复制 RT", command=self.copy_refresh_token).grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(detail_actions, text="预览 Sub2API", command=lambda: self.build_preview("Sub2API")).grid(row=0, column=3, sticky="ew", padx=3, pady=3)

    def _build_auth_tab(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(0, weight=1)

        manual_frame = ttk.LabelFrame(parent, text="手动授权", padding=8, style="Card.TLabelframe")
        manual_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        ttk.Button(manual_frame, text="生成授权 URL", command=self.generate_manual_url, style="Primary.TButton").pack(anchor=tk.W, pady=(0, 8))
        self.url_text = scrolledtext.ScrolledText(
            manual_frame,
            height=6,
            wrap=tk.WORD,
            font=("Consolas", 8),
            bg=self.palette["card"],
            fg=self.palette["text"],
            relief="flat",
            insertbackground=self.palette["text"],
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        self.url_text.pack(fill=tk.BOTH, expand=True)
        ttk.Label(manual_frame, text="回调 URL", style="Card.TLabel").pack(anchor=tk.W, pady=(8, 4))
        self.callback_entry = ttk.Entry(manual_frame, font=("Consolas", 9))
        self.callback_entry.pack(fill=tk.X)
        ttk.Button(manual_frame, text="提交并保存", command=self.submit_callback).pack(anchor=tk.W, pady=(8, 0))

        auto_frame = ttk.LabelFrame(parent, text="自动授权", padding=8, style="Card.TLabelframe")
        auto_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        ttk.Checkbutton(auto_frame, text="自动打开浏览器", variable=self.open_browser_var).pack(anchor=tk.W)
        ttk.Label(auto_frame, text="回调等待秒数", style="Card.TLabel").pack(anchor=tk.W, pady=(8, 4))
        ttk.Spinbox(auto_frame, from_=30, to=1800, textvariable=self.auto_auth_timeout_var, width=10).pack(anchor=tk.W)
        ttk.Button(auto_frame, text="启动自动授权", command=self.start_auto_auth, style="Primary.TButton").pack(anchor=tk.W, pady=(10, 0))

    def _build_convert_tab(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        controls = ttk.Frame(parent, style="Card.TFrame")
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for column in range(6):
            controls.columnconfigure(column, weight=1)
        ttk.Label(controls, text="预览格式", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=3, pady=3)
        ttk.Label(controls, text="Sub2API", style="Card.TLabel").grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="生成预览", command=self.build_preview_from_var).grid(row=0, column=2, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="复制预览", command=self.copy_preview).grid(row=0, column=3, sticky="ew", padx=3, pady=3)
        ttk.Label(controls, text="Sub2API", style="Card.TLabel").grid(row=0, column=4, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="导入剪贴板", command=self.import_from_clipboard).grid(row=0, column=5, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="导入文件", command=self.import_from_file).grid(row=1, column=5, sticky="ew", padx=3, pady=3)

        preview_card = ttk.Frame(parent, style="Card.TFrame")
        preview_card.grid(row=1, column=0, sticky="nsew")
        preview_card.columnconfigure(0, weight=1)
        preview_card.rowconfigure(0, weight=1)
        self.preview_text = scrolledtext.ScrolledText(
            preview_card,
            wrap=tk.WORD,
            font=("Consolas", 9),
            bg=self.palette["card"],
            fg=self.palette["text"],
            relief="flat",
            insertbackground=self.palette["text"],
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        self.preview_text.grid(row=0, column=0, sticky="nsew")

    def _build_auth2fa_tab(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        controls = ttk.Frame(parent, style="Card.TFrame")
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for column in range(7):
            controls.columnconfigure(column, weight=1)
        ttk.Button(controls, text="导入文件", command=self.import_auth2fa_accounts_file, style="Primary.TButton").grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(controls, text="清空", command=self.clear_auth2fa_accounts_text).grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        ttk.Label(controls, text="模式", style="Card.TLabel").grid(row=0, column=2, sticky="e", padx=3, pady=3)
        self.auth2fa_mode_combo = ttk.Combobox(controls, textvariable=self.auth2fa_mode_var, values=["协议链", "浏览器链"], state="readonly")
        self.auth2fa_mode_combo.grid(row=0, column=3, sticky="ew", padx=3, pady=3)
        self.auth2fa_mode_combo.bind("<<ComboboxSelected>>", self.on_auth2fa_mode_changed)
        ttk.Label(controls, text="线程", style="Card.TLabel").grid(row=0, column=4, sticky="e", padx=3, pady=3)
        ttk.Spinbox(controls, from_=1, to=MAX_REFRESH_WORKERS, textvariable=self.auth2fa_workers_var, width=8).grid(row=0, column=5, sticky="w", padx=3, pady=3)
        ttk.Button(controls, text="启动批量授权", command=self.start_auth2fa_batch, style="Primary.TButton").grid(row=0, column=6, sticky="ew", padx=3, pady=3)
        ttk.Checkbutton(controls, text="成功写入 Tokens", variable=self.auth2fa_save_token_var).grid(row=1, column=0, columnspan=2, sticky="w", padx=3, pady=3)
        ttk.Label(controls, text="每行一个 账号----密码----2FA密匙", style="CardSubtle.TLabel").grid(row=1, column=2, columnspan=3, sticky="w", padx=3, pady=3)
        ttk.Label(controls, textvariable=self.auth2fa_stats_var, style="Stats.TLabel").grid(row=1, column=5, columnspan=2, sticky="e", padx=3, pady=3)
        ttk.Label(controls, textvariable=self.auth2fa_mode_hint_var, style="CardSubtle.TLabel").grid(row=2, column=0, columnspan=7, sticky="w", padx=3, pady=(0, 3))

        input_card = ttk.Frame(parent, style="Card.TFrame")
        input_card.grid(row=1, column=0, sticky="nsew")
        input_card.columnconfigure(0, weight=1)
        input_card.rowconfigure(0, weight=1)
        self.auth2fa_input = scrolledtext.ScrolledText(
            input_card,
            wrap=tk.WORD,
            font=("Consolas", 9),
            bg=self.palette["card"],
            fg=self.palette["text"],
            relief="flat",
            insertbackground=self.palette["text"],
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        self.auth2fa_input.grid(row=0, column=0, sticky="nsew")
        self.auth2fa_input.bind("<KeyRelease>", lambda _e: self.update_auth2fa_input_stats())

        footer = ttk.Frame(parent, style="Card.TFrame")
        footer.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(footer, textvariable=self.auth2fa_output_var, style="CardSubtle.TLabel").pack(side=tk.LEFT)




    def _build_sub2api_tab(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        top_bar = ttk.Frame(parent, style="Card.TFrame")
        top_bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(top_bar, text="刷新远端列表", command=self.refresh_sub2api_accounts, style="Primary.TButton").pack(side=tk.LEFT)
        self.sub2api_stats_var = tk.StringVar(value="Sub2API 未加载")
        ttk.Label(top_bar, textvariable=self.sub2api_stats_var, style="Stats.TLabel", wraplength=410).pack(side=tk.RIGHT)

        notebook = ttk.Notebook(parent)
        notebook.grid(row=1, column=0, sticky="nsew")

        pool_tab = ttk.Frame(notebook, padding=8, style="Card.TFrame")
        invalidated_tab = ttk.Frame(notebook, padding=8, style="Card.TFrame")
        notebook.add(pool_tab, text="远端账号池")
        notebook.add(invalidated_tab, text="失效记录")

        self._build_sub2api_pool_tab(pool_tab)
        self._build_sub2api_invalidated_tab(invalidated_tab)

    def _build_sub2api_pool_tab(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1, minsize=200)

        actions = ttk.Frame(parent, style="Card.TFrame")
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for column in range(5):
            actions.columnconfigure(column, weight=1)
        ttk.Button(actions, text="刷新选中令牌", command=self.refresh_selected_sub2api_remote, style="Primary.TButton").grid(row=0, column=0, sticky="ew", padx=3, pady=3)
        ttk.Button(actions, text="上传配置", command=self.open_sub2api_upload_settings).grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        ttk.Button(actions, text="删除选中", command=self.delete_selected_sub2api_records).grid(row=0, column=2, sticky="ew", padx=3, pady=3)
        ttk.Button(actions, text="停用选中", command=lambda: self.set_selected_sub2api_status("inactive")).grid(row=0, column=3, sticky="ew", padx=3, pady=3)
        ttk.Button(actions, text="启用选中", command=lambda: self.set_selected_sub2api_status("active")).grid(row=0, column=4, sticky="ew", padx=3, pady=3)

        filters = ttk.Frame(parent, style="Card.TFrame")
        filters.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        for column in range(6):
            filters.columnconfigure(column, weight=1)
        ttk.Label(filters, text="搜索", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=3, pady=3)
        entry = ttk.Entry(filters, textvariable=self.sub2api_search_var)
        entry.grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        entry.bind("<KeyRelease>", lambda _e: self.populate_sub2api_tree())
        ttk.Label(filters, text="分组", style="Card.TLabel").grid(row=0, column=2, sticky="w", padx=3, pady=3)
        self.sub2api_group_combo = ttk.Button(filters, textvariable=self.sub2api_group_filter_var, command=self.choose_sub2api_group_filters)
        self.sub2api_group_combo.grid(row=0, column=3, sticky="ew", padx=3, pady=3)
        ttk.Label(filters, text="状态", style="Card.TLabel").grid(row=0, column=4, sticky="w", padx=3, pady=3)
        status = ttk.Combobox(filters, textvariable=self.sub2api_status_filter_var, values=["全部状态", "active", "inactive", "error", "invalidated", "unschedulable"], state="readonly")
        status.grid(row=0, column=5, sticky="ew", padx=3, pady=3)
        status.bind("<<ComboboxSelected>>", lambda _e: self.populate_sub2api_tree())
        ttk.Label(filters, text="类型", style="Card.TLabel").grid(row=1, column=0, sticky="w", padx=3, pady=3)
        record_type = ttk.Combobox(filters, textvariable=self.sub2api_type_filter_var, values=["全部类型", "oauth", "setup-token", "apikey", "upstream", "bedrock"], state="readonly")
        record_type.grid(row=1, column=1, sticky="ew", padx=3, pady=3)
        record_type.bind("<<ComboboxSelected>>", lambda _e: self.populate_sub2api_tree())
        ttk.Button(filters, text="清空筛选", command=self.clear_sub2api_filters).grid(row=1, column=5, sticky="ew", padx=3, pady=3)
        self.sub2api_pool_stats_var = tk.StringVar(value="")
        ttk.Label(filters, textvariable=self.sub2api_pool_stats_var, style="Stats.TLabel").grid(row=1, column=2, columnspan=3, sticky="w", padx=3, pady=3)

        center = ttk.Frame(parent, style="Card.TFrame")
        center.grid(row=2, column=0, sticky="nsew")
        center.columnconfigure(0, weight=1)
        center.rowconfigure(0, weight=1)
        self.sub2api_tree = ttk.Treeview(center, columns=("email", "groups", "status", "type", "flags", "expires_at", "last_used", "error"), displaycolumns=("email", "groups", "status", "error"), show="headings", height=10, selectmode="extended")
        self.sub2api_tree.heading("email", text="邮箱")
        self.sub2api_tree.heading("groups", text="分组")
        self.sub2api_tree.heading("status", text="状态")
        self.sub2api_tree.heading("type", text="类型")
        self.sub2api_tree.heading("flags", text="标记")
        self.sub2api_tree.heading("expires_at", text="到期时间")
        self.sub2api_tree.heading("last_used", text="最后使用")
        self.sub2api_tree.heading("error", text="错误摘要")
        self.sub2api_tree.column("email", width=200, stretch=True)
        self.sub2api_tree.column("groups", width=140, stretch=True)
        self.sub2api_tree.column("status", width=88, stretch=False, anchor=tk.CENTER)
        self.sub2api_tree.column("type", width=92, stretch=False, anchor=tk.CENTER)
        self.sub2api_tree.column("flags", width=100, stretch=False, anchor=tk.CENTER)
        self.sub2api_tree.column("expires_at", width=130, stretch=False)
        self.sub2api_tree.column("last_used", width=130, stretch=False)
        self.sub2api_tree.column("error", width=220, stretch=True)
        sub2api_scroll_y = ttk.Scrollbar(center, orient=tk.VERTICAL, command=self.sub2api_tree.yview)
        sub2api_scroll_x = ttk.Scrollbar(center, orient=tk.HORIZONTAL, command=self.sub2api_tree.xview)
        self.sub2api_tree.configure(yscrollcommand=sub2api_scroll_y.set, xscrollcommand=sub2api_scroll_x.set)
        self.sub2api_tree.grid(row=0, column=0, sticky="nsew")
        sub2api_scroll_y.grid(row=0, column=1, sticky="ns")
        sub2api_scroll_x.grid(row=1, column=0, sticky="ew")
        self.sub2api_tree.bind("<<TreeviewSelect>>", self.on_sub2api_selection_changed)
        self.sub2api_tree.tag_configure("error", foreground="#a94438")
        self.sub2api_tree.tag_configure("invalidated", foreground="#a94438")
        self.sub2api_tree.tag_configure("warning", foreground=self.palette["accent"])

        self.sub2api_detail_text = tk.Text(parent, wrap=tk.WORD, height=2, font=("Consolas", 9), bg=self.palette["card"], fg=self.palette["text"], relief="flat", insertbackground=self.palette["text"], highlightthickness=1, highlightbackground=self.palette["border"], padx=10, pady=10)
        self.sub2api_detail_text.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(parent, text="展开 / 收起账号详情", command=lambda: self.sub2api_detail_text.configure(height=8 if int(self.sub2api_detail_text.cget("height")) == 2 else 2)).grid(row=4, column=0, sticky="e", pady=3)
        self.sub2api_detail_text.config(state=tk.DISABLED)

    def _build_sub2api_invalidated_tab(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        actions = ttk.Frame(parent, style="Card.TFrame")
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(actions, text="删除选中失效", command=self.delete_selected_invalidated_sub2api_records, style="Primary.TButton").pack(side=tk.LEFT)
        ttk.Button(actions, text="删除全部失效", command=self.delete_all_invalidated_sub2api_records).pack(side=tk.LEFT, padx=6)
        self.sub2api_invalidated_stats_var = tk.StringVar(value="失效记录 0")
        ttk.Label(actions, textvariable=self.sub2api_invalidated_stats_var, style="Stats.TLabel").pack(side=tk.RIGHT)

        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.grid(row=1, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.sub2api_invalidated_tree = ttk.Treeview(frame, columns=("email", "groups", "status", "expires_at", "error"), show="headings", selectmode="extended")
        self.sub2api_invalidated_tree.heading("email", text="邮箱")
        self.sub2api_invalidated_tree.heading("groups", text="分组")
        self.sub2api_invalidated_tree.heading("status", text="状态")
        self.sub2api_invalidated_tree.heading("expires_at", text="到期时间")
        self.sub2api_invalidated_tree.heading("error", text="失效信息")
        self.sub2api_invalidated_tree.column("email", width=220, stretch=True)
        self.sub2api_invalidated_tree.column("groups", width=150, stretch=True)
        self.sub2api_invalidated_tree.column("status", width=88, stretch=False, anchor=tk.CENTER)
        self.sub2api_invalidated_tree.column("expires_at", width=130, stretch=False)
        self.sub2api_invalidated_tree.column("error", width=260, stretch=True)
        sub2api_invalid_scroll_y = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.sub2api_invalidated_tree.yview)
        sub2api_invalid_scroll_x = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.sub2api_invalidated_tree.xview)
        self.sub2api_invalidated_tree.configure(yscrollcommand=sub2api_invalid_scroll_y.set, xscrollcommand=sub2api_invalid_scroll_x.set)
        self.sub2api_invalidated_tree.grid(row=0, column=0, sticky="nsew")
        sub2api_invalid_scroll_y.grid(row=0, column=1, sticky="ns")
        sub2api_invalid_scroll_x.grid(row=1, column=0, sticky="ew")
        self.sub2api_invalidated_tree.bind("<<TreeviewSelect>>", self.on_sub2api_invalidated_selection_changed)
        self.sub2api_invalidated_tree.tag_configure("invalidated", foreground="#a94438")

        self.sub2api_invalidated_detail_text = tk.Text(parent, wrap=tk.WORD, height=4, font=("Consolas", 9), bg=self.palette["card"], fg=self.palette["text"], relief="flat", insertbackground=self.palette["text"], highlightthickness=1, highlightbackground=self.palette["border"], padx=10, pady=10)
        self.sub2api_invalidated_detail_text.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.sub2api_invalidated_detail_text.config(state=tk.DISABLED)

    def _build_settings_tab(self, parent) -> None:
        settings_notebook = ttk.Notebook(parent)
        settings_notebook.pack(fill=tk.BOTH, expand=True)

        basic_tab = ttk.Frame(settings_notebook, padding=8, style="Card.TFrame")
        oauth_tab = ttk.Frame(settings_notebook, padding=8, style="Card.TFrame")
        sub2api_tab = ttk.Frame(settings_notebook, padding=8, style="Card.TFrame")
        settings_notebook.add(basic_tab, text="基础")
        settings_notebook.add(oauth_tab, text="OAuth")
        settings_notebook.add(sub2api_tab, text="Sub2API")

        basic_frame = ttk.LabelFrame(basic_tab, text="基础设置", padding=10, style="Card.TLabelframe")
        basic_frame.pack(fill=tk.BOTH, expand=True)
        self._add_labeled_entry(basic_frame, "Tokens 目录", self.tokens_dir_var, browse=True)
        self._add_labeled_entry(basic_frame, "输出目录", self.outputs_dir_var, browse_outputs=True)
        self._add_labeled_entry(basic_frame, "全局代理", self.proxy_var)
        self._add_labeled_spin(basic_frame, "刷新线程", self.refresh_workers_var, 1, MAX_REFRESH_WORKERS)
        self._add_labeled_spin(basic_frame, "上传线程", self.upload_workers_var, 1, MAX_UPLOAD_WORKERS)
        self._add_labeled_spin(basic_frame, "自动维护检查秒数", self.auto_interval_var, 30, 3600)
        self._add_labeled_spin(basic_frame, "自动维护提前刷新秒数", self.auto_threshold_var, 30, 3600)
        tools = ttk.Frame(basic_frame, style="Card.TFrame")
        tools.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(tools, text="整理导出文件", command=self.organize_output_dirs).pack(side=tk.LEFT)
        ttk.Button(tools, text="清理 Tokens", command=self.cleanup_tokens_dir).pack(side=tk.LEFT, padx=6)
        ttk.Button(tools, text="保存设置", command=self.save_settings, style="Primary.TButton").pack(side=tk.RIGHT)

        oauth_frame = ttk.LabelFrame(oauth_tab, text="OAuth 配置", padding=10, style="Card.TLabelframe")
        oauth_frame.pack(fill=tk.BOTH, expand=True)
        self._add_labeled_entry(oauth_frame, "Auth URL", self.oauth_auth_url_var)
        self._add_labeled_entry(oauth_frame, "Token URL", self.oauth_token_url_var)
        self._add_labeled_entry(oauth_frame, "Client ID", self.oauth_client_id_var)
        self._add_labeled_entry(oauth_frame, "Redirect URI", self.oauth_redirect_uri_var)
        self._add_labeled_entry(oauth_frame, "Scope", self.oauth_scope_var)
        browser_frame = ttk.LabelFrame(oauth_tab, text="浏览器链", padding=10, style="Card.TLabelframe")
        browser_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self._add_labeled_entry(browser_frame, "浏览器路径", self.browser_path_var)
        self._add_labeled_spin(browser_frame, "调试起始端口", self.browser_debug_port_var, 1024, 65535)


        sub2api_frame = ttk.LabelFrame(sub2api_tab, text="Sub2API 配置", padding=10, style="Card.TLabelframe")
        sub2api_frame.pack(fill=tk.BOTH, expand=True)
        self._add_labeled_entry(sub2api_frame, "Sub2API URL", self.sub2api_url_var)
        self._add_labeled_entry(sub2api_frame, "管理 Token/API Key", self.sub2api_key_var, show="•")
        auth = ttk.Combobox(sub2api_frame, textvariable=self.sub2api_auth_mode_var, values=["auto", "api_key", "password"], state="readonly")
        auth.pack(fill=tk.X, pady=6)
        self._add_labeled_entry(sub2api_frame, "Group IDs", self.sub2api_group_ids_var)
        self._add_labeled_entry(sub2api_frame, "管理邮箱", self.sub2api_admin_email_var)
        self._add_labeled_entry(sub2api_frame, "管理密码", self.sub2api_admin_password_var, show="*")
        ttk.Button(sub2api_frame, text="连接 / 上传参数 / 分组与代理", command=self.open_sub2api_upload_settings, style="Primary.TButton").pack(fill=tk.X, pady=12)
        ttk.Button(sub2api_frame, text="保存设置", command=self.save_settings).pack(anchor=tk.E)

    def _build_log_panel(self, parent) -> None:
        log_toolbar = ttk.Frame(parent, style="Card.TFrame")
        log_toolbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(log_toolbar, textvariable=self.status_var, style="Card.TLabel").pack(side=tk.LEFT)
        ttk.Button(log_toolbar, text="清空日志", command=self.clear_logs).pack(side=tk.RIGHT)
        self.log_text = scrolledtext.ScrolledText(
            parent,
            height=3,
            wrap=tk.WORD,
            font=("Consolas", 9),
            bg=self.palette["card"],
            fg=self.palette["text"],
            relief="flat",
            insertbackground=self.palette["text"],
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def toggle_log_panel(self):
        panes = self.main_vertical_pane.panes()
        if str(self.log_panel) in panes:
            self.main_vertical_pane.forget(self.log_panel)
        else:
            self.main_vertical_pane.add(self.log_panel, weight=0)
            self.root.after_idle(self._apply_initial_pane_layout)

    def toggle_account_panel(self):
        if str(self.account_panel) in self.main_horizontal_pane.panes():
            self.main_horizontal_pane.forget(self.account_panel)
        else:
            self.main_horizontal_pane.insert(0, self.account_panel, weight=1)
            self.root.after_idle(self._apply_initial_pane_layout)
