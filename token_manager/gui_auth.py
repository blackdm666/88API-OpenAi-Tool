from __future__ import annotations

import threading
from pathlib import Path
from copy import deepcopy
from .maintenance import recovery_cycle
from .credential_vault import CredentialVault
from .integrations import fetch_sub2api_proxy_endpoints

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from .auth_batch import run_checked_authorization, remove_account_lines

from tools.auth_2fa_browser import run_authorize_batch_lines_browser
from tools.auth_2fa_live import parse_account_lines, run_authorize_batch_lines
from .constants import DEFAULT_AUTH_TIMEOUT_SECONDS
from .sub2api_policy import proxy_candidates
from .oauth import browser_assisted_authorize, exchange_callback, generate_oauth_start
from .services import refresh_record
from .gui_widgets import ModernScrollbar, center_window


def saved_credential_lines(accounts: dict, allowed_emails=None) -> str:
    """Build authorization input only in memory; never place it in the editor."""
    allowed = {str(email).strip().casefold() for email in (allowed_emails or [])}
    lines = []
    for item in accounts.values() if isinstance(accounts, dict) else []:
        if not isinstance(item, dict):
            continue
        email = str(item.get('email') or '').strip()
        password = str(item.get('password') or '')
        totp_secret = str(item.get('totp_secret') or '').strip()
        if email and (not allowed or email.casefold() in allowed) and password and totp_secret:
            lines.append(f'{email}----{password}----{totp_secret}')
    return '\n'.join(lines)


