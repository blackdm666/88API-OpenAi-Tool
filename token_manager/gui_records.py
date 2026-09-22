from __future__ import annotations

import json
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox

from .auth_proxy import authorization_proxy
from .auth_batch import remove_account_lines
from .credential_vault import CredentialVault
from .integrations import fetch_sub2api_accounts, sub2api_upload_payload
from .usage_display import quota_cell
from .recovery_support import remote_health
from .utils import openai_plan_label, sub2api_plan_type
from .sub2api_policy import auth_failure_kind, match_remote
from .converters import from_local_payload, from_sub2api_payload
from .services import export_organized_payloads, refresh_record, run_batch, sync_subscription, upload_record
from .utils import atomic_write_json, safe_email_filename


class GUIRecordsMixin:
    @staticmethod
    def _is_pending_auth2fa(record: dict[str, object]) -> bool:
        metadata = record.get('metadata') or {}
        return (
            isinstance(metadata, dict)
            and bool(metadata.get('auth2fa_pending'))
            and not any(record.get(key) for key in ('access_token', 'refresh_token', 'id_token'))
        )

    def show_token_context_menu(self, event):
        row = self.token_tree.identify_row(event.y)
        if row and row not in self.token_tree.selection():
            self.token_tree.selection_set(row)
        if not row:
            return
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="刷新本地令牌", command=self.refresh_selected)
        menu.add_command(label="上传到 Sub2API", command=self.upload_selected)
        menu.add_separator()
        menu.add_command(label="监控选中", command=lambda: self.set_recovery_selected(True))
        menu.add_command(label="取消监控", command=lambda: self.set_recovery_selected(False))
        menu.add_separator()
        menu.add_command(
            label="移除账号（保留远端）",
            command=self.remove_selected_keep_remote,
        )
        menu.add_command(label="删除账号", command=self.delete_selected)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def reload_tokens(self, save_first: bool = True) -> None:
        if save_first:
            self.save_settings(reload_tokens=False, notify=False)
        selected_ids = set(self.token_tree.selection())
        xview, yview = self.token_tree.xview()[0], self.token_tree.yview()[0]
        for item in self.token_tree.get_children():
            self.token_tree.delete(item)
        all_records = self.store.load_all()
        self.local_record_index = {str(r.get('email') or '').strip().lower(): r for r in all_records if str(r.get('email') or '').strip()}
        rank={r["id"]:i for i,r in enumerate(self.sorted_sub2api_records())}
        def remote_order(record):
            remote=self.local_remote_record(record)
            return (rank.get(remote["id"],len(rank)) if remote else len(rank),str(record.get("email","")).casefold())
        all_records.sort(key=remote_order)
        self.records = self.filter_records(all_records)
        self.update_stats(all_records, self.records)
        for record in self.records:
            remote=self.local_remote_record(record)
            usage=self.usage_for_record(remote) if remote else {}
            upload_summary = self.upload_summary(record)
            status = self.account_status(record, remote)
            if record.get("uploads"):
                for target_state in record["uploads"].values():
                    if isinstance(target_state, dict) and not target_state.get("ok", True):
                        status = "上传异常" if status == "有效" else status
                        break
            iid = str(record.get("_filename") or record.get("email"))
            tags = ()
            if record["_is_expired"]:
                tags = ("expired",)
            elif record["_remaining_seconds"] < 600:
                tags = ("warning",)
            self.token_tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(
                    record.get("email", "Unknown"),
                    self.plan_label(record),
                    quota_cell(usage,"seven_day"),
                    status,
                    record["_remaining_text"],
                    upload_summary,
                    self.recovery_summary(record),
                ),
                tags=tags,
            )
            if iid in selected_ids:
                self.token_tree.selection_add(iid)
        self.token_tree.xview_moveto(xview)
        self.token_tree.yview_moveto(yview)
        self.on_selection_changed()

    def account_status(self, record, remote=None):
        if self._is_pending_auth2fa(record):
            return '失效'
        if not record.get('access_token') or not record.get('refresh_token'):
            return '失效'
        if remote:
            if auth_failure_kind(remote):
                return '失效'
            credentials_status = remote.get('credentials_status') or {}
            if credentials_status.get('has_access_token') is False:
                return '失效'
            if remote.get('status') == 'inactive':
                return '失效'
            health = remote_health(remote)
            if health in {'已停用', '授权异常', '远端异常', '已启用·已到期'}:
                return '失效'
            return '正常'
        return '失效' if record['_is_expired'] else '正常'

    def filter_records(self, records: list[dict[str, object]]) -> list[dict[str, object]]:
        search = self.search_var.get().strip().lower()
        plan_filter = self.plan_filter_var.get().strip().lower()
        status_filter = self.status_filter_var.get().strip()
        filtered: list[dict[str, object]] = []
        for record in records:
            email = str(record.get("email") or "").lower()
            plan_label = self.plan_label(record).lower()
            has_upload_error = any(
                isinstance(state, dict) and not state.get("ok", True)
                for state in (record.get("uploads") or {}).values()
            )
            if search and search not in email and search not in str(record.get("account_id") or "").lower():
                continue
            if plan_filter and plan_filter != "全部标签".lower() and plan_filter != plan_label:
                continue
            if status_filter in ("正常", "失效") and self.account_status(
                record, self.local_remote_record(record)
            ) != status_filter:
                continue
            if status_filter == "上传异常" and not has_upload_error:
                continue
            filtered.append(record)
        return filtered

    def update_stats(self, all_records: list[dict[str, object]], visible_records: list[dict[str, object]]) -> None:
        labels = ['Plus', 'Pro 20x', 'Pro 5x', 'Business Standard', 'Business Premium', 'Free', 'Enterprise', 'Unknown']
        totals = {label: 0 for label in labels}
        for record in all_records:
            totals[self.plan_label(record)] = totals.get(self.plan_label(record), 0) + 1
        counts=[f"全部 {len(all_records)}",f"当前 {len(visible_records)}"]
        counts.extend(f'{label} {totals[label]}' for label in labels if totals[label])
        self.stats_var.set(' · '.join(counts))

    def organize_output_dirs(self) -> None:
        self.save_settings(reload_tokens=False, notify=False)

        def worker():
            records = self.store.load_all()
            return export_organized_payloads(self.store, records, self.config, log_fn=self.log)

        def done(result):
            self.set_running(False, "目录整理完成")
            if result.get("error"):
                messagebox.showerror("错误", result["error"])
                return
            self.log(
                "已整理导出 "
                f"Sub2API 聚合 {result.get('sub2api_count', 0)} 个账号"
            )
            if result.get("removed_sub2api_files", 0):
                self.log(f"已清理旧的 Sub2API 分散文件 {result.get('removed_sub2api_files', 0)} 个")
            self.reload_tokens(save_first=False)
            messagebox.showinfo(
                "完成",
                f"Sub2API 已生成聚合文件\n{result.get('sub2api_path', '')}\n"
                f"清理旧的分散文件 {result.get('removed_sub2api_files', 0)} 个",
            )

        self.run_background("正在整理 Sub2API 输出目录", worker, done)

    def cleanup_tokens_dir(self) -> None:
        self.save_settings(reload_tokens=False, notify=False)

        def worker():
            return self.store.cleanup_tokens_directory()

        def done(result):
            self.set_running(False, "Tokens 清理完成")
            if result.get("error"):
                messagebox.showerror("错误", result["error"])
                return
            summary = (
                f"保留 {result.get('kept', 0)} 个账号文件\n"
                f"删除重复文件 {result.get('removed_files', 0)}\n"
                f"移动最佳文件 {result.get('moved_best_files', 0)}\n"
                f"移除旧目录 {result.get('removed_dirs', 0)}"
            )
            self.log(summary)
            self.reload_tokens(save_first=False)
            messagebox.showinfo("完成", summary)

        self.run_background("正在清理 Tokens 目录", worker, done)

    def upload_summary(self, record: dict[str, object]) -> str:
        remote = self.local_remote_record(record)
        if remote:
            remote_id = remote.get("id")
            if remote_id not in (None, ""):
                return f"已上传 #{remote_id}"
            return "已上传"
        if self.sub2api_snapshot_server:
            return "未上传"
        uploads = record.get("uploads") or {}
        state = uploads.get("sub2api") or {}
        if state.get("ok"):
            return "已上传"
        return "未上传"

    def recovery_summary(self, record: dict[str, object]) -> str:
        """Keep the list column about enrollment, not the detailed recovery state."""
        return "监控中" if bool((record.get("sub2api_recovery") or {}).get("enabled")) else "未监控"

    def plan_label(self, record: dict[str, object]) -> str:
        subscription = record.get('subscription') or {}
        # The matched Sub2API DTO is authoritative for the account pool and
        # follows the same plan_type definition as the remote admin UI.
        try:
            remote = self.local_remote_record(record)
        except Exception:
            remote = None
        remote_label = openai_plan_label(sub2api_plan_type(remote))
        if remote_label != "Unknown":
            return remote_label
        local_raw = subscription.get('plan_type') or record.get('_plan')
        return openai_plan_label(local_raw)

    def selected_records(self) -> list[dict[str, object]]:
        selected = set(self.token_tree.selection())
        return [record for record in self.records if str(record.get("_filename") or record.get("email")) in selected]

    def primary_record(self) -> dict[str, object] | None:
        records = self.selected_records()
        return records[0] if records else None

    def on_selection_changed(self, _event=None) -> None:
        record = self.primary_record()
        if not record:
            self.detail_text.config(state=tk.NORMAL)
            self.detail_text.delete("1.0", tk.END)
            self.detail_text.insert("1.0", "未选择账号")
            self.detail_text.config(state=tk.DISABLED)
            return

        uploads = record.get("uploads") or {}
        upload_lines = []
        for key in ("sub2api",):
            state = uploads.get(key) or {}
            if state:
                upload_lines.append(
                    f"{key}: {'成功' if state.get('ok') else '失败'} {state.get('updated_at', '')} {state.get('message', '')}".strip()
                )
        subscription = record.get("subscription") or {}
        sub2api_remote = self.sub2api_index.get(str(record.get("email") or "").strip().lower(), {})
        sub2api_remote_text = "Sub2API 未加载或未找到对应账号"
        if sub2api_remote:
            sub2api_remote_text = (
                f"状态 {sub2api_remote.get('status', '')}  "
                f"分组 {', '.join(sub2api_remote.get('group_names') or []) or '无'}  "
                f"到期 {sub2api_remote.get('expires_at_text', '') or '无'}"
            )
        detail = f"""邮箱: {record.get('email', 'Unknown')}
账号 ID: {record.get('account_id', 'Unknown')}
标签: {self.plan_label(record)}
标签来源: {subscription.get('source', '')}
订阅到期: {subscription.get('subscription_active_until', '') or '未知'}
状态: {self.account_status(record, sub2api_remote or None)}
本地期限: {'已过期' if record['_is_expired'] else '未过期'}
剩余时间: {record['_remaining_text']}
最后刷新: {record.get('last_refresh', '')}
创建时间: {record.get('created_at', '')}
文件: {record.get('_filename', '')}

凭据: Access Token {'已保存' if record.get('access_token') else '缺失'} / Refresh Token {'已保存' if record.get('refresh_token') else '缺失'}

自动恢复: {(record.get('sub2api_recovery') or {}).get('status', '未监控')}
恢复说明: {(record.get('sub2api_recovery') or {}).get('message', '在左侧选择账号后点击“监控选中”')}
远端核验: {(record.get('sub2api_recovery') or {}).get('remote_health', '未核验')}
模型测试: {(record.get('sub2api_recovery') or {}).get('probe_model', '-')} / {(record.get('sub2api_recovery') or {}).get('probe_message', '尚未测试')}

上传状态:
{chr(10).join(upload_lines) if upload_lines else '暂无'}

Sub2API 远端:
{sub2api_remote_text}
"""
        self.detail_text.config(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert("1.0", detail)
        self.detail_text.config(state=tk.DISABLED)

    def refresh_selected(self) -> None:
        records = self.selected_records()
        if not records:
            messagebox.showerror("错误", "请先选择账号")
            return
        pending = [record for record in records if self._is_pending_auth2fa(record)]
        if pending:
            messagebox.showinfo(
                "尚未授权",
                f"选中的 {len(pending)} 个账号只有2FA资料，不能刷新OAuth令牌；请点击“智能补授权”。",
            )
            return

        settings = self.current_settings()
        workers = min(len(records), int(settings.get("refresh_workers") or 1))

        def worker():
            return run_batch(
                records,
                workers=workers,
                job=lambda record: refresh_record(
                    self.store,
                    record,
                    settings,
                    proxy_url=authorization_proxy(settings),
                    log_fn=self.log,
                ),
                progress_cb=self.with_progress("刷新"),
            )

        def done(result):
            self.set_running(False, "刷新完成")
            self.reload_tokens()
            if result.get("error"):
                messagebox.showerror("错误", result["error"])
                return
            messagebox.showinfo("完成", f"刷新完成\n成功: {result['success_count']}\n失败: {result['fail_count']}")

        self.run_background("正在刷新账号", worker, done)

    def refresh_all(self) -> None:
        if not self.records:
            messagebox.showinfo("提示", "没有账号")
            return
        self.token_tree.selection_set([str(record.get("_filename") or record.get("email")) for record in self.records])
        self.refresh_selected()

    def sync_selected_labels(self) -> None:
        records = self.selected_records()
        if not records:
            messagebox.showerror("错误", "请先选择账号")
            return
        settings = self.current_settings()
        proxy = settings.get("http_proxy", "")
        workers = min(len(records), int(settings.get("refresh_workers") or 1), 4)

        def worker():
            return run_batch(
                records,
                workers=workers,
                job=lambda record: sync_subscription(self.store, record, proxy_url=proxy, log_fn=self.log),
                progress_cb=self.with_progress("同步标签"),
            )

        def done(result):
            self.set_running(False, "标签同步完成")
            self.reload_tokens()
            if result.get("error"):
                messagebox.showerror("错误", result["error"])
                return
            messagebox.showinfo("完成", f"同步完成\n成功: {result['success_count']}\n失败: {result['fail_count']}")

        self.run_background("正在同步标签", worker, done)

    def upload_selected(self) -> None:
        records = self.selected_records()
        if not records:
            messagebox.showerror("错误", "请先选择账号")
            return
        pending = [record for record in records if self._is_pending_auth2fa(record)]
        if pending:
            messagebox.showinfo(
                "尚未授权",
                f"选中的 {len(pending)} 个账号只有2FA资料，不能上传空凭据；请先完成“智能补授权”。",
            )
            return
        self.save_settings(reload_tokens=False, notify=False)
        target = self.upload_target_var.get().strip().lower()
        settings = dict(self.config)
        proxy = settings.get("http_proxy", "")
        workers = min(len(records), int(settings.get("upload_workers") or 1))

        def worker():
            return run_batch(
                records,
                workers=workers,
                job=lambda record: upload_record(self.store, record, settings, target=target, proxy_url=proxy, log_fn=self.log),
                progress_cb=self.with_progress(f"上传 {target}"),
            )

        def done(result):
            self.set_running(False, "上传完成")
            # Upload state is persisted locally, but the left-side status can
            # also be derived from the cached Sub2API snapshot.  Re-read the
            # local files first, then refresh the remote snapshot after any
            # successful Sub2API upload so old 401/error state is not shown.
            self.reload_tokens(save_first=False)
            if result.get("error"):
                messagebox.showerror("错误", result["error"])
                return
            success_count = int(result.get("success_count") or 0)
            if target == "sub2api" and success_count > 0:
                self.log("Sub2API 上传成功，正在刷新远端账号状态")
                self.refresh_sub2api_accounts()
            messagebox.showinfo("完成", f"上传 {target} 完成\n成功: {result['success_count']}\n失败: {result['fail_count']}")

        self.run_background(f"正在上传到 {target}", worker, done)

    def upload_all(self) -> None:
        if not self.records:
            messagebox.showinfo("提示", "没有账号")
            return
        self.token_tree.selection_set([str(record.get("_filename") or record.get("email")) for record in self.records])
        self.upload_selected()

    def delete_selected(self) -> None:
        if self.auto_refresh_running or self.is_running():
            messagebox.showinfo('请先停止维护', '请先停止自动维护并等待当前任务结束')
            return
        records = self.selected_records()
        if not records:
            messagebox.showerror("错误", "请先选择账号")
            return
        if not messagebox.askyesno(
            "确认删除",
            f"确定删除选中的 {len(records)} 个账号吗？\n\n"
            "将同步清理：本地凭据、已保存的2FA资料，以及能唯一匹配到的 Sub2API 远端账号。",
        ):
            return
        settings = self.current_settings()
        records = [dict(record) for record in records]
        sub2api_cfg = (settings.get("integrations") or {}).get("sub2api") or {}
        has_remote_config = bool(
            str(sub2api_cfg.get("api_url") or "").strip()
            and str(sub2api_cfg.get("api_key") or "").strip()
        )

        def worker():
            lookup_ok = True
            lookup_error = ""
            remotes = []
            if has_remote_config:
                try:
                    remotes = fetch_sub2api_accounts(
                        settings,
                        proxy_url=settings.get("http_proxy", ""),
                        filters={"platform": "openai"},
                    )
                except Exception as exc:
                    lookup_ok = False
                    lookup_error = str(exc)
            else:
                # Without a current API Key, do not use a stale cached list to
                # issue destructive remote DELETE requests.
                remotes = []

            resolved: list[tuple[dict[str, object], dict[str, object]]] = []
            unresolved: list[tuple[dict[str, object], str]] = []
            if lookup_ok:
                for record in records:
                    try:
                        remote = match_remote(
                            {**record, "sub2api_recovery": {}},
                            remotes,
                            allow_rebind=True,
                        )
                    except ValueError as exc:
                        remote = None
                        unresolved.append((record, str(exc)))
                    if remote:
                        resolved.append((record, remote))
                    elif not any(item[0] is record for item in unresolved):
                        unresolved.append((record, "未找到唯一匹配的 Sub2API 远端账号"))

            remote_result = {
                "success_count": 0,
                "fail_count": 0,
                "results": [],
            }
            if lookup_ok and resolved:
                from .services import delete_sub2api_remote_records

                remote_result = delete_sub2api_remote_records(
                    [remote for _, remote in resolved],
                    settings,
                    proxy_url=settings.get("http_proxy", ""),
                    log_fn=self.log,
                )

            successful_remote_ids = {
                int(item[0].get("id") or 0)
                for item in remote_result.get("results", [])
                if item[1] and int(item[0].get("id") or 0) > 0
            }
            failed_remote_ids = {
                int(item[0].get("id") or 0)
                for item in remote_result.get("results", [])
                if not item[1] and int(item[0].get("id") or 0) > 0
            }
            deletable: list[dict[str, object]] = []
            blocked: list[tuple[dict[str, object], str]] = []
            for record in records:
                remote = next(
                    (remote for local, remote in resolved if local is record),
                    None,
                )
                if remote is not None:
                    remote_id = int(remote.get("id") or 0)
                    if remote_id in failed_remote_ids:
                        blocked.append((record, "Sub2API 删除失败，已保留本地账号和2FA资料"))
                    elif remote_id in successful_remote_ids:
                        deletable.append(record)
                    else:
                        blocked.append((record, "Sub2API 删除结果未知，已保留本地账号和2FA资料"))
                elif lookup_ok:
                    deletable.append(record)
                else:
                    blocked.append((record, f"无法读取 Sub2API 列表，未执行本地删除：{lookup_error}"))

            vault_removed = set()
            vault_error = ""
            if deletable:
                try:
                    vault_removed = CredentialVault().delete_accounts(
                        [str(record.get("email") or "") for record in deletable]
                    )
                except Exception as exc:
                    vault_error = str(exc)
                    blocked.extend(
                        (record, f"2FA资料清理失败，已保留本地账号：{vault_error}")
                        for record in deletable
                    )
                    deletable = []

            local_deleted = 0
            for record in deletable:
                try:
                    self.store.delete(record.get("_filename", ""))
                    local_deleted += 1
                except Exception as exc:
                    blocked.append((record, f"本地凭据删除失败：{exc}"))

            return {
                "lookup_ok": lookup_ok,
                "lookup_error": lookup_error,
                "remote_result": remote_result,
                "local_deleted": local_deleted,
                "vault_removed": len(vault_removed),
                "blocked": blocked,
                "unresolved": unresolved,
            }

        def done(result):
            self.set_running(False, "账号删除完成")
            if result.get("error"):
                messagebox.showerror("删除失败", result["error"])
                return
            local_deleted = int(result.get("local_deleted") or 0)
            vault_removed = int(result.get("vault_removed") or 0)
            remote_result = result.get("remote_result") or {}
            remote_success = int(remote_result.get("success_count") or 0)
            remote_failed = int(remote_result.get("fail_count") or 0)
            for record, reason in result.get("blocked") or []:
                self.log(f"删除保留 {record.get('email', 'Unknown')}：{reason}", "error")
            for record, reason in result.get("unresolved") or []:
                self.log(f"删除说明 {record.get('email', 'Unknown')}：{reason}", "warning")
            self.log(
                f"删除完成：本地 {local_deleted} 个，2FA资料 {vault_removed} 个，"
                f"Sub2API远端成功 {remote_success} 个，失败 {remote_failed} 个"
            )
            self.reload_tokens(save_first=False)
            if remote_success and has_remote_config:
                self.refresh_sub2api_accounts()
            messagebox.showinfo(
                "删除完成",
                f"本地删除：{local_deleted}\n"
                f"2FA资料清理：{vault_removed}\n"
                f"Sub2API远端删除成功：{remote_success}\n"
                f"Sub2API远端删除失败：{remote_failed}\n"
                f"保留待处理：{len(result.get('blocked') or [])}",
            )

        self.run_background("正在同步删除账号", worker, done)

    def remove_selected_keep_remote(self) -> None:
        """Remove only local credentials and saved 2FA material.

        This action intentionally does not read or mutate the Sub2API API.
        The remote account remains available for later re-import or manual
        management.
        """
        if self.auto_refresh_running or self.is_running():
            messagebox.showinfo(
                "请先停止维护",
                "请先停止自动维护并等待当前任务结束",
            )
            return
        records = self.selected_records()
        if not records:
            messagebox.showerror("错误", "请先选择账号")
            return
        if not messagebox.askyesno(
            "确认移除本地账号",
            f"确定移除选中的 {len(records)} 个账号吗？\n\n"
            "只清理本地凭据和已保存的2FA资料，保留 Sub2API 远端账号不变。",
        ):
            return

        records = [dict(record) for record in records]
        emails = [
            str(record.get("email") or "").strip()
            for record in records
            if str(record.get("email") or "").strip()
        ]

        def worker():
            try:
                vault_removed = CredentialVault().delete_accounts(emails)
            except Exception as exc:
                return {
                    "error": f"2FA资料清理失败：{exc}",
                    "local_deleted": 0,
                    "vault_removed": 0,
                    "blocked": [],
                }

            local_deleted = 0
            blocked: list[tuple[dict[str, object], str]] = []
            for record in records:
                try:
                    self.store.delete(record.get("_filename", ""))
                    local_deleted += 1
                except Exception as exc:
                    blocked.append((record, f"本地凭据删除失败：{exc}"))
            return {
                "error": "",
                "local_deleted": local_deleted,
                "vault_removed": len(vault_removed),
                "blocked": blocked,
            }

        def done(result):
            self.set_running(False, "本地账号移除完成")
            if result.get("error"):
                self.log(f"移除本地账号失败：{result['error']}", "error")
                messagebox.showerror("移除失败", result["error"])
                return

            # The editor is also cleared on the UI thread so save-on-close
            # cannot resurrect the 2FA material just removed from the vault.
            if hasattr(self, "auth2fa_input"):
                text = remove_account_lines(
                    self.auth2fa_input.get("1.0", "end"),
                    emails,
                )
                self.auth2fa_input.delete("1.0", "end")
                self.auth2fa_input.insert("1.0", text)
                if hasattr(self, "update_auth2fa_input_stats"):
                    self.update_auth2fa_input_stats()
                if hasattr(self, "update_vault_status"):
                    self.update_vault_status()

            for record, reason in result.get("blocked") or []:
                self.log(
                    f"移除本地账号保留 {record.get('email', 'Unknown')}：{reason}",
                    "error",
                )
            local_deleted = int(result.get("local_deleted") or 0)
            vault_removed = int(result.get("vault_removed") or 0)
            self.log(
                f"已移除本地账号（保留Sub2API远端）：本地凭据 {local_deleted} 个，"
                f"2FA资料 {vault_removed} 个"
            )
            self.reload_tokens(save_first=False)
            messagebox.showinfo(
                "移除完成",
                f"本地凭据移除：{local_deleted}\n"
                f"2FA资料清理：{vault_removed}\n"
                "Sub2API远端账号：已保留",
            )

        self.run_background("正在移除本地账号（保留远端）", worker, done)

    def copy_to_clipboard(self, value: str, success_message: str) -> None:
        if not value:
            messagebox.showerror("错误", "内容为空")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.status_var.set(success_message)

    def copy_access_token(self) -> None:
        record = self.primary_record()
        if not record:
            messagebox.showerror("错误", "请先选择账号")
            return
        self.copy_to_clipboard(str(record.get("access_token") or ""), "Access Token 已复制")

    def copy_refresh_token(self) -> None:
        record = self.primary_record()
        if not record:
            messagebox.showerror("错误", "请先选择账号")
            return
        self.copy_to_clipboard(str(record.get("refresh_token") or ""), "Refresh Token 已复制")

    def build_preview(self, format_name: str) -> None:
        record = self.primary_record()
        if not record:
            messagebox.showerror("错误", "请先选择账号")
            return
        if self._is_pending_auth2fa(record):
            messagebox.showinfo("尚未授权", "该账号还没有OAuth Token，完成“智能补授权”后才能生成上传预览。")
            return
        self.save_settings(reload_tokens=False, notify=False)
        payload = sub2api_upload_payload(record, self.current_settings())
        export_target = 'sub2api'
        self.preview_text_value = json.dumps(payload, ensure_ascii=False, indent=2)
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert("1.0", self.preview_text_value)
        if hasattr(self, "right_notebook") and hasattr(self, "convert_tab"):
            self.right_notebook.select(self.convert_tab)
        export_path = self.store.export_payload(str(record.get("email") or ""), export_target, payload)
        self.log(f"已输出 {export_target} 文件: {export_path}")
        self.status_var.set(f"{format_name} 预览已生成")

    def build_preview_from_var(self) -> None:
        self.build_preview(self.preview_format_var.get().strip())

    def copy_preview(self) -> None:
        text = self.preview_text.get("1.0", tk.END).strip()
        self.copy_to_clipboard(text, "预览内容已复制")

    def export_preview_file(self) -> None:
        record = self.primary_record()
        if not record:
            messagebox.showerror("错误", "请先选择账号")
            return
        if self._is_pending_auth2fa(record):
            messagebox.showinfo("尚未授权", "该账号还没有OAuth Token，完成“智能补授权”后才能导出。")
            return
        self.save_settings(reload_tokens=False, notify=False)
        payload = dict(sub2api_upload_payload(record, self.current_settings()))
        # File export is intended for a clean Sub2API import. Never include
        # the local/remote proxy assignment in this user-selected file.
        payload.pop("proxy_id", None)
        payload.pop("proxy", None)
        email = str(record.get("email") or "account").strip()
        selected = filedialog.asksaveasfilename(
            title="导出 Sub2API 账号文件（不含代理）",
            initialfile=f"{safe_email_filename(email)}_sub2api.json",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            parent=self.root,
        )
        if not selected:
            return
        try:
            atomic_write_json(Path(selected), payload)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            self.log(f"导出 Sub2API 文件失败 {email}：{exc}", "error")
            return
        self.log(f"已导出 Sub2API 文件（不含代理）: {selected}")
        self.status_var.set("Sub2API 文件已导出（不含代理）")

    def import_payloads(self, payloads: list[dict[str, object]], source: str) -> int:
        count = 0
        for payload in payloads:
            record = from_sub2api_payload(payload) if "credentials" in payload else from_local_payload(payload)
            if not record.get("access_token") and not record.get("refresh_token"):
                continue
            self.store.save_record(record)
            count += 1
        return count

    def import_from_clipboard(self) -> None:
        try:
            raw = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showerror("错误", "剪贴板为空")
            return
        self._import_text(raw, self.import_source_var.get().strip())

    def import_from_file(self) -> None:
        file_path = filedialog.askopenfilename(
            title="选择 JSON 文件",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            parent=self.root,
        )
        if not file_path:
            return
        raw = Path(file_path).read_text(encoding="utf-8-sig")
        self._import_text(raw, self.import_source_var.get().strip())

    def _import_text(self, raw: str, source: str) -> None:
        if self.auto_refresh_running or self.is_running():
            messagebox.showinfo('请先停止维护', '请先停止自动维护并等待当前任务结束，再导入新凭据')
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            messagebox.showerror("错误", f"JSON 解析失败: {exc}")
            return
        payloads = data if isinstance(data, list) else data.get("accounts", [data]) if isinstance(data, dict) else []
        payloads = [item for item in payloads if isinstance(item, dict)]
        count = self.import_payloads(payloads, source)
        self.log(f"已从 {source} 导入 {count} 条账号")
        self.reload_tokens()
        messagebox.showinfo("完成", f"已导入 {count} 条账号")
