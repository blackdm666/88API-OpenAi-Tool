from __future__ import annotations

from typing import Any
import threading
import time

import tkinter as tk
from tkinter import messagebox, ttk
from .gui_widgets import CheckList
from .integrations import fetch_sub2api_usage, fetch_sub2api_accounts
from .usage_display import snapshot_usage, quota_cell, sort_account_rows
from .sub2api_policy import normalize_server_url, match_remote

from .services import (
    delete_sub2api_remote_records,
    fetch_sub2api_remote_snapshot,
    is_sub2api_invalidated,
    refresh_sub2api_remote_records,
    set_sub2api_remote_records_status,
)


class GUISub2APIMixin:
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
        self.sub2api_group_filters.clear()
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
            if type_filter and type_filter != "全部类型".lower() and type_filter != record_type:
                continue
            if status_filter == "invalidated" and not is_sub2api_invalidated(record):
                continue
            if status_filter == "unschedulable" and record.get("schedulable", True):
                continue
            if status_filter not in {"", "全部状态".lower(), "invalidated", "unschedulable"} and status_filter != status:
                continue
            filtered.append(record)
        return filtered

    def _update_sub2api_group_filter_values(self):
        names = sorted(self.sub2api_group_filters)
        self.sub2api_group_filter_var.set('、'.join(names) if names else '全部分组（可多选）')

    def choose_sub2api_group_filters(self):
        dialog = tk.Toplevel(self.root)
        dialog.title('筛选分组 · 多选')
        dialog.geometry('470x380')
        dialog.transient(self.root)
        dialog.grab_set()
        names = sorted({name for r in self.sub2api_records for name in r.get('group_names', [])})
        choices = CheckList(dialog, [(name, name) for name in names], self.sub2api_group_filters)
        choices.pack(fill='both', expand=True, padx=16, pady=16)
        buttons = ttk.Frame(dialog, padding=12)
        buttons.pack(fill='x')
        def apply():
            self.sub2api_group_filters = set(choices.selected())
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

        active_count = 0
        inactive_count = 0
        error_count = 0
        unschedulable_count = 0

        for idx, record in enumerate(self.filtered_sub2api_records, start=1):
            status = str(record.get("status") or "").strip().lower()
            if status == "active":
                active_count += 1
            elif status == "inactive":
                inactive_count += 1
            elif status == "error":
                error_count += 1
            if not record.get("schedulable", True):
                unschedulable_count += 1

            iid = self._build_sub2api_row_id(record, idx)
            self.sub2api_row_index[iid] = record
            tags: tuple[str, ...] = ()
            if is_sub2api_invalidated(record):
                tags = ("invalidated",)
            elif status == "error":
                tags = ("error",)
            elif not record.get("schedulable", True) or status == "inactive":
                tags = ("warning",)
            self.sub2api_tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(
                    record.get("id", ""),
                    record.get("name") or record.get("email", ""),
                    self._sub2api_groups_text(record),
                    record.get("status", ""),
                    quota_cell(self.usage_for_record(record),"five_hour"),
                    quota_cell(self.usage_for_record(record),"seven_day"),
                    self._sub2api_error_summary(record),
                ),
                tags=tags,
            )

            if iid in selected:
                self.sub2api_tree.selection_add(iid)

        self.sub2api_stats_var.set(f'显示 {len(self.filtered_sub2api_records)} / {len(self.sub2api_records)} · 异常 {error_count} · {self.sort_description()}')
        self.sub2api_pool_stats_var.set(
            f"当前 {len(self.filtered_sub2api_records)}  Active {active_count}  Inactive {inactive_count}  Error {error_count}  停调度 {unschedulable_count}"
        )
        self.sub2api_invalidated_stats_var.set(f"失效记录 {len(self.invalidated_sub2api_records)}")
        self.sub2api_tree.xview_moveto(xview)
        self.sub2api_tree.yview_moveto(yview)

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
            return match_remote(record, self.sub2api_records)
        except ValueError:
            return None

    def sort_description(self):
        labels={'id':'ID','email':'账号名称','groups':'分组','status':'状态','quota5':'5h已用','quota7':'7d已用','error':'错误'}
        return labels[self.sub2api_sort_column]+('降序' if self.sub2api_sort_descending else '升序')

    def sorted_sub2api_records(self):
        return sort_account_rows(self.sub2api_records,self.sub2api_sort_column,self.sub2api_sort_descending,self.usage_for_record)

    def sort_sub2api_accounts(self,column):
        if column==self.sub2api_sort_column:
            self.sub2api_sort_descending=not self.sub2api_sort_descending
        else:
            self.sub2api_sort_column=column
            self.sub2api_sort_descending=False
        labels={'id':'ID','email':'账号名称','groups':'分组','status':'状态','quota5':'5h已用','quota7':'7d已用','error':'错误摘要'}
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
            self.set_running(False, "Sub2API 远端刷新完成")
            if result.get("error"):
                messagebox.showerror("错误", result["error"])
                return
            self.persist_runtime_settings(settings)
            messagebox.showinfo(
                "完成",
                f"{label}远端刷新完成\n成功: {result.get('success_count', 0)}\n失败: {result.get('fail_count', 0)}",
            )
            self.refresh_sub2api_accounts()

        self.run_background("正在刷新 Sub2API 远端账号", worker, done)

    def set_selected_sub2api_status(self, status: str) -> None:
        records = self.selected_sub2api_pool_records()
        if not records:
            messagebox.showerror("错误", "请先选择远端 Sub2API 账号")
            return
        settings = self.current_settings()
        proxy = settings.get("http_proxy", "")

        def worker():
            return set_sub2api_remote_records_status(
                records,
                settings,
                status=status,
                proxy_url=proxy,
                log_fn=self.log,
            )

        def done(result):
            self.set_running(False, "Sub2API 状态更新完成")
            if result.get("error"):
                messagebox.showerror("错误", result["error"])
                return
            self.persist_runtime_settings(settings)
            messagebox.showinfo(
                "完成",
                f"状态更新完成\n成功: {result.get('success_count', 0)}\n失败: {result.get('fail_count', 0)}",
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