class GUIAuthMixin:
    def delete_saved_auth2fa_accounts(self, emails):
        if self.is_running() or self.auto_refresh_running:
            raise ValueError('请先停止自动维护并等待授权任务完成，再删除资料')
        removed = CredentialVault().delete_accounts(emails)
        # Clearing the editor as well prevents save-on-close from resurrecting
        # credentials removed from the encrypted vault.
        text = remove_account_lines(self.auth2fa_input.get('1.0', 'end'), emails)
        self.auth2fa_input.delete('1.0', 'end')
        self.auth2fa_input.insert('1.0', text)
        self.update_auth2fa_input_stats()
        self.update_vault_status()
        self.log(f'已删除 {len(removed)} 个账号的加密2FA资料')
        return removed

    def manage_auth2fa_credentials(self):
        if self.is_running() or self.auto_refresh_running:
            self.log('请先停止自动维护并等待当前任务完成，再管理2FA资料', 'warning')
            return
        try:
            emails = sorted(CredentialVault().load())
        except Exception as exc:
            messagebox.showerror('资料库读取失败', str(exc), parent=self.root)
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('管理已存 2FA 资料')
        dialog.geometry('640x440')
        dialog.transient(self.root)
        center_window(dialog, self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(frame, text='搜索邮箱；支持多选。删除仅移除已存密码/2FA密匙及输入框对应行，保留本地Token和Sub2API账号。', wraplength=590).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 8))
        search = tk.StringVar()
        ttk.Entry(frame, textvariable=search).grid(row=1, column=0, columnspan=2, sticky='ew', pady=(0, 8))
        tree = ttk.Treeview(frame, columns=('email',), show='headings', selectmode='extended')
        tree.heading('email', text='已保存的账号邮箱')
        tree.column('email', width=480)
        tree.grid(row=2, column=0, sticky='nsew')
        scrollbar = ModernScrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=2, column=1, sticky='ns', padx=(4, 0))
        count = tk.StringVar()
        def populate(*_):
            selected = set(tree.selection())
            for row in tree.get_children():
                tree.delete(row)
            for email in emails:
                if search.get().strip().casefold() in email.casefold():
                    tree.insert('', 'end', iid=email, values=(email,))
                    if email in selected:
                        tree.selection_add(email)
            count.set(f'已保存 {len(emails)} · 显示 {len(tree.get_children())}')
        def delete_selected():
            selected = list(tree.selection())
            if not selected:
                return
            if not messagebox.askyesno('删除2FA资料', f'确认删除选中的 {len(selected)} 个账号资料？\n删除后自动重新登录需要再次导入这些资料。', parent=dialog):
                return
            try:
                self.delete_saved_auth2fa_accounts(selected)
                emails[:] = sorted(CredentialVault().load())
                populate()
            except Exception as exc:
                messagebox.showerror('删除失败', str(exc), parent=dialog)
        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(10, 0))
        ttk.Label(buttons, textvariable=count).pack(side='left')
        ttk.Button(buttons, text='删除所选资料', command=delete_selected).pack(side='right')
        ttk.Button(buttons, text='全选显示结果', command=lambda: tree.selection_set(tree.get_children())).pack(side='right', padx=6)
        search.trace_add('write', populate)
        populate()

    def update_vault_status(self):
        try:
            vault = CredentialVault()
            count = len(vault.load())
            self.auth2fa_vault_var.set(f'已加密保存 {count} 个账号 · 文档/sub2api/outputs')
        except Exception as exc:
            self.auth2fa_vault_var.set(str(exc))

    def save_auth2fa_credentials(self, *, silent=False):
        accounts, errors = parse_account_lines(self.auth2fa_input.get('1.0', 'end'))
        if not accounts and not errors:
            return True
        try:
            if errors:
                raise ValueError('账号资料格式有误，未保存；请检查无效行')
            count = CredentialVault().save_accounts(accounts)
            self.update_vault_status()
            if not silent:
                self.log(f'2FA 资料已加密保存，共 {count} 个账号')
            return True
        except Exception as exc:
            self.auth2fa_vault_var.set('资料未保存：' + str(exc))
            self.log('2FA 资料保存失败：' + str(exc), 'error')
            return False

    def load_auth2fa_credentials(self):
        if self.is_running() or self.auto_refresh_running:
            self.log('请先停止维护并等待任务完成，再载入授权资料', 'warning')
            return
        try:
            accounts = CredentialVault().load()
            text = '\n'.join(f"{a['email']}----{a['password']}----{a['totp_secret']}" for a in accounts.values())
            self.auth2fa_input.delete('1.0', 'end')
            self.auth2fa_input.insert('1.0', text)
            self.update_auth2fa_input_stats()
            self.update_vault_status()
        except Exception as exc:
            self.auth2fa_vault_var.set(str(exc))

    def update_auth2fa_mode_hint(self, *, announce: bool = True, persist: bool = True) -> None:
        mode_label = str(self.auth2fa_mode_var.get() or "协议链").strip() or "协议链"
        if mode_label == "浏览器链":
            browser_name = Path(str(self.browser_path_var.get() or "").strip()).name
            port = int(self.browser_debug_port_var.get() or 9333)
            if browser_name:
                hint = f"当前模式 浏览器链 已选中 走独立浏览器 端口 {port}  浏览器 {browser_name}"
            else:
                hint = "当前模式 浏览器链 已选中 但浏览器路径还没填"
        else:
            hint = "当前模式 协议链 已选中 走纯协议授权"
        self.auth2fa_mode_hint_var.set(hint)
        self.root.update_idletasks()
        if persist:
            self.save_settings(reload_tokens=False, notify=False)
        if announce:
            self.status_var.set(f"2FA 模式已切到 {mode_label}")
            self.log(f"2FA 授权模式已切到 {mode_label}")

    def on_auth2fa_mode_changed(self, _event=None) -> None:
        self.update_auth2fa_mode_hint(announce=True, persist=True)

    def import_auth2fa_accounts_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择 2FA 授权账号文件",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            parent=self.root,
        )
        if not selected:
            return
        try:
            text = Path(selected).read_text(encoding="utf-8-sig")
        except Exception as exc:
            messagebox.showerror("错误", str(exc))
            self.log(f"读取 2FA 账号文件失败: {exc}", "error")
            return
        self.auth2fa_input.delete("1.0", "end")
        self.auth2fa_input.insert("1.0", text)
        self.auth2fa_output_var.set(f"已导入 {selected}")
        self.update_auth2fa_input_stats()
        self.save_auth2fa_credentials()
        self.log(f"已导入 2FA 账号文件: {selected}")

    def clear_auth2fa_accounts_text(self) -> None:
        self.auth2fa_input.delete("1.0", "end")
        self.auth2fa_output_var.set("")
        self.update_auth2fa_input_stats()

    def update_auth2fa_input_stats(self) -> None:
        raw_text = self.auth2fa_input.get("1.0", "end")
        line_count = sum(1 for line in raw_text.splitlines() if str(line or "").strip() and not str(line or "").strip().startswith("#"))
        accounts, errors = parse_account_lines(raw_text)
        self.auth2fa_stats_var.set(f"待授权 {len(accounts)}  无效 {len(errors)}  原始 {line_count}")

    def start_auth2fa_batch(self) -> None:
        if self.is_running() or self.auto_refresh_running:
            self.log('已有任务或自动维护在运行，未启动新的授权任务', 'warning')
            return
        if not self.save_auth2fa_credentials():
            return
        # The encrypted vault is the source of truth after first import. The
        # editor may be empty or intentionally kept hidden; smart recovery can
        # still authorize every saved identity without asking the user to paste
        # passwords/TOTP secrets again.
        local_records = self.store.load_all()
        local_emails = [record.get('email') for record in local_records if record.get('email')]
        try:
            raw_text = saved_credential_lines(CredentialVault().load(), local_emails)
        except Exception as exc:
            messagebox.showerror('资料库读取失败', str(exc))
            return
        if not raw_text.strip():
            raw_text = self.auth2fa_input.get("1.0", "end")
        accounts, errors = parse_account_lines(raw_text)
        if not accounts:
            messagebox.showerror("错误", "左侧没有可补授权的本地凭据；已删除的账号视为废弃")
            self.update_auth2fa_input_stats()
            return
        self.save_settings(reload_tokens=False, notify=False)
        settings = self.current_settings()
        mode = str(settings.get("auth_2fa_mode") or "protocol").strip().lower()
        workers = max(1, int(settings.get("auth_2fa_live_workers") or 1))
        save_token = bool(settings.get("auth_2fa_live_save_token", False))
        browser_path = str(settings.get("browser_executable_path") or "").strip()
        browser_debug_port = int(settings.get("browser_auth_start_port") or 9333)
        timeout = int(settings.get("auto_auth_timeout_seconds") or DEFAULT_AUTH_TIMEOUT_SECONDS)
        output_dir = str((settings.get("outputs_dir") or "")).strip()
        folder_name = "auth_2fa_browser" if mode == "browser" else "auth_2fa_live"
        save_dir = None if not output_dir else str(Path(output_dir).expanduser() / folder_name)

        def gui_log(message: str) -> None:
            self.log(message)

        def progress(done: int, total_count: int, email: str) -> None:
            prefix = "浏览器授权" if mode == "browser" else "2FA授权"
            self.root.after(0, lambda: self.status_var.set(f"{prefix} {done}/{total_count} {email}"))
            self.root.after(0, lambda: self.auth2fa_stats_var.set(f"执行中 {done}/{total_count}"))

        def worker():
            options = dict(workers=workers, save_dir=save_dir, save_token=save_token,
                           include_secrets=False, quiet=True, log_fn=gui_log, progress_cb=progress)
            proxy_ids = proxy_candidates((settings.get('integrations') or {}).get('sub2api') or {})
            if proxy_ids:
                endpoints = fetch_sub2api_proxy_endpoints(
                    settings, proxy_url=settings.get('http_proxy', ''), proxy_ids=proxy_ids
                )
                proxy_pool = [item['url'] for item in endpoints]
                if not proxy_pool:
                    raise ValueError('配置的 Sub2API 代理池没有可用代理，无法启动2FA授权')
                options['proxy_pool'] = proxy_pool
            runner = run_authorize_batch_lines
            if mode == 'browser':
                runner = run_authorize_batch_lines_browser
                options.update(browser_path=browser_path, debug_port_base=browser_debug_port, timeout=timeout)
            return run_checked_authorization(raw_text, settings, self.store.load_all(), runner, options, log_fn=gui_log)

        def done(result):
            self.set_running(False, "2FA 批量授权结束")
            if result.get("error"):
                messagebox.showerror("错误", result["error"])
                return
            summary_path = str(result.get("summary_path") or "")
            self.auth2fa_output_var.set(summary_path)
            self.auth2fa_stats_var.set(
                f"完成 成功 {int(result.get('success_count') or 0)} 失败 {int(result.get('fail_count') or 0)} 跳过 {int(result.get('skipped_count') or 0)}"
            )
            if save_token and int(result.get("success_count") or 0) > 0:
                self.reload_tokens(save_first=False)
            summary_title = "浏览器链批量授权" if mode == "browser" else "2FA 批量授权"
            self.log(f"{summary_title}完成 成功={int(result.get('success_count') or 0)} 失败={int(result.get('fail_count') or 0)} 跳过={int(result.get('skipped_count') or 0)}")
            if summary_path:
                self.log(f"批量汇总: {summary_path}")
            messagebox.showinfo(
                "完成",
                f"成功 {int(result.get('success_count') or 0)} 个\n"
                f"失败 {int(result.get('fail_count') or 0)} 个\n"
                f"跳过 {int(result.get('skipped_count') or 0)} 个（原因见日志）\n"
                f"{summary_path}",
            )

        status_text = "正在核对 Sub2API 授权状态"
        self.run_background(status_text, worker, done)

    def generate_manual_url(self) -> None:
        self.save_settings(reload_tokens=False, notify=False)
        self.manual_oauth_start = generate_oauth_start(self.config)
        self.url_text.delete("1.0", "end")
        self.url_text.insert("1.0", self.manual_oauth_start.auth_url)
        self.copy_to_clipboard(self.manual_oauth_start.auth_url, "授权 URL 已复制")
        self.log("已生成手动授权 URL")

    def submit_callback(self) -> None:
        if self.auto_refresh_running or self.is_running():
            messagebox.showinfo('请先停止维护', '为避免同时刷新同一个账号，请停止自动维护并等待当前任务完成后再授权。')
            return
        if not self.manual_oauth_start:
            messagebox.showerror("错误", "请先生成授权 URL")
            return
        callback_url = self.callback_entry.get().strip()
        if not callback_url:
            messagebox.showerror("错误", "请输入回调 URL")
            return
        self.save_settings(reload_tokens=False, notify=False)
        try:
            token_data = exchange_callback(
                callback_url,
                self.manual_oauth_start,
                self.config,
                proxy_url=self.config.get("http_proxy", ""),
            )
            path = self.store.save_token_response(token_data, metadata={"auth_mode": "manual"})
        except Exception as exc:
            messagebox.showerror("错误", str(exc))
            self.log(f"手动授权失败: {exc}", "error")
            return
        self.log(f"手动授权成功: {path}")
        self.callback_entry.delete(0, "end")
        self.reload_tokens()
        messagebox.showinfo("完成", f"Token 已保存\n{path}")

    def start_auto_auth(self) -> None:
        if self.is_running() and getattr(self, 'auto_auth_running', False):
            self.auto_auth_stop.set()
            self.auto_auth_button.config(text='正在停止自动授权', state='disabled')
            self.log('自动授权停止请求已提交，正在结束回调等待')
            return
        if self.is_running() or self.auto_refresh_running:
            self.log('已有任务或自动维护在运行，未启动自动授权', 'warning')
            return
        settings = self.current_settings()
        proxy = settings.get("http_proxy", "")
        timeout = int(settings.get("auto_auth_timeout_seconds") or DEFAULT_AUTH_TIMEOUT_SECONDS)
        open_browser = bool(settings.get("open_browser_on_auto_auth", True))

        self.auto_auth_stop.clear()
        self.auto_auth_running = True
        self.auto_auth_button.config(text='停止自动授权')

        def worker():
            try:
                return browser_assisted_authorize(
                    settings,
                    proxy_url=proxy,
                    timeout=timeout,
                    open_browser=open_browser,
                    log_fn=self.log,
                    cancelled=self.auto_auth_stop,
                )
            except RuntimeError:
                if self.auto_auth_stop.is_set():
                    return {"cancelled": True}
                raise

        def done(result):
            self.auto_auth_running = False
            self.auto_auth_button.config(text='启动自动授权', state='normal')
            stopped = self.auto_auth_stop.is_set()
            self.set_running(False, "自动授权已停止" if stopped else "自动授权结束")
            if stopped or result.get("cancelled"):
                self.log('自动授权已停止')
                return
            if result.get("error"):
                messagebox.showerror("错误", result["error"])
                return
            token_data = result["token_data"]
            path = self.store.save_token_response(token_data, metadata={"auth_mode": "auto_browser"})
            self.log(f"自动授权成功: {path}")
            self.reload_tokens()
            messagebox.showinfo("完成", f"自动授权成功\n{path}")

        self.run_background("正在等待自动授权回调", worker, done)

    def toggle_auto_refresh(self) -> None:
        if self.auto_refresh_running:
            self.maintenance_stop.set()
            self.auto_refresh_button.config(text="正在停止…", state="disabled")
            self.log("停止请求已提交，当前网络请求返回后退出")
            return
        if self.auto_refresh_thread and self.auto_refresh_thread.is_alive():
            return
        if self.is_running():
            messagebox.showinfo("请稍候", "请等待当前任务结束")
            return
        self.save_settings(reload_tokens=False, notify=False)
        self.maintenance_stop.clear()
        self.auto_refresh_running = True
        self.auto_refresh_button.config(text="停止自动维护")
        self.log("自动维护已启动：本地到期检查 + 已开启账号的Sub2API恢复")
        self.auto_refresh_thread = threading.Thread(target=self.auto_refresh_worker, daemon=True)
        self.auto_refresh_thread.start()

    def auto_refresh_worker(self) -> None:
        try:
            while not self.maintenance_stop.is_set():
                with self._state_lock:
                    settings = deepcopy(self.config)
                    store = self.store
                try:
                    all_records = store.load_all()
                    threshold = int(settings.get("auto_refresh_threshold_seconds") or 300)
                    # Monitored accounts are handled by one recovery loop. Never
                    # rotate their refresh tokens concurrently with local expiry.
                    records = [r for r in all_records if not (r.get("sub2api_recovery") or {}).get("enabled")
                               and 0 < r["_remaining_seconds"] <= threshold and r.get("refresh_token")]
                    for record in records:
                        if self.maintenance_stop.is_set():
                            break
                        try:
                            refresh_record(store, record, settings, proxy_url=settings.get("http_proxy", ""), log_fn=self.log)
                        except Exception as exc:
                            self.log(f"本地到期刷新失败：{exc}", "error")
                    if not self.maintenance_stop.is_set() and any((r.get("sub2api_recovery") or {}).get("enabled") for r in all_records):
                        result = recovery_cycle(store, settings, log_fn=self.log, cancelled=self.maintenance_stop.is_set)
                        self.log(f"远端检查 {result['checked']}，恢复 {result['recovered']}，需关注 {result['blocked']}")
                        self.root.after(0, lambda snapshot=result['records']: self.update_recovery_snapshot(snapshot))
                    self.root.after(0, lambda: self.reload_tokens(save_first=False))
                except Exception as exc:
                    self.log(f"自动维护异常: {exc}", "error")
                self.maintenance_stop.wait(max(30, int(settings.get("auto_refresh_interval_seconds") or 60)))
        finally:
            def stopped():
                self.auto_refresh_running = False
                self.auto_refresh_button.config(text="启动自动维护", state="normal")
                self.status_var.set("自动维护已停止")
            self.root.after(0, stopped)
