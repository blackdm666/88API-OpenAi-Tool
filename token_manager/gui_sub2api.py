from __future__ import annotations

from typing import Any
import threading
import time

import tkinter as tk
from tkinter import messagebox, ttk
from .gui_widgets import CheckList, center_window
from .integrations import (
    bulk_update_sub2api_accounts,
    fetch_sub2api_usage,
    fetch_sub2api_accounts,
    fetch_sub2api_concurrency_snapshot,
    update_sub2api_account_settings,
)
from .usage_display import snapshot_usage, quota_cell, sort_account_rows, scheduling_cell, concurrency_cell
from .recovery_support import remote_health
from .sub2api_policy import normalize_server_url, match_remote, default_list_group_ids
from .utils import openai_plan_label, sub2api_plan_type
from .gui_sub2api_settings import FINGERPRINTS, WS_MODES

from .services import (
    delete_sub2api_remote_records,
    fetch_sub2api_remote_snapshot,
    is_sub2api_invalidated,
    refresh_sub2api_remote_records,
    set_sub2api_remote_records_schedulable,
)


class GUISub2APIMixin:
    def show_sub2api_context_menu(self, event):
        row = self.sub2api_tree.identify_row(event.y)
        if row and row not in self.sub2api_tree.selection():
            self.sub2api_tree.selection_set(row)
        if not row:
            return
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label='启用调度（选中）', command=lambda: self.set_selected_sub2api_schedulable(True))
        menu.add_command(label='停用调度（选中）', command=lambda: self.set_selected_sub2api_schedulable(False))
        menu.add_separator()
        menu.add_command(label='刷新令牌', command=self.refresh_selected_sub2api_remote)
        selected_count = len(self.selected_sub2api_pool_records())
        menu.add_command(
            label='编辑账号' if selected_count == 1 else f'批量编辑账号（{selected_count}）',
            command=self.edit_selected_sub2api_remote,
        )
        menu.add_separator()
        menu.add_command(label='删除选中…', command=self.delete_selected_sub2api_records)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def reset_sub2api_default_groups(self):
        cfg = (self.config.get('integrations') or {}).get('sub2api') or {}
        self.sub2api_group_filters.clear()
        self.sub2api_group_filter_ids = default_list_group_ids(cfg.get('default_list_group_ids', '2'))

    def initial_remote_load(self):
        cfg=(self.current_settings().get('integrations') or {}).get('sub2api') or {}
        if cfg.get('api_url') and (cfg.get('api_key') or cfg.get('access_token') or (cfg.get('admin_email') and cfg.get('admin_password'))) and not self.is_running() and not self.auto_refresh_running:
            self.refresh_sub2api_accounts()

    def update_recovery_snapshot(self, records):
        self.sub2api_records = records
        now=time.time()
        for account_id in list(self.sub2api_usage_cache):
            if now-self.sub2api_usage_cache_time.get(account_id,0)>=60:
                self.sub2api_usage_cache.pop(account_id,None)
                self.sub2api_usage_cache_time.pop(account_id,None)
        self.usage_sync_var.set("服务器快照 · 同步 " + time.strftime("%H:%M:%S") + " · 每60秒")
        self.sub2api_index = self._build_sub2api_email_index(records)
        self.populate_sub2api_tree()

    def update_concurrency_snapshot(self, records):
        by_id = {record.get('id'): record for record in records}
        for record in self.sub2api_records:
            live = by_id.get(record.get('id'))
            if live:
                for key in ('status', 'schedulable', 'concurrency', 'current_concurrency', 'active_sessions', 'current_rpm', 'temp_unschedulable_until', 'rate_limit_reset_at', 'overload_until'):
                    if key in live:
                        record[key] = live[key]
        self.sub2api_concurrency_updated_at = time.time()
        self.usage_sync_var.set('服务器快照 · 用量60秒 · 并发5秒 · 同步 ' + time.strftime('%H:%M:%S'))
        self.populate_sub2api_tree()

    def poll_sub2api_concurrency(self):
        self.root.after(5000, self.poll_sub2api_concurrency)
        if self.is_running() or self.auto_refresh_running or self._concurrency_inflight or not self.sub2api_records:
            return
        settings = self.current_settings()
        try:
            server = normalize_server_url(settings['integrations']['sub2api']['api_url'])
        except ValueError:
            return
        if server != self.sub2api_snapshot_server:
            return
        self._concurrency_inflight = True
        def work():
            try:
                records = fetch_sub2api_concurrency_snapshot(settings, proxy_url=settings.get('http_proxy', ''), filters={'platform': 'openai'})
                error = None
            except Exception as exc:
                records, error = [], exc
            def done():
                self._concurrency_inflight = False
                if error is not None:
                    self.usage_sync_var.set('服务器快照 · 并发同步失败，保留上次数据')
                    return
                self.update_concurrency_snapshot(records)
            try:
                self.root.after(0, done)
            except (RuntimeError, tk.TclError):
                pass
        threading.Thread(target=work, daemon=True).start()

    def _sub2api_error_summary(self, record: dict[str, Any], max_len: int = 96) -> str:
        raw = str(record.get("error_message") or "").strip().replace("\r", " ").replace("\n", " ")
        text = " ".join(raw.split())
        if not text:
            return "-"
        if len(text) <= max_len:
            return text
        return f"{text[: max_len - 1]}…"

    def _sub2api_groups_text(self, record: dict[str, Any], max_len: int = 32) -> str:
        groups = [str(item).strip() for item in (record.get("group_names") or []) if str(item).strip()]
        text = ", ".join(groups) or "-"
        if len(text) <= max_len:
            return text
        return f"{text[: max_len - 1]}…"

    def _sub2api_account_label(self, record: dict[str, Any]) -> str:
        remote_label = openai_plan_label(sub2api_plan_type(record))
        if remote_label != "Unknown":
            return remote_label
        email = str(record.get('email') or '').strip().lower()
        local = self.local_record_index.get(email)
        if local:
            local_label = self.plan_label(local)
            if local_label != "Unknown":
                return local_label
        return remote_label

    def _sub2api_flags_text(self, record: dict[str, Any]) -> str:
        flags: list[str] = []
        if not record.get("schedulable", True):
            flags.append("停调度")
        if record.get("auto_pause_on_expired"):
            flags.append("到期暂停")
        if record.get("proxy_id"):
            flags.append("代理")
        return " ".join(flags) or "-"

    def clear_sub2api_filters(self) -> None:
        self.sub2api_search_var.set("")
        self.sub2api_group_filter_var.set("全部分组")
        self.reset_sub2api_default_groups()
        self.sub2api_status_filter_var.set("全部状态")
        self.sub2api_type_filter_var.set("oauth")
        self.populate_sub2api_tree()

    def filter_sub2api_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        search = self.sub2api_search_var.get().strip().lower()
        selected_groups = {g.lower() for g in self.sub2api_group_filters}
        status_filter = self.sub2api_status_filter_var.get().strip().lower()
        type_filter = self.sub2api_type_filter_var.get().strip().lower()
        filtered: list[dict[str, Any]] = []
        for record in records:
            email = str(record.get("email") or "").strip().lower()
            name = str(record.get("name") or "").strip().lower()
            groups = " ".join(str(item).strip().lower() for item in (record.get("group_names") or []))
            error_message = str(record.get("error_message") or "").strip().lower()
            record_type = str(record.get("type") or "").strip().lower()
            status = str(record.get("status") or "").strip().lower()
            if search and search not in email and search not in name and search not in groups and search not in error_message:
                continue
            if selected_groups:
                group_names = [str(item).strip().lower() for item in (record.get("group_names") or [])]
                if not selected_groups.intersection(group_names):
                    continue
            elif self.sub2api_group_filter_ids and not self.sub2api_group_filter_ids.intersection(record.get('group_ids') or []):
                continue
            if type_filter and type_filter != "全部类型".lower() and type_filter != record_type:
                continue
            if status_filter == "invalidated" and not is_sub2api_invalidated(record):
                continue
            if status_filter == "unschedulable" and record.get("schedulable") is not False:
                continue
            if status_filter not in {"", "全部状态".lower(), "invalidated", "unschedulable"} and status_filter != status:
                continue
            filtered.append(record)
        return filtered

    def _update_sub2api_group_filter_values(self):
        names = sorted(self.sub2api_group_filters)
        if not names:
            catalog = self.sub2api_group_catalog()
            names = [f'{catalog.get(i, "分组")} (#{i})' for i in sorted(self.sub2api_group_filter_ids)]
        self.sub2api_group_filter_var.set('、'.join(names) if names else '全部分组（可多选）')

    def sub2api_group_catalog(self):
        catalog = {int(g['id']):g['name'] for g in self.sub2api_groups}
        for record in self.sub2api_records:
            for group in record.get('groups') or []:
                catalog.setdefault(int(group['id']), group.get('name') or '分组')
            for group_id in record.get('group_ids') or []:
                catalog.setdefault(int(group_id), '分组')
        return catalog

    def choose_sub2api_group_filters(self):
        dialog = tk.Toplevel(self.root)
        dialog.title('筛选分组 · 多选')
        dialog.geometry('470x380')
        dialog.transient(self.root)
        center_window(dialog, self.root)
        dialog.grab_set()
        catalog = self.sub2api_group_catalog()
        choices = CheckList(dialog, [(i, f'{name} (#{i})') for i,name in sorted(catalog.items())], self.sub2api_group_filter_ids)
        choices.pack(fill='both', expand=True, padx=16, pady=16)
        buttons = ttk.Frame(dialog, padding=12)
        buttons.pack(fill='x')
        def apply():
            self.sub2api_group_filters.clear()
            self.sub2api_group_filter_ids = set(choices.selected())
            dialog.destroy()
            self.populate_sub2api_tree()
        ttk.Button(buttons, text='全选', command=choices.select_all).pack(side='left')
        ttk.Button(buttons, text='清空', command=lambda: choices.select_all(False)).pack(side='left', padx=6)
        ttk.Button(buttons, text='应用筛选', command=apply, style='Primary.TButton').pack(side='right')

    def populate_sub2api_tree(self) -> None:
        selected = set(self.sub2api_tree.selection())
        xview=self.sub2api_tree.xview()[0]
        yview=self.sub2api_tree.yview()[0]
        for item in self.sub2api_tree.get_children():
            self.sub2api_tree.delete(item)

        self.sub2api_row_index = {}
        self.sub2api_invalidated_row_index = {}
        self.filtered_sub2api_records = self.filter_sub2api_records(self.sorted_sub2api_records())
        self.invalidated_sub2api_records = [record for record in self.filtered_sub2api_records if is_sub2api_invalidated(record)]
        self._update_sub2api_group_filter_values()

        for idx, record in enumerate(self.filtered_sub2api_records, start=1):
            status = str(record.get("status") or "").strip().lower()
            iid = self._build_sub2api_row_id(record, idx)
            self.sub2api_row_index[iid] = record
            tags: tuple[str, ...] = ()
            if is_sub2api_invalidated(record):
                tags = ("invalidated",)
            elif status == "error":
                tags = ("error",)
            elif record.get("schedulable") is False or status == "inactive":
                tags = ("warning",)
            self.sub2api_tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(
                    record.get("id", ""),
                    record.get("name") or record.get("email", ""),
                    self._sub2api_account_label(record),
                    record.get("status", ""),
                    scheduling_cell(record),
                    concurrency_cell(record),
                    quota_cell(self.usage_for_record(record),"seven_day"),
                    record.get('priority', ''),
                    self._sub2api_error_summary(record),
                ),
                tags=tags,
            )

            if iid in selected:
                self.sub2api_tree.selection_add(iid)

        available_count = sum(
            1 for record in self.filtered_sub2api_records
            if remote_health(record) == '已启用·可调度'
        )
        current_concurrency = sum(
            self._numeric_account_value(record.get('current_concurrency'))
            for record in self.filtered_sub2api_records
        )
        concurrency_limit = sum(
            self._numeric_account_value(record.get('concurrency'))
            for record in self.filtered_sub2api_records
        )
        self.sub2api_stats_var.set(
            f'账号数量：{len(self.filtered_sub2api_records)}    '
            f'可用账号：{available_count}    '
            f'并发请求：{current_concurrency}    '
            f'并发上限：{concurrency_limit}'
        )
        self.sub2api_invalidated_stats_var.set(f"失效记录 {len(self.invalidated_sub2api_records)}")
        self.sub2api_tree.xview_moveto(xview)
        self.sub2api_tree.yview_moveto(yview)

    @staticmethod
    def _numeric_account_value(value) -> int:
        try:
            number = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return max(0, number)

    @staticmethod
    def _build_sub2api_row_id(record: dict[str, Any], idx: int) -> str:
        return f"{int(record.get('id') or 0)}|{str(record.get('email') or record.get('name') or idx).strip()}"

    @staticmethod
    def _sub2api_sort_key(record: dict[str, Any]) -> tuple[int, int, str]:
        status = str(record.get("status") or "").strip().lower()
        status_rank = 3 if status == "active" else 2 if status == "inactive" else 1 if status == "error" else 0
        schedulable_rank = 1 if record.get("schedulable", True) else 0
        name = str(record.get("email") or record.get("name") or "").strip()
        return (status_rank, schedulable_rank, name)

    def _build_sub2api_email_index(self, records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in records:
            email = str(item.get("email") or "").strip().lower()
            if not email:
                continue
            grouped.setdefault(email, []).append(item)
        result: dict[str, dict[str, Any]] = {}
        for email, items in grouped.items():
            result[email] = sorted(items, key=self._sub2api_sort_key, reverse=True)[0]
        return result

    def refresh_sub2api_accounts(self) -> None:
        settings = self.current_settings()
        proxy = settings.get("http_proxy", "")
        server = normalize_server_url(settings["integrations"]["sub2api"]["api_url"])

        def worker():
            return fetch_sub2api_remote_snapshot(settings, proxy_url=proxy, log_fn=self.log)

        def done(result):
            self.set_running(False, "Sub2API 刷新完成")
            if result.get("error"):
                messagebox.showerror("错误", result["error"])
                self.sub2api_stats_var.set("Sub2API 加载失败")
                return
            self.persist_runtime_settings(settings)
            self.sub2api_usage_cache.clear()
            self.sub2api_usage_cache_time.clear()
            self.usage_sync_var.set("服务器快照 · 同步 " + time.strftime("%H:%M:%S") + " · 每60秒")
            self.sub2api_snapshot_server=server
            self.sub2api_groups = result.get("groups", [])
            self.sub2api_records = result.get("records", [])
            self.sub2api_index = self._build_sub2api_email_index(self.sub2api_records)
            self.populate_sub2api_tree()
            self.reload_tokens(save_first=False)
            self.log(f"Sub2API 列表已刷新，共 {len(self.sub2api_records)} 条；用量来自已有快照，需更新可选中账号点击更新用量")

        self.run_background("正在连接 Sub2API", worker, done)

    def selected_sub2api_pool_records(self) -> list[dict[str, Any]]:
        selected = set(self.sub2api_tree.selection())
        return [record for iid, record in self.sub2api_row_index.items() if iid in selected]

    def usage_for_record(self, record):
        account_id=record.get('id')
        return self.sub2api_usage_cache[account_id] if account_id in self.sub2api_usage_cache else snapshot_usage(record)

    def local_remote_record(self, record):
        try:
            # A Sub2API row may be recreated with a new numeric ID. For
            # display, follow it only when the old bound row is gone and the
            # stable OAuth workspace identity still matches. The maintenance
            # loop persists the new binding after its full safety checks.
            return match_remote(record, self.sub2api_records, allow_rebind=True)
        except ValueError:
            return None

    def sort_description(self):
        labels={'id':'ID','email':'账号名称','groups':'账号标签','status':'状态','scheduling':'调度','concurrency':'并发','quota7':'7d已用','priority':'优先级','error':'错误'}
        return labels[self.sub2api_sort_column]+('降序' if self.sub2api_sort_descending else '升序')

    def sorted_sub2api_records(self):
        return sort_account_rows(self.sub2api_records,self.sub2api_sort_column,self.sub2api_sort_descending,self.usage_for_record)

    def sort_sub2api_accounts(self,column):
        if column==self.sub2api_sort_column:
            self.sub2api_sort_descending=not self.sub2api_sort_descending
        else:
            self.sub2api_sort_column=column
            self.sub2api_sort_descending=False
        labels={'id':'ID','email':'账号名称','groups':'账号标签','status':'状态','scheduling':'调度','concurrency':'并发','quota7':'7d已用','priority':'优先级','error':'错误摘要'}
        for key,label in labels.items():
            arrow=(' ↓' if self.sub2api_sort_descending else ' ↑') if key==column else ''
            self.sub2api_tree.heading(key,text=label+arrow)
        self.populate_sub2api_tree()
        self.reload_tokens(save_first=False)

    def poll_remote_snapshot(self):
        self.root.after(60000,self.poll_remote_snapshot)
        if self.is_running() or self.auto_refresh_running or self._snapshot_inflight or not self.sub2api_records:
            return
        settings=self.current_settings()
        try:
            server=normalize_server_url(settings['integrations']['sub2api']['api_url'])
        except ValueError:
            return
        if server!=self.sub2api_snapshot_server:
            return
        self._snapshot_inflight=True
        def work():
            try:
                records=fetch_sub2api_accounts(settings,proxy_url=settings.get('http_proxy',''),filters={'platform':'openai'})
                error=None
            except Exception as exc:
                records=[];error=exc
            def done():
                self._snapshot_inflight=False
                if self.is_running() or self.auto_refresh_running:
                    return
                try:
                    if normalize_server_url(self.current_settings()['integrations']['sub2api']['api_url'])!=server:
                        return
                except ValueError:
                    return
                if error is not None:
                    self.usage_sync_var.set('服务器快照 · 自动同步失败，保留上次数据')
                    return
                now=time.time()
                for account_id in list(self.sub2api_usage_cache):
                    if now-self.sub2api_usage_cache_time.get(account_id,0)>=60:
                        self.sub2api_usage_cache.pop(account_id,None)
                        self.sub2api_usage_cache_time.pop(account_id,None)
                self.update_recovery_snapshot(records)
                self.reload_tokens(save_first=False)
            try:
                self.root.after(0,done)
            except (RuntimeError,tk.TclError):
                pass
        threading.Thread(target=work,daemon=True).start()

    def refresh_sub2api_usage(self):
        rows=self.selected_sub2api_pool_records() or self.filtered_sub2api_records
        rows=[r for r in rows if r.get('platform')=='openai' and r.get('type')=='oauth']
        if not rows:
            messagebox.showinfo('更新用量','请先加载并选择OAuth账号')
            return
        if len(rows)>50:
            messagebox.showinfo('更新用量','请选中账号或缩小分组筛选，每次最多50个')
            return
        settings=self.current_settings()
        server=normalize_server_url(settings['integrations']['sub2api']['api_url'])
        if self.sub2api_snapshot_server and self.sub2api_snapshot_server != server:
            messagebox.showinfo('服务器已变更','请先重新刷新账号列表')
            return
        ids=[r['id'] for r in rows]
        def worker():
            return fetch_sub2api_usage(settings,ids,proxy_url=settings.get('http_proxy',''))
        def done(result):
            self.set_running(False,'用量更新完成')
            self.usage_sync_var.set('用量读取 '+time.strftime('%H:%M:%S')+' · 快照每60秒同步')
            if result.get('error'):
                messagebox.showerror('读取用量失败',result['error'])
                return
            for account_id in ids:
                key=str(account_id)
                data=(result.get('usage') or {}).get(key)
                error=(result.get('errors') or {}).get(key)
                record=next((r for r in rows if r['id']==account_id),{})
                # The server creates a 0% window when it only has local request
                # statistics. Don't present that placeholder as known quota.
                if data:
                    for short,window_key in [('5h','five_hour'),('7d','seven_day')]:
                        window=data.get(window_key)
                        if isinstance(window,dict) and window.get('utilization')==0 and not window.get('resets_at') and f'codex_{short}_used_percent' not in (record.get('extra') or {}):
                            window['utilization']=None
                self.sub2api_usage_cache[account_id]={'error':error} if error else data or {}
                self.sub2api_usage_cache_time[account_id]=time.time()
            self.populate_sub2api_tree()
            self.reload_tokens(save_first=False)
            self.log(f"Sub2API用量读取 {len(ids)} 个，失败 {len(result.get('errors') or {})} 个；未强制探测")
        self.run_background('正在读取账号用量',worker,done)

    def refresh_selected_sub2api_remote(self) -> None:
        records = self.selected_sub2api_pool_records()
        if not records:
            messagebox.showerror("错误", "请先选择远端 Sub2API 账号")
            return
        self._refresh_sub2api_remote_records(records, label="选中")

    def edit_selected_sub2api_remote(self) -> None:
        records = self.selected_sub2api_pool_records()
        if not records:
            messagebox.showinfo('编辑账号', '请先选择远端账号')
            return
        if len(records) > 1:
            self.edit_multiple_sub2api_remote(records)
            return
        record = records[0]
        dialog = tk.Toplevel(self.root)
        dialog.title(f"编辑远端账号 #{record.get('id', '')}")
        dialog.geometry('560x470')
        dialog.transient(self.root)
        center_window(dialog, self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(1, weight=1)

        values = {
            'concurrency': tk.StringVar(value=str(record.get('concurrency') or 0)),
            'priority': tk.StringVar(value=str(record.get('priority') or 0)),
            'rate_multiplier': tk.StringVar(value=str(record.get('rate_multiplier', 1))),
            'fingerprint': tk.StringVar(value='关闭'),
            'ws_mode': tk.StringVar(value='上下文池（默认）'),
            'auto_pause': tk.BooleanVar(value=bool(record.get('auto_pause_on_expired', True))),
            'proxy': tk.StringVar(value='直连'),
        }
        extra = record.get('extra') or {}
        values['fingerprint'].set(next((label for label, mode in FINGERPRINTS.items()
                                        if mode == extra.get('codex_fingerprint_mode', 'off')), '关闭'))
        values['ws_mode'].set(next((label for label, mode in WS_MODES.items()
                                   if mode == extra.get('openai_oauth_responses_websockets_v2_mode', 'ctx_pool')),
                                  '上下文池（默认）'))
        proxy_choices = {'直连': 0}
        for item in self.sub2api_records:
            proxy_id = item.get('proxy_id')
            if proxy_id:
                proxy = item.get('proxy') or {}
                proxy_choices[f"#{proxy_id} {proxy.get('name') or '代理'}"] = int(proxy_id)
        current_proxy = int(record.get('proxy_id') or 0)
        if current_proxy:
            values['proxy'].set(next((label for label, value in proxy_choices.items() if value == current_proxy), f'#{current_proxy} 代理'))
            proxy_choices.setdefault(values['proxy'].get(), current_proxy)

        def field(row, label, key, *, options=None):
            ttk.Label(frame, text=label, style='Card.TLabel').grid(row=row, column=0, sticky='w', pady=6, padx=(0, 12))
            if options is None:
                widget = ttk.Entry(frame, textvariable=values[key])
            else:
                widget = ttk.Combobox(frame, textvariable=values[key], values=options, state='readonly')
            widget.grid(row=row, column=1, sticky='ew', pady=6)

        field(0, '并发数', 'concurrency')
        field(1, '调度优先级', 'priority')
        field(2, '账号倍率', 'rate_multiplier')
        field(3, '代理池', 'proxy', options=list(proxy_choices))
        field(4, '设备指纹收敛', 'fingerprint', options=list(FINGERPRINTS))
        field(5, 'WS mode', 'ws_mode', options=list(WS_MODES))
        ttk.Checkbutton(frame, text='账号到期时自动暂停', variable=values['auto_pause']).grid(row=6, column=0, columnspan=2, sticky='w', pady=6)
        ttk.Label(frame, text='只修改运营参数；OAuth凭据、邮箱和工作区身份不会被编辑覆盖。', style='CardSubtle.TLabel', wraplength=500).grid(row=7, column=0, columnspan=2, sticky='w', pady=(8, 12))

        def save():
            try:
                concurrency = int(values['concurrency'].get().strip())
                priority = int(values['priority'].get().strip())
                rate = float(values['rate_multiplier'].get().strip())
                if concurrency < 1 or concurrency > 10000 or priority < 0 or priority > 100000 or rate < 0:
                    raise ValueError
            except (TypeError, ValueError):
                messagebox.showerror('配置无效', '并发、优先级或倍率格式不正确', parent=dialog)
                return
            updates = {
                'concurrency': concurrency,
                'priority': priority,
                'rate_multiplier': rate,
                'proxy_id': proxy_choices.get(values['proxy'].get(), 0),
                'auto_pause_on_expired': bool(values['auto_pause'].get()),
                'extra': {
                    'codex_fingerprint_mode': FINGERPRINTS[values['fingerprint'].get()],
                    'openai_oauth_responses_websockets_v2_mode': WS_MODES[values['ws_mode'].get()],
                    'openai_oauth_responses_websockets_v2_enabled': WS_MODES[values['ws_mode'].get()] != 'off',
                },
            }
            settings = self.current_settings()
            proxy = settings.get('http_proxy', '')
            def worker():
                return update_sub2api_account_settings(settings, record, updates, proxy_url=proxy)
            def done(result):
                self.set_running(False, '远端账号编辑完成')
                if result.get('error'):
                    messagebox.showerror('编辑失败', result['error'], parent=dialog)
                    return
                dialog.destroy()
                self.refresh_sub2api_accounts()
            self.run_background('正在保存远端账号配置', worker, done)

        buttons = ttk.Frame(frame)
        buttons.grid(row=8, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        ttk.Button(buttons, text='取消', command=dialog.destroy).pack(side='right')
        ttk.Button(buttons, text='保存', command=save, style='Primary.TButton').pack(side='right', padx=8)

    def edit_multiple_sub2api_remote(self, records: list[dict[str, Any]]) -> None:
        """Open a partial update form for several remote accounts.

        Blank fields and the explicit “不修改” choices are omitted from the
        bulk payload, so each account keeps its existing value for those
        fields.
        """
        dialog = tk.Toplevel(self.root)
        dialog.title(f"批量编辑远端账号（{len(records)} 个）")
        dialog.geometry("560x470")
        dialog.transient(self.root)
        center_window(dialog, self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        values = {
            "concurrency": tk.StringVar(value="不修改"),
            "priority": tk.StringVar(value="不修改"),
            "rate_multiplier": tk.StringVar(value="不修改"),
            "proxy": tk.StringVar(value="不修改"),
            "fingerprint": tk.StringVar(value="不修改"),
            "ws_mode": tk.StringVar(value="不修改"),
            "auto_pause": tk.StringVar(value="不修改"),
        }
        proxy_choices = {"不修改": None, "直连": 0}
        for item in self.sub2api_records:
            proxy_id = item.get("proxy_id")
            if proxy_id:
                proxy = item.get("proxy") or {}
                proxy_choices[f"#{proxy_id} {proxy.get('name') or '代理'}"] = int(proxy_id)

        def field(row, label, key, *, options=None):
            ttk.Label(frame, text=label, style="Card.TLabel").grid(
                row=row, column=0, sticky="w", pady=6, padx=(0, 12)
            )
            if options is None:
                widget = ttk.Entry(frame, textvariable=values[key])
                if key in {"concurrency", "priority", "rate_multiplier"}:
                    def clear_default(_event, variable=values[key]):
                        if variable.get() == "不修改":
                            variable.set("")

                    def restore_default(_event, variable=values[key]):
                        if not variable.get().strip():
                            variable.set("不修改")

                    widget.bind("<FocusIn>", clear_default)
                    widget.bind("<FocusOut>", restore_default)
            else:
                widget = ttk.Combobox(
                    frame, textvariable=values[key], values=options, state="readonly"
                )
            widget.grid(row=row, column=1, sticky="ew", pady=6)

        field(0, "并发数", "concurrency")
        field(1, "调度优先级", "priority")
        field(2, "账号倍率", "rate_multiplier")
        field(3, "代理池", "proxy", options=list(proxy_choices))
        field(4, "设备指纹收敛", "fingerprint", options=["不修改", *FINGERPRINTS])
        field(5, "WS mode", "ws_mode", options=["不修改", *WS_MODES])
        field(6, "到期自动暂停", "auto_pause", options=["不修改", "启用", "停用"])
        ttk.Label(
            frame,
            text="空白或“不修改”表示保留每个账号原值；不会覆盖 OAuth 凭据、邮箱、分组和工作区身份。",
            style="CardSubtle.TLabel",
            wraplength=500,
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 12))

        def save():
            updates: dict[str, Any] = {}
            try:
                raw_concurrency = values["concurrency"].get().strip()
                if raw_concurrency and raw_concurrency != "不修改":
                    concurrency = int(raw_concurrency)
                    if concurrency < 1 or concurrency > 10000:
                        raise ValueError
                    updates["concurrency"] = concurrency
                raw_priority = values["priority"].get().strip()
                if raw_priority and raw_priority != "不修改":
                    priority = int(raw_priority)
                    if priority < 0 or priority > 100000:
                        raise ValueError
                    updates["priority"] = priority
                raw_rate = values["rate_multiplier"].get().strip()
                if raw_rate and raw_rate != "不修改":
                    rate = float(raw_rate)
                    if rate < 0:
                        raise ValueError
                    updates["rate_multiplier"] = rate
            except (TypeError, ValueError):
                messagebox.showerror("配置无效", "并发、优先级或倍率格式不正确", parent=dialog)
                return

            proxy_value = proxy_choices.get(values["proxy"].get())
            if proxy_value is not None:
                updates["proxy_id"] = proxy_value
            if values["auto_pause"].get() != "不修改":
                updates["auto_pause_on_expired"] = values["auto_pause"].get() == "启用"
            extra: dict[str, Any] = {}
            if values["fingerprint"].get() != "不修改":
                extra["codex_fingerprint_mode"] = FINGERPRINTS[values["fingerprint"].get()]
            if values["ws_mode"].get() != "不修改":
                mode = WS_MODES[values["ws_mode"].get()]
                extra.update(
                    openai_oauth_responses_websockets_v2_mode=mode,
                    openai_oauth_responses_websockets_v2_enabled=mode != "off",
                )
            if extra:
                updates["extra"] = extra
            if not updates:
                messagebox.showinfo("批量编辑", "没有填写要修改的字段", parent=dialog)
                return

            settings = self.current_settings()
            proxy = settings.get("http_proxy", "")
            account_ids = [int(record.get("id") or 0) for record in records]

            def worker():
                return bulk_update_sub2api_accounts(
                    settings, account_ids, updates, proxy_url=proxy
                )

            def done(result):
                self.set_running(False, "批量编辑完成")
                if result.get("error"):
                    messagebox.showerror("批量编辑失败", result["error"], parent=dialog)
                    return
                dialog.destroy()
                self.log(f"已统一更新 {len(account_ids)} 个 Sub2API 账号")
                self.refresh_sub2api_accounts()

            self.run_background("正在批量编辑 Sub2API 账号", worker, done)

        buttons = ttk.Frame(frame)
        buttons.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="统一保存", command=save, style="Primary.TButton").pack(
            side="right", padx=8
        )

    def refresh_filtered_sub2api_remote(self) -> None:
        records = list(self.filtered_sub2api_records)
        if not records:
            messagebox.showinfo("提示", "当前筛选下没有账号")
            return
        self._refresh_sub2api_remote_records(records, label="当前筛选")

    def _refresh_sub2api_remote_records(self, records: list[dict[str, Any]], *, label: str) -> None:
        settings = self.current_settings()
        proxy = settings.get("http_proxy", "")

        def worker():
            return refresh_sub2api_remote_records(records, settings, proxy_url=proxy, log_fn=self.log)

        def done(result):
            self.set_running(False, "Sub2API 刷新令牌完成")
            if result.get("error"):
                messagebox.showerror("错误", result["error"])
                return
            self.persist_runtime_settings(settings)
            messagebox.showinfo(
                "完成",
                f"{label}刷新令牌完成\n成功: {result.get('success_count', 0)}\n失败: {result.get('fail_count', 0)}",
            )
            self.refresh_sub2api_accounts()

        self.run_background("正在刷新 Sub2API 远端账号", worker, done)

    def set_selected_sub2api_schedulable(self, enabled: bool) -> None:
        records = self.selected_sub2api_pool_records()
        if not records:
            messagebox.showerror("错误", "请先选择远端 Sub2API 账号")
            return
        settings = self.current_settings()
        proxy = settings.get("http_proxy", "")

        def worker():
            return set_sub2api_remote_records_schedulable(
                records,
                settings,
                enabled=enabled,
                proxy_url=proxy,
                log_fn=self.log,
            )

        def done(result):
            self.set_running(False, "Sub2API 调度更新完成")
            if result.get("error"):
                messagebox.showerror("错误", result["error"])
                return
            self.persist_runtime_settings(settings)
            messagebox.showinfo(
                "完成",
                f"调度更新完成\n成功: {result.get('success_count', 0)}\n失败: {result.get('fail_count', 0)}",
            )
            self.refresh_sub2api_accounts()

        self.run_background("正在更新 Sub2API 远端状态", worker, done)

    def delete_selected_sub2api_records(self) -> None:
        records = self.selected_sub2api_pool_records()
        if not records:
            messagebox.showerror("错误", "请先选择远端 Sub2API 账号")
            return
        self._delete_sub2api_records(records, title=f"确定删除选中的 {len(records)} 个远端 Sub2API 账号吗？")



    def _delete_sub2api_records(self, records: list[dict[str, Any]], *, title: str) -> None:
        if not messagebox.askyesno("确认", title):
            return
        settings = self.current_settings()
        proxy = settings.get("http_proxy", "")

        def worker():
            return delete_sub2api_remote_records(records, settings, proxy_url=proxy, log_fn=self.log)

        def done(result):
            self.set_running(False, "Sub2API 远端删除完成")
            if result.get("error"):
                messagebox.showerror("错误", result["error"])
                return
            self.persist_runtime_settings(settings)
            messagebox.showinfo(
                "完成",
                f"删除完成\n成功: {result.get('success_count', 0)}\n失败: {result.get('fail_count', 0)}",
            )
            self.refresh_sub2api_accounts()

        self.run_background("正在删除 Sub2API 远端账号", worker, done)
