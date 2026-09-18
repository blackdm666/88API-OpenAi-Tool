from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from copy import deepcopy

from .integrations import (
    fetch_sub2api_groups,
    fetch_sub2api_proxies,
    fetch_sub2api_accounts,
)
from .sub2api_policy import normalize_server_url, upload_options, match_remote


FINGERPRINTS = {
    "关闭": "off",
    "设备级收敛": "device",
    "会话级收敛": "session",
    "完整收敛": "full",
}


class GUISub2APISettingsMixin:
    def open_sub2api_upload_settings(self):
        if self.is_running() or self.auto_refresh_running:
            messagebox.showinfo(
                "请稍候", "请先停止自动维护并等待当前任务完成，再调整连接和上传参数。"
            )
            return
        settings = self.current_settings()
        cfg = deepcopy(settings["integrations"]["sub2api"])
        dialog = tk.Toplevel(self.root)
        dialog.title("Sub2API · 连接与上传设置")
        dialog.geometry("700x540")
        dialog.minsize(620, 500)
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=20, style="Card.TFrame")
        frame.pack(fill="both", expand=True)
        book = ttk.Notebook(frame)
        book.pack(fill="both", expand=True)
        connection = ttk.Frame(book, padding=14, style="Card.TFrame")
        parameters = ttk.Frame(book, padding=14, style="Card.TFrame")
        book.add(connection, text="服务器连接")
        book.add(parameters, text="上传参数")
        connection.columnconfigure(1, weight=1)
        parameters.columnconfigure(1, weight=1)
        book.select(parameters)
        values = {}

        def field(row, key, label, default="", secret=False, options=None):
            container = connection if row <= 4 else parameters
            row = row if row <= 4 else row - 6
            ttk.Label(container, text=label, style="Card.TLabel").grid(
                row=row, column=0, sticky="w", pady=6, padx=(0, 14)
            )
            values[key] = tk.StringVar(
                value=str(
                    cfg.get(key, default) if cfg.get(key, default) is not None else ""
                )
            )
            if options:
                widget = ttk.Combobox(
                    container,
                    textvariable=values[key],
                    values=options,
                    state="readonly",
                )
            else:
                widget = ttk.Entry(
                    container, textvariable=values[key], show="•" if secret else ""
                )
            widget.grid(row=row, column=1, sticky="ew", pady=6)
            return widget

        field(0, "api_url", "服务器地址", "https://")
        field(
            1, "auth_mode", "鉴权方式", "auto", options=["auto", "api_key", "password"]
        )
        field(2, "api_key", "管理 API Key / Token", secret=True)
        field(3, "admin_email", "管理员邮箱")
        field(4, "admin_password", "管理员密码", secret=True)
        ttk.Label(
            connection,
            text="auto 优先使用 Key；api_key 仅用 Key；password 仅用邮箱密码。",
            wraplength=550,
            style="CardSubtle.TLabel",
        ).grid(row=5, column=0, columnspan=2, sticky="w")
        field(6, "group_ids", "上传分组 ID（逗号分隔）", "2")
        field(7, "concurrency", "并发数", 10)
        field(8, "priority", "调度优先级", 1)
        field(9, "rate_multiplier", "账号倍率", 1)
        field(10, "proxy_id", "IP代理 ID（空或0为直连）", "")
        field(
            11,
            "codex_fingerprint_mode",
            "Codex 设备指纹收敛",
            "off",
            options=list(FINGERPRINTS),
        )
        values["codex_fingerprint_mode"].set(
            next(
                (
                    k
                    for k, v in FINGERPRINTS.items()
                    if v == cfg.get("codex_fingerprint_mode", "off")
                ),
                "关闭",
            )
        )
        pause = tk.BooleanVar(value=bool(cfg.get("auto_pause_on_expired", True)))
        ttk.Checkbutton(parameters, text="账号到期时自动暂停", variable=pause).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=6
        )
        catalog = tk.StringVar(value="读取远端选项后可查看分组和代理的名称、ID。")
        ttk.Label(
            frame, textvariable=catalog, wraplength=630, style="CardSubtle.TLabel"
        ).pack(fill="x", pady=8)
        actions = ttk.Frame(frame, style="Card.TFrame")
        actions.pack(fill="x", pady=(10, 0))

        def collect(validate=True):
            updated = deepcopy(settings)
            params = {k: v.get().strip() for k, v in values.items()}
            params["api_url"] = normalize_server_url(params["api_url"])
            params["codex_fingerprint_mode"] = FINGERPRINTS[
                params["codex_fingerprint_mode"]
            ]
            params["auto_pause_on_expired"] = pause.get()
            if validate:
                upload_options(params)
            # A changed login or server must not retain another session.
            if any(
                params[k] != str(cfg.get(k, ""))
                for k in ("api_url", "auth_mode", "admin_email", "admin_password")
            ):
                params.update(access_token="", refresh_token="", token_expires_at=0)
            updated["integrations"]["sub2api"].update(params)
            return updated

        def load_choices():
            try:
                snapshot = collect(False)
            except ValueError as exc:
                messagebox.showerror("配置无效", str(exc), parent=dialog)
                return

            def worker():
                return {
                    "groups": fetch_sub2api_groups(
                        snapshot, proxy_url=snapshot.get("http_proxy", "")
                    ),
                    "proxies": fetch_sub2api_proxies(
                        snapshot, proxy_url=snapshot.get("http_proxy", "")
                    ),
                }

            def done(result):
                self.set_running(False, "远端选项已读取")
                if not dialog.winfo_exists():
                    return
                if result.get("error"):
                    catalog.set("读取失败：" + result["error"])
                    return
                self.show_sub2api_option_picker(dialog, result, values)
                catalog.set(
                    f"已读取 {len(result['groups'])} 个分组、{len(result['proxies'])} 个代理。参数只在保存后生效。"
                )

            self.run_background("读取 Sub2API 分组与代理", worker, done)

        def save():
            if self.is_running():
                return
            try:
                updated = collect()
            except (ValueError, KeyError) as exc:
                messagebox.showerror("配置无效", str(exc), parent=dialog)
                return
            self.persist_runtime_settings(updated)
            for key, var in [
                ("api_url", self.sub2api_url_var),
                ("api_key", self.sub2api_key_var),
                ("group_ids", self.sub2api_group_ids_var),
                ("admin_email", self.sub2api_admin_email_var),
                ("admin_password", self.sub2api_admin_password_var),
            ]:
                var.set(updated["integrations"]["sub2api"].get(key, ""))
            self.sub2api_auth_mode_var.set(
                updated["integrations"]["sub2api"]["auth_mode"]
            )
            self.status_var.set("上传参数已保存；手动上传时应用，自动恢复仅更新凭据")
            dialog.destroy()

        ttk.Button(actions, text="读取分组 / 代理", command=load_choices).pack(
            side="left"
        )
        ttk.Button(actions, text="取消", command=dialog.destroy).pack(side="right")
        ttk.Button(
            actions, text="保存配置", command=save, style="Primary.TButton"
        ).pack(side="right", padx=8)

    def show_sub2api_option_picker(self, owner, data, values):
        picker = tk.Toplevel(owner)
        picker.title("选择上传分组与代理")
        picker.geometry("600x420")
        picker.transient(owner)
        picker.grab_set()
        box = ttk.Frame(picker, padding=16)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="分组（可按 Ctrl / Shift 多选）").pack(anchor="w")
        group_list = tk.Listbox(
            box, selectmode="extended", exportselection=False, height=9
        )
        group_list.pack(fill="both", expand=True, pady=8)
        available = [
            g
            for g in data["groups"]
            if g.get("platform") == "openai" and g.get("status") == "active"
        ]
        selected = values["group_ids"].get().replace("，", ",").split(",")
        for i, group in enumerate(available):
            group_list.insert("end", f"#{group['id']}  {group['name']}")
            if str(group["id"]) in [s.strip() for s in selected]:
                group_list.selection_set(i)
        proxies = [p for p in data["proxies"] if p.get("status") in ("active", "")]
        labels = ["直连（不使用代理）"] + [f"#{p['id']}  {p['name']}" for p in proxies]
        proxy_box = ttk.Combobox(box, values=labels, state="readonly")
        proxy_box.pack(fill="x", pady=8)
        current = next(
            (
                i + 1
                for i, p in enumerate(proxies)
                if str(p["id"]) == values["proxy_id"].get()
            ),
            0,
        )
        proxy_box.current(current)

        def apply():
            indices = group_list.curselection()
            if not indices:
                messagebox.showerror(
                    "请选择分组", "至少选择一个上传分组", parent=picker
                )
                return
            values["group_ids"].set(",".join(str(available[i]["id"]) for i in indices))
            index = proxy_box.current()
            values["proxy_id"].set(str(proxies[index - 1]["id"]) if index > 0 else "")
            picker.destroy()
            owner.grab_set()

        ttk.Button(box, text="应用选择", command=apply, style="Primary.TButton").pack(
            anchor="e", pady=8
        )

    def set_recovery_selected(self, enabled):
        records = self.selected_records()
        if not records:
            messagebox.showinfo("选择账号", "请先在左侧选择要监控的本地账号")
            return
        if self.auto_refresh_running:
            messagebox.showinfo("停止维护", "请先停止自动维护，再调整监控账号范围")
            return
        settings = self.current_settings()
        self.save_settings(reload_tokens=False, notify=False)
        store = self.store

        def worker():
            if not enabled:
                remotes = []
                server = ""
            else:
                server = normalize_server_url(
                    settings["integrations"]["sub2api"]["api_url"]
                )
                remotes = fetch_sub2api_accounts(
                    settings,
                    proxy_url=settings.get("http_proxy", ""),
                    filters={"platform": "openai"},
                )
            plans = []
            for record in records:
                remote = (
                    match_remote({**record, "sub2api_recovery": {}}, remotes)
                    if enabled
                    else None
                )
                if enabled and not remote:
                    raise ValueError(
                        f"{record.get('email', '')} 未找到唯一匹配的远端 OAuth 账号"
                    )
                state = {
                    "enabled": enabled,
                    "status": "待检查" if enabled else "未监控",
                }
                if enabled:
                    state.update(server=server, remote_id=remote["id"])
                plans.append((record, state))
            for record, state in plans:
                fresh = deepcopy(record)
                fresh["sub2api_recovery"] = state
                store.save_record(fresh, filename=record.get("_filename"))
            return {"count": len(plans)}

        def done(result):
            self.set_running(False, "监控范围已更新")
            if result.get("error"):
                messagebox.showerror("监控设置失败", result["error"])
            self.reload_tokens(save_first=False)

        self.run_background("核对远端账号绑定", worker, done)

    def show_maintenance_rules(self):
        messagebox.showinfo(
            "自动维护规则",
            "需要点击“启动自动维护”，程序关闭后停止运行。\n\n"
            "① 本地到期维护：默认每60秒检查；仅刷新未过期且剩余≤300秒的账号。\n"
            "② Sub2API恢复：仅处理左侧手动开启监控、身份唯一匹配的账号。\n"
            "③ 远端error且明确401时刷新OAuth凭据，再写回原账号并校验；普通429与临时停调度交由服务器处理。\n"
            "④ 保留原分组、并发、代理、指纹设置，不删除或新建远端账号。\n"
            "⑤ 失败按5/10/20分钟退避，最多3次；永久撤销转“需要重新授权”。\n"
            "⑥ 在授权页完成官方登录并保存到同一邮箱后，下一轮同步新凭据。\n\n"
            "停止维护会阻止后续操作；正在进行的网络请求需等待返回。",
        )
