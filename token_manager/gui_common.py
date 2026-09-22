from __future__ import annotations

from copy import deepcopy
import threading
import time
import traceback
import webbrowser
from typing import Any

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .config import save_app_config
from .constants import (
    APP_VERSION,
    DEFAULT_ACCOUNT_PURCHASE_URL,
    DEFAULT_AUTH_TIMEOUT_SECONDS,
    DEFAULT_LOG_POLL_MS,
    DEFAULT_UI_REFRESH_MS,
    MAX_REFRESH_WORKERS,
    MAX_UPLOAD_WORKERS,
)
from .store import TokenStore
from .sub2api_policy import redact_error
from .gui_widgets import HoverTooltip, center_window
from .auth_proxy import authorization_proxy_pool
from .updater import (
    UpdateInfo,
    check_for_update as fetch_update_info,
    download_update_package,
    launch_update,
)


class GUICommonMixin:
    def _install_default_tooltips(self, root) -> None:
        """Attach plain-language help to every main button and input widget."""
        references = getattr(self, "_tooltip_references", [])
        descriptions = {
            "重新读取账号": "重新扫描本地 Tokens 目录；不会主动刷新 OAuth 令牌。",
            "刷新本地令牌": "刷新左侧选中账号的 OAuth 令牌并保存新凭据。",
            "同步标签": "读取 OpenAI 订阅身份并更新左侧标签；失败时保留原标签。",
            "上传选中": "把选中的本地 OAuth 凭据同步到 Sub2API，并应用当前上传参数。",
            "远端配置": "打开 Sub2API 连接、上传参数、自动维护和列表显示设置。",
            "监控选中": "将选中的本地账号绑定到唯一的 Sub2API 远端账号并纳入自动维护。",
            "删除账号": "删除本地账号，同时清理已保存的2FA资料，并尝试删除唯一匹配的 Sub2API 账号。",
            "启动自动维护": "周期检查令牌和远端授权；401时按规则刷新或使用2FA资料补授权。",
            "速刷号购买": "打开固定的速刷号购买页面，不会上传本地账号、Token 或2FA凭据。",
            "生成预览": "生成当前选中账号的 Sub2API JSON 预览，并写入输出目录。",
            "复制预览": "把预览框中的完整 JSON 复制到剪贴板。",
            "导出为文件": "选择保存位置导出 Sub2API JSON；导出文件会去掉代理信息。",
            "导入剪贴板": "读取剪贴板中的 JSON 并导入本地凭据。",
            "导入文件": "选择一个 JSON 文件并导入本地凭据。",
            "刷新列表": "重新读取 Sub2API 远端账号、状态和已有额度快照。",
            "更新用量": "调用 Sub2API 用量接口更新选中账号的额度快照。",
            "Sub2API 设置": "配置服务器地址、管理员 API Key、上传参数和自动维护规则。",
            "授权代理设置": "单独配置 OAuth/2FA 使用的代理；支持多行、HTTP、HTTPS、SOCKS5 和 SOCKS5H。",
            "检查更新": "从固定的 test.88api.ai 更新地址检查新版本。",
            "保存设置": "保存当前目录、授权代理和连接参数；Sub2API 管理接口固定直连。",
            "整理导出文件": "整理输出目录中的 Sub2API 文件并生成聚合导出文件。",
            "清理 Tokens": "清理重复或历史 Tokens 文件，只保留每个账号的最佳记录。",
            "清空日志": "清空软件内显示的运行日志，不影响账号和输出文件。",
            "导入文件": "从本地文件导入账号资料或2FA资料。",
            "清空输入": "清空当前编辑框，不删除已加密保存的2FA资料。",
            "智能补授权": "只处理需要授权且能唯一匹配的本地账号；成功后写回 Token。",
            "加密保存资料": "用当前 Windows 用户 DPAPI 加密保存邮箱、密码和2FA密匙。",
            "载入已存资料": "将加密资料载入当前编辑框；不会把资料写入日志。",
            "管理已存资料": "搜索或删除加密保存的2FA资料。",
            "添加到左侧凭据": "把2FA资料对应的邮箱加入左侧，先显示待授权占位记录。",
            "查看本次结果": "查看最近一次批量授权的成功、失败、跳过原因和诊断路径。",
            "维护规则": "查看自动维护的触发条件、重试和安全边界。",
        }

        def label_before(widget) -> str:
            try:
                children = list(widget.master.winfo_children())
                index = children.index(widget)
            except (ValueError, tk.TclError):
                return ""
            for candidate in reversed(children[:index]):
                if candidate.winfo_class() in {"TLabel", "Label"}:
                    text = str(candidate.cget("text") or "").strip()
                    if text:
                        return text
            return ""

        def attach(widget, text):
            if not text or getattr(widget, "_codex_tooltip_attached", False):
                return
            references.append(HoverTooltip(widget, text))
            try:
                widget._codex_tooltip_attached = True
            except Exception:
                pass

        def walk(widget):
            widget_class = widget.winfo_class()
            if widget_class in {"TButton", "Button", "TCheckbutton", "Checkbutton", "TMenubutton"}:
                text = str(widget.cget("text") or widget.cget("textvariable") or "").strip()
                attach(widget, descriptions.get(text, f"点击执行“{text or '此操作'}”。"))
            elif widget_class in {"TEntry", "Entry", "TCombobox", "TSpinbox", "Spinbox"}:
                label = label_before(widget)
                if not label:
                    label = "此输入项"
                attach(widget, f"{label}：在这里输入或选择对应值。")
            elif widget_class in {"Text", "ScrolledText"}:
                attach(widget, "文本输入区：按界面提示输入内容；敏感资料不会写入运行日志。")
            for child in widget.winfo_children():
                walk(child)

        walk(root)
        self._tooltip_references = references

    def _configure_styles(self) -> None:
        self.palette = {
            "bg": "#edf3fa",
            "card": "#ffffff",
            "card_alt": "#f6f8fc",
            "border": "#d9e3ef",
            "border_strong": "#c6d4e3",
            "text": "#1f2937",
            "muted": "#64748b",
            "primary": "#2563eb",
            "primary_hover": "#1d4ed8",
            "primary_soft": "#e8f0ff",
            "accent": "#d97706",
            "accent_soft": "#fff4df",
            "status_bg": "#e8f0ff",
            "success": "#0f9f73",
            "success_soft": "#e7f8f1",
            "danger": "#dc3f52",
            "danger_soft": "#ffebef",
            "info": "#0ea5e9",
            "info_soft": "#e8f6ff",
        }
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.layout('Workspace.TNotebook.Tab', [])
        style.configure(
            'Nav.TButton',
            padding=(8, 5),
            font=('Microsoft YaHei UI', 9),
            width=0,
            background=self.palette["card_alt"],
            foreground=self.palette["muted"],
            bordercolor=self.palette["border"],
        )
        style.map(
            'Nav.TButton',
            background=[
                ('active', self.palette["primary_soft"]),
                ('pressed', self.palette["primary_soft"]),
            ],
            foreground=[
                ('active', self.palette["primary"]),
                ('pressed', self.palette["primary"]),
            ],
        )
        style.configure(
            'ActiveNav.TButton',
            padding=(8, 5),
            font=('Microsoft YaHei UI', 9, 'bold'),
            width=0,
            background=self.palette["primary_soft"],
            foreground=self.palette["primary"],
            bordercolor=self.palette["primary"],
        )
        style.map(
            'ActiveNav.TButton',
            background=[
                ('active', self.palette["primary_soft"]),
                ('pressed', self.palette["primary_soft"]),
            ],
            foreground=[
                ('active', self.palette["primary_hover"]),
                ('pressed', self.palette["primary_hover"]),
            ],
        )
        base_font = ("Microsoft YaHei UI", 10)
        bold_font = ("Microsoft YaHei UI", 10, "bold")
        hero_font = ("Microsoft YaHei UI", 21, "bold")

        self.root.configure(bg=self.palette["bg"])
        style.configure(".", font=base_font, background=self.palette["bg"], foreground=self.palette["text"])
        style.configure("TFrame", background=self.palette["bg"])
        style.configure("Shell.TFrame", background=self.palette["bg"])
        style.configure("Panel.TFrame", background=self.palette["card"])
        style.configure("Card.TFrame", background=self.palette["card"])
        style.configure("CardHost.TFrame", background=self.palette["bg"])
        style.configure("StatusBar.TFrame", background=self.palette["card_alt"])
        style.configure("TLabel", background=self.palette["bg"], foreground=self.palette["text"])
        style.configure("Card.TLabel", background=self.palette["card"], foreground=self.palette["text"])
        style.configure("CardSubtle.TLabel", background=self.palette["card"], foreground=self.palette["muted"])
        style.configure("SectionTitle.TLabel", background=self.palette["card"], foreground=self.palette["text"], font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("SectionBody.TLabel", background=self.palette["card"], foreground=self.palette["muted"])
        style.configure("Stats.TLabel", background=self.palette["card"], foreground=self.palette["muted"], font=bold_font)
        style.configure("Chip.TLabel", background=self.palette["card_alt"], foreground=self.palette["muted"], font=("Microsoft YaHei UI", 9, "bold"), padding=(10, 5))
        style.configure("StatusChip.TLabel", background=self.palette["status_bg"], foreground=self.palette["primary"], font=("Microsoft YaHei UI", 9, "bold"), padding=(12, 6))
        style.configure("StatusBarLabel.TLabel", background=self.palette["card_alt"], foreground=self.palette["muted"], font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("StatusBarValue.TLabel", background=self.palette["card_alt"], foreground=self.palette["text"], font=bold_font)
        style.configure("TCheckbutton", background=self.palette["card"], foreground=self.palette["text"])
        style.map("TCheckbutton", background=[("active", self.palette["card"])], foreground=[("active", self.palette["text"])])
        style.configure(
            "Card.TLabelframe",
            background=self.palette["card"],
            borderwidth=1,
            relief="solid",
            bordercolor=self.palette["border"],
            lightcolor=self.palette["border"],
            darkcolor=self.palette["border"],
        )
        style.configure(
            "Card.TLabelframe.Label",
            font=bold_font,
            foreground=self.palette["text"],
            background=self.palette["card"],
        )
        style.configure(
            "Inner.TLabelframe",
            background=self.palette["card_alt"],
            borderwidth=1,
            relief="solid",
            bordercolor=self.palette["border"],
            lightcolor=self.palette["border"],
            darkcolor=self.palette["border"],
        )
        style.configure(
            "Inner.TLabelframe.Label",
            font=bold_font,
            foreground=self.palette["text"],
            background=self.palette["card_alt"],
        )
        style.configure(
            "TButton",
            padding=(10, 6),
            background=self.palette["card_alt"],
            foreground=self.palette["text"],
            bordercolor=self.palette["border"],
            lightcolor=self.palette["border"],
            darkcolor=self.palette["border"],
        )
        style.map(
            "TButton",
            background=[("active", self.palette["primary_soft"]), ("pressed", self.palette["primary_soft"])],
            foreground=[("active", self.palette["primary"]), ("pressed", self.palette["primary"])],
        )
        style.configure(
            "Primary.TButton",
            padding=(10, 6),
            font=bold_font,
            background=self.palette["primary"],
            foreground="#ffffff",
            bordercolor=self.palette["primary"],
            lightcolor=self.palette["primary"],
            darkcolor=self.palette["primary"],
        )
        style.map(
            "Primary.TButton",
            background=[("active", self.palette["primary_hover"]), ("pressed", self.palette["primary_hover"])],
            foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
        )
        style.configure("Accent.TLabel", font=bold_font, foreground=self.palette["muted"], background=self.palette["bg"])
        style.configure("Hero.TLabel", font=hero_font, foreground=self.palette["text"], background=self.palette["card"])
        style.configure("SubHero.TLabel", font=base_font, foreground=self.palette["muted"], background=self.palette["card"])
        style.configure(
            "TEntry",
            padding=(7, 5),
            fieldbackground=self.palette["card"],
            foreground=self.palette["text"],
            bordercolor=self.palette["border"],
            lightcolor=self.palette["border"],
            darkcolor=self.palette["border"],
            insertcolor=self.palette["text"],
        )
        style.configure(
            "TCombobox",
            padding=(6, 4),
            fieldbackground=self.palette["card"],
            background=self.palette["card"],
            foreground=self.palette["text"],
            bordercolor=self.palette["border"],
            arrowsize=14,
        )
        style.map('TCombobox',
                  foreground=[('disabled', self.palette['muted']), ('readonly', self.palette['text'])],
                  fieldbackground=[('readonly', self.palette['card'])],
                  selectbackground=[('readonly', self.palette['primary_soft'])],
                  selectforeground=[('readonly', self.palette['text'])])
        self.root.option_add('*TCombobox*Listbox.background', self.palette['card'])
        self.root.option_add('*TCombobox*Listbox.foreground', self.palette['text'])
        self.root.option_add('*TCombobox*Listbox.selectBackground', self.palette['primary'])
        self.root.option_add('*TCombobox*Listbox.selectForeground', '#ffffff')
        self.root.option_add('*TCombobox*Listbox.font', base_font)
        style.configure(
            "Treeview",
            rowheight=31,
            font=base_font,
            background=self.palette["card"],
            fieldbackground=self.palette["card"],
            foreground=self.palette["text"],
            bordercolor=self.palette["border"],
            borderwidth=0,
            relief="flat",
            selectborderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            font=bold_font,
            background=self.palette["card_alt"],
            foreground=self.palette["text"],
            bordercolor=self.palette["border"],
            padding=(8, 7),
            relief="flat",
        )
        style.map(
            "Treeview",
            background=[("selected", self.palette["primary_soft"])],
            foreground=[("selected", self.palette["text"])],
        )
        style.map("Treeview.Heading", background=[("active", self.palette["accent_soft"])])
        style.layout(
            "Modern.Vertical.TScrollbar",
            style.layout("Vertical.TScrollbar"),
        )
        style.layout(
            "Modern.Horizontal.TScrollbar",
            style.layout("Horizontal.TScrollbar"),
        )
        for scrollbar_style in (
            "Modern.Vertical.TScrollbar",
            "Modern.Horizontal.TScrollbar",
        ):
            style.configure(
                scrollbar_style,
                # Keep the scrollbar visually subordinate to the account
                # tables. In particular, do not turn the whole thumb bright
                # blue when the pointer happens to pass over a non-scrollable
                # list.
                background="#b8c5d4",
                troughcolor=self.palette["card_alt"],
                bordercolor=self.palette["border"],
                lightcolor="#b8c5d4",
                darkcolor="#b8c5d4",
                arrowcolor="#b8c5d4",
                relief="flat",
                borderwidth=0,
                gripcount=0,
                # The clam theme calculates the real scrollbar thickness from
                # its arrow elements. Removing them collapses the widget to
                # one pixel on Windows, so retain compact muted arrows.
                arrowsize=10,
                sliderlength=36,
            )
            style.map(
                scrollbar_style,
                background=[
                    ("pressed", "#64748b"),
                    ("active", "#8fa0b4"),
                ],
                lightcolor=[
                    ("pressed", "#64748b"),
                    ("active", "#8fa0b4"),
                ],
                darkcolor=[
                    ("pressed", "#64748b"),
                    ("active", "#8fa0b4"),
                ],
                arrowcolor=[
                    ("pressed", "#64748b"),
                    ("active", "#8fa0b4"),
                ],
            )
        style.configure("TNotebook", background=self.palette["bg"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            padding=(16, 10),
            font=bold_font,
            background=self.palette["card_alt"],
            foreground=self.palette["muted"],
            bordercolor=self.palette["border"],
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", self.palette["card"]), ("active", self.palette["accent_soft"])],
            foreground=[("selected", self.palette["text"]), ("active", self.palette["text"])],
        )

    def _add_labeled_entry(self, parent, label: str, variable, browse: bool = False, browse_outputs: bool = False, show: str | None = None):
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.pack(fill=tk.X, pady=4)
        ttk.Label(frame, text=label, width=18, style="Card.TLabel").pack(side=tk.LEFT)
        entry_kwargs: dict[str, Any] = {"textvariable": variable}
        if show is not None:
            entry_kwargs["show"] = show
        ttk.Entry(frame, **entry_kwargs).pack(side=tk.LEFT, fill=tk.X, expand=True)
        if browse:
            ttk.Button(frame, text="浏览", command=self.choose_tokens_dir, width=8).pack(side=tk.LEFT, padx=4)
        if browse_outputs:
            ttk.Button(frame, text="浏览", command=self.choose_outputs_dir, width=8).pack(side=tk.LEFT, padx=4)

    def _add_labeled_spin(self, parent, label: str, variable, min_value: int, max_value: int):
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.pack(fill=tk.X, pady=4)
        ttk.Label(frame, text=label, width=18, style="Card.TLabel").pack(side=tk.LEFT)
        ttk.Spinbox(frame, from_=min_value, to=max_value, textvariable=variable, width=10).pack(side=tk.LEFT)

    def log(self, message: str, level: str = "info") -> None:
        self.log_bus.write(level, redact_error(message))

    def open_auth_proxy_settings(self) -> None:
        """Edit the multiline OAuth/2FA proxy pool in a dedicated window."""
        if self.is_running() or self.auto_refresh_running:
            messagebox.showinfo(
                "请稍候",
                "请先停止自动维护并等待当前任务完成，再调整授权代理。",
                parent=self.root,
            )
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("OAuth/2FA 授权代理设置")
        dialog.geometry("760x470")
        dialog.minsize(620, 360)
        dialog.transient(self.root)
        center_window(dialog, self.root)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=14, style="Card.TFrame")
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(
            frame,
            text="OAuth授权代理（仅OAuth/2FA）",
            style="Stats.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            frame,
            text=(
                "支持多个代理：每行一个，也可以使用逗号或分号分隔。"
                "支持 http、https、socks5、socks5h；留空表示直连。"
            ),
            style="CardSubtle.TLabel",
            wraplength=700,
        ).grid(row=1, column=0, sticky="ew", pady=(6, 8))
        editor = scrolledtext.ScrolledText(
            frame,
            height=12,
            wrap=tk.CHAR,
            font=("Consolas", 10),
            undo=True,
            bg=self.palette["card"],
            fg=self.palette["text"],
            insertbackground=self.palette["text"],
            selectbackground=self.palette["primary_soft"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.palette["border"],
        )
        editor.grid(row=2, column=0, sticky="nsew")
        editor.insert("1.0", self.auth_proxy_var.get())
        editor.focus_set()

        buttons = ttk.Frame(frame, style="Card.TFrame")
        buttons.grid(row=3, column=0, sticky="ew", pady=(12, 0))

        def save():
            value = editor.get("1.0", tk.END).strip()
            candidate = self.current_settings()
            candidate["auth_proxy"] = value
            try:
                authorization_proxy_pool(candidate)
            except ValueError as exc:
                messagebox.showerror("授权代理设置无效", str(exc), parent=dialog)
                return
            self.auth_proxy_var.set(value)
            config = deepcopy(self.config)
            config["auth_proxy"] = value
            with self._state_lock:
                self.config = config
            save_app_config(config)
            self.status_var.set("OAuth/2FA 授权代理已保存")
            self.log("OAuth/2FA 授权代理设置已保存")
            dialog.destroy()

        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(
            buttons,
            text="保存代理",
            command=save,
            style="Primary.TButton",
        ).pack(side=tk.RIGHT, padx=8)
        self._install_default_tooltips(dialog)

    def open_account_purchase_page(self) -> None:
        """Open the fixed account-purchase page without exposing it as a setting."""
        try:
            opened = webbrowser.open(DEFAULT_ACCOUNT_PURCHASE_URL, new=2)
        except Exception as exc:
            self.log(f"打开速刷号购买页面失败：{exc}", "error")
            self.status_var.set("速刷号购买页面打开失败")
            messagebox.showerror(
                "打开购买页面失败",
                f"无法打开默认浏览器。\n{exc}",
                parent=self.root,
            )
            return
        if not opened:
            self.log("默认浏览器未能打开速刷号购买页面", "error")
            self.status_var.set("速刷号购买页面打开失败")
            messagebox.showerror(
                "打开购买页面失败",
                "默认浏览器没有接受打开请求，请手动访问购买入口。",
                parent=self.root,
            )
            return
        self.log(f"已打开速刷号购买页面：{DEFAULT_ACCOUNT_PURCHASE_URL}")
        self.status_var.set("已打开速刷号购买页面")

    def poll_logs(self) -> None:
        for event in self.log_bus.drain():
            ts = time.strftime("%H:%M:%S", time.localtime(event.created_at))
            level = event.level if event.level in {"info", "success", "warning", "error"} else "info"
            marker = {"info": "●", "success": "✓", "warning": "▲", "error": "✕"}[level]
            self.log_text.insert(tk.END, f"[{ts}] {marker} {event.message}\n", level)
            self.log_text.see(tk.END)
        self.root.after(DEFAULT_LOG_POLL_MS, self.poll_logs)

    def clear_logs(self) -> None:
        self.log_text.delete("1.0", tk.END)

    def current_settings(self) -> dict[str, Any]:
        def _int(var, default: int) -> int:
            try:
                return int(var.get() or default)
            except (ValueError, TypeError):
                return default

        from copy import deepcopy
        config = deepcopy(self.config)
        integrations = dict(config.get("integrations") or {})
        sub2api_existing = dict(integrations.get("sub2api") or {})
        for legacy_key in (
            "auth_mode",
            "admin_email",
            "admin_password",
            "access_token",
            "refresh_token",
            "token_expires_at",
        ):
            sub2api_existing.pop(legacy_key, None)
        config["tokens_dir"] = self.tokens_dir_var.get().strip()
        config["outputs_dir"] = self.outputs_dir_var.get().strip()
        # Sub2API management requests intentionally use a direct connection.
        # Remove the legacy setting when an older config is saved.
        config.pop("http_proxy", None)
        config["auth_proxy"] = self.auth_proxy_var.get().strip()
        config["refresh_workers"] = max(1, min(MAX_REFRESH_WORKERS, _int(self.refresh_workers_var, 6)))
        config["upload_workers"] = max(1, min(MAX_UPLOAD_WORKERS, _int(self.upload_workers_var, 4)))
        config["auth_2fa_mode"] = "browser" if str(self.auth2fa_mode_var.get() or "").strip() == "浏览器链" else "protocol"
        config["auth_2fa_live_workers"] = max(1, min(MAX_REFRESH_WORKERS, _int(self.auth2fa_workers_var, 3)))
        config["auth_2fa_live_save_token"] = bool(self.auth2fa_save_token_var.get())
        config["browser_executable_path"] = self.browser_path_var.get().strip()
        config["browser_auth_start_port"] = max(1024, _int(self.browser_debug_port_var, 9333))
        config["auto_refresh_interval_seconds"] = max(30, _int(self.auto_interval_var, 60))
        config["auto_refresh_threshold_seconds"] = max(30, _int(self.auto_threshold_var, 300))
        config["auto_auth_timeout_seconds"] = max(30, _int(self.auto_auth_timeout_var, DEFAULT_AUTH_TIMEOUT_SECONDS))
        config["open_browser_on_auto_auth"] = bool(self.open_browser_var.get())
        config["oauth"] = {
            "auth_url": self.oauth_auth_url_var.get().strip(),
            "token_url": self.oauth_token_url_var.get().strip(),
            "client_id": self.oauth_client_id_var.get().strip(),
            "redirect_uri": self.oauth_redirect_uri_var.get().strip(),
            "scope": self.oauth_scope_var.get().strip(),
        }
        config["integrations"] = {
            "sub2api": {
                **sub2api_existing,
                "api_url": self.sub2api_url_var.get().strip(),
                "api_key": self.sub2api_key_var.get().strip(),
                "group_ids": self.sub2api_group_ids_var.get().strip(),
            },
        }
        return config

    def save_settings(self, reload_tokens: bool = True, notify: bool = True) -> None:
        try:
            config = self.current_settings()
            authorization_proxy_pool(config)
        except ValueError as exc:
            if notify:
                messagebox.showerror("代理设置无效", str(exc), parent=self.root)
            self.log(f"代理设置无效：{exc}", "error")
            return
        with self._state_lock:
            self.config = config
            self.store = TokenStore(config)
        save_app_config(config)
        if notify:
            self.status_var.set("设置已保存")
            self.log("设置已保存")
        if reload_tokens:
            self.reload_tokens(save_first=False)

    def check_for_updates(self, *, silent: bool = False, startup: bool = False) -> None:
        """Check the fixed first-party update endpoint without making it editable."""
        from .constants import DEFAULT_UPDATE_MANIFEST_URL

        if self._update_check_inflight:
            self.log("更新检查已在进行中，跳过重复请求", "warning")
            return
        self._update_check_inflight = True

        def worker():
            try:
                return {
                    "update": fetch_update_info(
                        DEFAULT_UPDATE_MANIFEST_URL,
                        current_version=APP_VERSION,
                    )
                }
            except Exception as exc:
                return {"error": str(exc)}

        def done(result):
            self._update_check_inflight = False
            if result.get("error"):
                self.log(f"检查更新失败：{result['error']}", "error")
                if not silent:
                    messagebox.showerror("检查更新失败", result["error"], parent=self.root)
                return
            info = result.get("update")
            if info is None:
                self.log(f"当前已是最新版本 v{APP_VERSION}")
                if not silent:
                    messagebox.showinfo(
                        "检查更新",
                        f"当前已是最新版本 v{APP_VERSION}",
                        parent=self.root,
                    )
                return
            self._prompt_download_update(info, startup=startup)

        if not startup:
            self.status_var.set("正在检查云端更新")
        def run_check():
            result = worker()
            try:
                self.root.after(0, lambda: done(result))
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=run_check, daemon=True).start()

    def _prompt_download_update(self, info: UpdateInfo, *, startup: bool = False) -> None:
        if self.is_running() or self.auto_refresh_running:
            if startup:
                self._pending_startup_update = info
                self.log("启动检查发现新版本，等待当前启动任务结束后提示更新", "info")
                self.root.after(
                    1000,
                    lambda: self._prompt_download_update(info, startup=True),
                )
                return
            self.log("发现新版本，但当前有任务运行；请任务结束后点击“检查更新”", "warning")
            messagebox.showinfo(
                "发现新版本",
                "发现新版本，但当前正在执行任务。\n任务结束后请再次点击“检查更新”。",
                parent=self.root,
            )
            return
        if self._update_download_inflight:
            self.log("更新包已在下载中，跳过重复操作", "warning")
            return
        notes = info.notes or "此版本没有附加更新说明"
        text = (
            f"发现新版本 v{info.version}\n"
            f"当前版本 v{APP_VERSION}\n\n{notes}\n\n是否下载并更新？"
        )
        if not info.mandatory and not messagebox.askyesno("发现新版本", text, parent=self.root):
            return
        if info.mandatory:
            messagebox.showinfo("必须更新", text, parent=self.root)

        def worker():
            try:
                package = download_update_package(
                    info,
                    progress_cb=self._update_progress,
                )
                return {"package": str(package)}
            except Exception as exc:
                return {"error": str(exc)}

        def done(result):
            self._update_download_inflight = False
            if result.get("error"):
                self.log(f"下载更新失败：{result['error']}", "error")
                messagebox.showerror("下载更新失败", result["error"], parent=self.root)
                return
            package = result["package"]
            if not messagebox.askyesno(
                "准备重启更新",
                "更新包已通过 SHA-256 校验。\n"
                "程序将先保存当前状态并退出，然后替换为新版本。\n"
                "现在重启更新吗？",
                parent=self.root,
            ):
                self.log(f"更新包已下载，稍后可重新检查更新：{package}")
                return
            try:
                self.save_auth2fa_credentials(silent=True)
                launch_update(package)
                self.log("更新程序已启动，主程序即将安全退出")
                self.root.destroy()
            except Exception as exc:
                self.log(f"启动更新失败：{exc}", "error")
                messagebox.showerror("启动更新失败", str(exc), parent=self.root)

        self._update_download_inflight = True
        self.run_background("正在下载云端更新", worker, done)

    def _update_progress(self, received: int, total: int | None) -> None:
        if total:
            percent = min(100, int(received * 100 / total))
            self.root.after(0, lambda: self.status_var.set(f"正在下载更新 {percent}%"))
        else:
            self.root.after(
                0,
                lambda: self.status_var.set(
                    f"正在下载更新 {received // 1024 // 1024} MB"
                ),
            )

    def persist_runtime_settings(self, settings: dict[str, Any]) -> None:
        with self._state_lock:
            self.config = settings
            self.store = TokenStore(settings)
        save_app_config(settings)

    def choose_tokens_dir(self) -> None:
        selected = filedialog.askdirectory(
            title="选择 Tokens 目录",
            initialdir=self.tokens_dir_var.get().strip() or ".",
            parent=self.root,
        )
        if selected:
            self.tokens_dir_var.set(selected)

    def choose_outputs_dir(self) -> None:
        selected = filedialog.askdirectory(
            title="选择输出目录",
            initialdir=self.outputs_dir_var.get().strip() or ".",
            parent=self.root,
        )
        if selected:
            self.outputs_dir_var.set(selected)

    def clear_filters(self) -> None:
        self.search_var.set("")
        self.plan_filter_var.set("全部标签")
        self.status_filter_var.set("全部状态")
        self.reload_tokens(save_first=False)

    def apply_quick_filter(self, plan: str, status: str = "全部状态") -> None:
        self.search_var.set("")
        self.plan_filter_var.set(plan)
        self.status_filter_var.set(status)
        self.reload_tokens(save_first=False)

    def set_running(self, running: bool, status: str = "") -> None:
        with self._running_job_lock:
            self.running_job = running
        self.status_var.set(status or ("任务进行中" if running else "就绪"))

    def is_running(self) -> bool:
        with self._running_job_lock:
            return self.running_job

    def run_background(self, status: str, worker, on_done):
        with self._running_job_lock:
            busy = self.running_job or self.auto_refresh_running
            if not busy:
                self.running_job = True
        # Tk modal dialogs pump timers and completion callbacks. Never open one
        # while holding this non-reentrant lock: those callbacks call is_running.
        # Keep repeated clicks non-modal and leave the current job untouched.
        if busy:
            reason = "自动维护正在运行，请先停止维护" if self.auto_refresh_running else "已有任务在运行，请等待完成"
            self.log(f"未启动「{status}」：{reason}", "warning")
            return
        self.status_var.set(status)

        def runner():
            try:
                result = worker()
            except Exception as exc:
                traceback_text = traceback.format_exc(limit=5)
                self.log(f"任务异常: {exc}", "error")
                self.log(traceback_text, "error")
                result = {"error": str(exc)}
            self.root.after(0, lambda: on_done(result))

        threading.Thread(target=runner, daemon=True).start()

    def with_progress(self, job_name: str):
        def _progress(done: int, total_count: int, email: str):
            self.root.after(0, lambda: self.status_var.set(f"{job_name} {done}/{total_count} {email}"))
            self.log(f"{job_name} 进度 {done}/{total_count}: {email}")

        return _progress

    def update_ui_timer(self) -> None:
        if not self.is_running():
            self.reload_tokens(save_first=False)
        self.root.after(DEFAULT_UI_REFRESH_MS, self.update_ui_timer)
