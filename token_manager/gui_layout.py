from __future__ import annotations

import tkinter as tk
from tkinter import scrolledtext, ttk

from .constants import MAX_REFRESH_WORKERS, MAX_UPLOAD_WORKERS
from .gui_widgets import UsageTreeview, HoverTooltip


class GUILayoutMixin:
    def setup_ui(self) -> None:
        shell = ttk.Frame(self.root, padding=(8, 6, 8, 6), style="Shell.TFrame")
        shell.pack(fill=tk.BOTH, expand=True)

        left, right, bottom = self._build_main_panes(shell)
        self._build_log_panel(bottom)
        self._build_token_panel(left)
        self._build_right_panel(right)
        self.root.after(120, self._apply_initial_pane_layout)

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

        bottom = ttk.Frame(self.main_vertical_pane, padding=0, style="Card.TFrame")
        bottom.configure(height=170)
        self.log_panel = bottom

        left = ttk.LabelFrame(self.main_horizontal_pane, text="本地凭据", padding=8, style="Card.TLabelframe")
        heading=ttk.Frame(left,style='Card.TFrame')
        ttk.Label(heading,text='本地凭据',style='Stats.TLabel').pack(side='left')
        self.account_heading_stats=ttk.Label(heading,textvariable=self.stats_var,style='CardSubtle.TLabel',font=('Microsoft YaHei UI',9))
        self.account_heading_stats.pack(side='left',padx=10)
        left.configure(labelwidget=heading)
        right = ttk.Frame(self.main_horizontal_pane, padding=(6,0,0,0), style="Card.TFrame")
        self.account_panel = left
        self.main_horizontal_pane.add(left, weight=1)
        self.main_horizontal_pane.add(right, weight=1)
        self.main_vertical_pane.add(top_host, weight=5)
        self.main_vertical_pane.add(bottom, weight=0)
        self._layout_resize_pending = None
        self.main_vertical_pane.bind("<Configure>", self._queue_layout_resize)
        return left, right, bottom

    def _apply_initial_pane_layout(self) -> None:
        try:
            self.root.update_idletasks()
            total_height = max(1, self.main_vertical_pane.winfo_height())
            total_width = max(1, self.main_horizontal_pane.winfo_width())
            log_height = max(125, min(200, int(total_height * 0.23)))
            if str(self.log_panel) in self.main_vertical_pane.panes():
                self.main_vertical_pane.sashpos(0, max(220, total_height - log_height))
            self.main_horizontal_pane.sashpos(0, total_width // 2)
        except (AttributeError, tk.TclError):
            return

    def _build_token_panel(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        action_frame = ttk.Frame(parent, style="Card.TFrame")
        action_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for column in range(3):
            action_frame.columnconfigure(column, weight=1)
        help_text = {
            "重新读取账号": "重新扫描本地凭据目录，不会刷新 OAuth 令牌。",
            "刷新本地令牌": "刷新选中账号的本地 OAuth 令牌并保存。",
            "同步标签": "读取选中账号的订阅身份并更新本地标签。",
            "上传选中": "将选中的本地凭据同步或更新到 Sub2API。",
            "远端配置": "配置 Sub2API 连接、上传参数、代理池和自动维护规则。",
            "监控选中": "绑定选中账号并纳入 Sub2API 自动维护。",
            "删除账号": "删除本地凭据文件，不删除远端账号。",
            "启动自动维护": "周期检查令牌、401、补授权、上传和运行验证。",
            "导入 / 导出": "预览、导入或导出账号数据格式。",
        }
        def action(text, command, row, column, style="TButton"):
            button = ttk.Button(action_frame, text=text, command=command, style=style)
            button.grid(row=row, column=column, sticky="ew", padx=3, pady=3)
            HoverTooltip(button, help_text[text])
            return button
        action("重新读取账号", self.reload_tokens, 0, 0, "Primary.TButton")
        action("刷新本地令牌", self.refresh_selected, 0, 1)
        action("同步标签", self.sync_selected_labels, 0, 2)
        action("上传选中", self.upload_selected, 1, 0)
        action("远端配置", self.open_sub2api_upload_settings, 1, 1)
        action("监控选中", lambda: self.set_recovery_selected(True), 1, 2)
        action("删除账号", self.delete_selected, 2, 0)
        self.auto_refresh_button = action("启动自动维护", self.toggle_auto_refresh, 2, 1)
        action("导入 / 导出", lambda: self.right_notebook.select(self.convert_tab), 2, 2)

        filter_frame = ttk.Frame(parent, style="Card.TFrame")
        filter_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
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
            values=["全部标签", "Plus", "Pro 20x", "Pro 5x", "Business Standard", "Business Premium", "Free", "Enterprise", "Unknown"],
            state="readonly",
        )
        plan_combo.grid(row=0, column=3, sticky="ew", padx=3, pady=3)
        plan_combo.bind("<<ComboboxSelected>>", lambda _e: self.reload_tokens(save_first=False))
        ttk.Label(filter_frame, text="状态", style="Card.TLabel").grid(row=0, column=4, sticky="w", padx=3, pady=3)
        status_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.status_filter_var,
            values=["全部状态", "未过期", "已过期", "可调度", "授权失效", "待补授权", "已停用", "上传异常"],
            state="readonly",
        )
        status_combo.grid(row=0, column=5, sticky="ew", padx=3, pady=3)
        status_combo.bind("<<ComboboxSelected>>", lambda _e: self.reload_tokens(save_first=False))

        list_frame = ttk.Frame(parent, style="Card.TFrame")
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        columns = ("email", "plan", "quota7", "status", "remaining", "upload", "recovery")
        self.token_tree = UsageTreeview(list_frame, columns=columns, show="headings", selectmode="extended")
        self.token_tree.heading("email", text="邮箱")
        self.token_tree.heading("plan", text="标签")
        for key,label in [("quota7","7d已用")]:
            self.token_tree.heading(key,text=label)
            self.token_tree.column(key,width=75,stretch=False)
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
        self.token_tree.bind("<Button-3>", self.show_token_context_menu, add="+")
        self.token_tree.tag_configure("expired", foreground="#a94438")
        self.token_tree.tag_configure("warning", foreground=self.palette["accent"])

    def _build_right_panel(self, parent) -> None:
        nav = ttk.Frame(parent, style='Card.TFrame')
        nav.pack(fill=tk.X, pady=(0,5))
        nav.columnconfigure(0,weight=1)
        self.workspace_nav=ttk.Frame(nav,style='Card.TFrame')
        self.workspace_nav.grid(row=0,column=0,sticky='w')
        self.workspace_actions=ttk.Frame(nav,style='Card.TFrame')
        self.workspace_actions.grid(row=0,column=1,sticky='e')
        ttk.Button(self.workspace_actions,text='维护规则',command=self.show_maintenance_rules,style='Nav.TButton').pack(side='left',padx=2)
        ttk.Button(self.workspace_actions,text='远端 / 双栏',command=self.toggle_account_panel,style='Nav.TButton').pack(side='left',padx=2)
        self.right_notebook = ttk.Notebook(parent,style='Workspace.TNotebook')
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
        self.workspace_buttons={}
        for tab,label in [(self.detail_tab,'详情'),(self.auth_tab,'授权'),(self.auth2fa_tab,'2FA'),(self.convert_tab,'导入导出'),(self.sub2api_tab,'Sub2API'),(self.settings_tab,'设置')]:
            button=ttk.Button(self.workspace_nav,text=label,command=lambda t=tab:self.right_notebook.select(t),style='Nav.TButton')
            button.pack(side='left',padx=1)
            self.workspace_buttons[str(tab)]=button
        def mark_tab(event=None):
            current=self.right_notebook.select()
            for tab,button in self.workspace_buttons.items():
                button.configure(style='ActiveNav.TButton' if tab==current else 'Nav.TButton')
        self.right_notebook.bind('<<NotebookTabChanged>>',mark_tab)
        mark_tab()

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
        self.auto_auth_button = ttk.Button(auto_frame, text="启动自动授权", command=self.start_auto_auth, style="Primary.TButton")
        self.auto_auth_button.pack(anchor=tk.W, pady=(10, 0))

    def _build_convert_tab(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        controls = ttk.Frame(parent, style="Card.TFrame")
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for column in range(6):
            controls.columnconfigure(column, weight=1)
        ttk.Label(controls, text="预览格式", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=3, pady=3)
        ttk.Combobox(
            controls,
            textvariable=self.preview_format_var,
            values=("Sub2API",),
            state="readonly",
            width=12,
        ).grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        for column, (label, command) in enumerate(
            (
                ("生成预览", self.build_preview_from_var),
                ("复制预览", self.copy_preview),
                ("导入剪贴板", self.import_from_clipboard),
                ("导入文件", self.import_from_file),
            ),
            start=2,
        ):
            ttk.Button(controls, text=label, command=command).grid(
                row=0, column=column, sticky="ew", padx=3, pady=3
            )

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
        ttk.Button(controls, text="清空输入", command=self.clear_auth2fa_accounts_text).grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        ttk.Label(controls, text="模式", style="Card.TLabel").grid(row=0, column=2, sticky="e", padx=3, pady=3)
        self.auth2fa_mode_combo = ttk.Combobox(controls, textvariable=self.auth2fa_mode_var, values=["协议链", "浏览器链"], state="readonly")
        self.auth2fa_mode_combo.grid(row=0, column=3, sticky="ew", padx=3, pady=3)
        self.auth2fa_mode_combo.bind("<<ComboboxSelected>>", self.on_auth2fa_mode_changed)
        ttk.Label(controls, text="线程", style="Card.TLabel").grid(row=0, column=4, sticky="e", padx=3, pady=3)
        ttk.Spinbox(controls, from_=1, to=MAX_REFRESH_WORKERS, textvariable=self.auth2fa_workers_var, width=8).grid(row=0, column=5, sticky="w", padx=3, pady=3)
        ttk.Button(controls, text="智能补授权", command=self.start_auth2fa_batch, style="Primary.TButton").grid(row=0, column=6, sticky="ew", padx=3, pady=3)
        ttk.Checkbutton(controls, text="成功写入 Tokens", variable=self.auth2fa_save_token_var).grid(row=1, column=0, columnspan=2, sticky="w", padx=3, pady=3)
        ttk.Label(controls, text="每行一个 账号----密码----2FA密匙", style="CardSubtle.TLabel").grid(row=1, column=2, columnspan=3, sticky="w", padx=3, pady=3)
        ttk.Label(controls, textvariable=self.auth2fa_stats_var, style="Stats.TLabel").grid(row=1, column=5, columnspan=2, sticky="e", padx=3, pady=3)
        ttk.Label(controls, textvariable=self.auth2fa_mode_hint_var, style="CardSubtle.TLabel").grid(row=2, column=0, columnspan=7, sticky="w", padx=3, pady=(0, 3))
        ttk.Button(controls, text='加密保存资料', command=self.save_auth2fa_credentials).grid(row=3, column=0, columnspan=2, sticky='ew', padx=3)
        ttk.Button(controls, text='载入已存资料', command=self.load_auth2fa_credentials).grid(row=3, column=2, columnspan=2, sticky='ew', padx=3)
        ttk.Button(controls, text='管理已存资料', command=self.manage_auth2fa_credentials).grid(row=3, column=4, columnspan=3, sticky='ew', padx=3)
        ttk.Label(controls, textvariable=self.auth2fa_vault_var, style='CardSubtle.TLabel', wraplength=570).grid(row=4, column=0, columnspan=7, sticky='w', padx=3, pady=4)

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
        parent.rowconfigure(2, weight=1)
        self.sub2api_stats_var = tk.StringVar(value='未加载 · ID升序')
        self.sub2api_pool_stats_var = tk.StringVar(value='')
        self.sub2api_invalidated_stats_var = tk.StringVar(value='')
        toolbar = ttk.Frame(parent, style='Card.TFrame')
        toolbar.grid(row=0,column=0,sticky='ew',pady=(0,8))
        ttk.Button(toolbar,text='刷新列表',command=self.refresh_sub2api_accounts,style='Primary.TButton').pack(side='left')
        ttk.Button(toolbar,text='更新用量',command=self.refresh_sub2api_usage).pack(side='left',padx=6)
        ttk.Button(toolbar,text='Sub2API 设置',command=self.open_sub2api_upload_settings).pack(side='left')
        more = ttk.Menubutton(toolbar,text='账号操作 ▾')
        menu = tk.Menu(more,tearoff=False)
        menu.add_command(label='刷新令牌',command=self.refresh_selected_sub2api_remote)
        menu.add_separator()
        menu.add_command(label='启用调度（选中）',command=lambda:self.set_selected_sub2api_schedulable(True))
        menu.add_command(label='停用调度（选中）',command=lambda:self.set_selected_sub2api_schedulable(False))
        menu.add_separator()
        menu.add_command(label='删除选中…',command=self.delete_selected_sub2api_records)
        more.configure(menu=menu)
        more.pack(side='right')

        filters = ttk.Frame(parent, style='Card.TFrame')
        filters.grid(row=1,column=0,sticky='ew',pady=(0,8))
        for col in range(4): filters.columnconfigure(col,weight=1,uniform='filter')
        fields = [('分组',None),('搜索',self.sub2api_search_var),('状态',self.sub2api_status_filter_var),('类型',self.sub2api_type_filter_var)]
        for col,(label,var) in enumerate(fields):
            cell=ttk.Frame(filters,style='Card.TFrame')
            cell.grid(row=0,column=col,sticky='ew',padx=(0,6))
            cell.columnconfigure(0,weight=1)
            ttk.Label(cell,text=label,style='CardSubtle.TLabel').grid(row=0,column=0,sticky='w')
            if label=='搜索':
                widget=ttk.Entry(cell,textvariable=var,width=10)
                widget.bind('<KeyRelease>',lambda e:self.populate_sub2api_tree())
            elif label=='分组':
                widget=ttk.Button(cell,textvariable=self.sub2api_group_filter_var,command=self.choose_sub2api_group_filters)
                self.sub2api_group_combo=widget
            else:
                options=['全部状态','active','inactive','error','invalidated','unschedulable'] if label=='状态' else ['oauth','全部类型','setup-token','apikey','upstream','bedrock']
                widget=ttk.Combobox(cell,textvariable=var,values=options,state='readonly',width=10)
                widget.bind('<<ComboboxSelected>>',lambda e:self.populate_sub2api_tree())
            widget.grid(row=1,column=0,sticky='ew',pady=(3,0))
        table=ttk.Frame(parent,style='Card.TFrame')
        table.grid(row=2,column=0,sticky='nsew')
        table.columnconfigure(0,weight=1);table.rowconfigure(0,weight=1)
        columns=('id','email','groups','status','scheduling','concurrency','quota7','priority','error')
        self.sub2api_tree=UsageTreeview(table,columns=columns,show='headings',selectmode='extended',height=12)
        for key,label,width in [('id','ID',40),('email','账号名称',105),('groups','账号标签',105),('status','状态',55),('scheduling','调度',70),('concurrency','并发',65),('quota7','7d已用',60),('priority','优先级',60),('error','错误摘要',85)]:
            self.sub2api_tree.heading(key,text=label+(' ↑' if key=='id' else ''),command=lambda k=key:self.sort_sub2api_accounts(k))
            self.sub2api_tree.column(key,width=width,minwidth=40,stretch=key in ('email','groups','error'),anchor=tk.CENTER if key in ('groups','status','scheduling','concurrency','quota7','priority') else tk.W)
        y=ttk.Scrollbar(table,orient='vertical',command=self.sub2api_tree.yview)
        x=ttk.Scrollbar(table,orient='horizontal',command=self.sub2api_tree.xview)
        self.sub2api_tree.configure(yscrollcommand=y.set,xscrollcommand=x.set)
        self.sub2api_tree.grid(row=0,column=0,sticky='nsew');y.grid(row=0,column=1,sticky='ns');x.grid(row=1,column=0,sticky='ew')
        self.sub2api_tree.bind('<Button-3>', self.show_sub2api_context_menu, add='+')
        self.sub2api_tree.tag_configure('error',foreground='#a94438')
        self.sub2api_tree.tag_configure('invalidated',foreground='#a94438')
        self.sub2api_tree.tag_configure('warning',foreground=self.palette['accent'])
        footer=ttk.Frame(parent,style='Card.TFrame')
        footer.grid(row=3,column=0,sticky='ew',pady=(5,0))
        footer.columnconfigure(0,weight=1)
        ttk.Label(footer,textvariable=self.sub2api_stats_var,style='CardSubtle.TLabel').grid(row=0,column=0,sticky='w')

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
        ttk.Button(sub2api_frame, text="Sub2API 设置（连接 / 上传 / 自动维护）", command=self.open_sub2api_upload_settings, style="Primary.TButton").pack(fill=tk.X, pady=12)
        ttk.Button(sub2api_frame, text="保存设置", command=self.save_settings).pack(anchor=tk.E)

    def _build_log_panel(self, parent) -> None:
        self.log_text = scrolledtext.ScrolledText(
            parent, height=7, wrap=tk.WORD, font=('Consolas',9),
            bg=self.palette['card'], fg=self.palette['text'], relief='flat',
            insertbackground=self.palette['text'], highlightthickness=1,
            highlightbackground=self.palette['border'], padx=6,pady=5)
        self.log_text.tag_configure("info", foreground=self.palette["muted"])
        self.log_text.tag_configure(
            "success",
            foreground=self.palette["success"],
            font=("Consolas", 9, "bold"),
        )
        self.log_text.tag_configure(
            "warning",
            foreground=self.palette["accent"],
            font=("Consolas", 9, "bold"),
        )
        self.log_text.tag_configure(
            "error",
            foreground=self.palette["danger"],
            font=("Consolas", 9, "bold"),
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.clear_log_button=ttk.Button(parent,text='清空日志',command=self.clear_logs,style='Nav.TButton')
        self.clear_log_button.place(relx=1,x=-24,y=4,anchor='ne')

    def _queue_layout_resize(self, _event=None):
        if self._layout_resize_pending is None:
            self._layout_resize_pending=self.root.after_idle(self._clamp_log_area)

    def _clamp_log_area(self):
        self._layout_resize_pending=None
        try:
            height=self.main_vertical_pane.winfo_height()
            current=self.main_vertical_pane.sashpos(0)
            if height>250:
                self.main_vertical_pane.sashpos(0,min(max(220,current),height-110))
        except tk.TclError:
            pass

    def toggle_account_panel(self):
        if str(self.account_panel) in self.main_horizontal_pane.panes():
            self.main_horizontal_pane.forget(self.account_panel)
        else:
            self.main_horizontal_pane.insert(0, self.account_panel, weight=1)
            self.root.after_idle(self._apply_initial_pane_layout)
