from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from copy import deepcopy

from .integrations import (
    fetch_sub2api_groups,
    fetch_sub2api_proxies,
    fetch_sub2api_accounts,
)
from .sub2api_policy import normalize_server_url, upload_options, match_remote, proxy_candidates, default_list_group_ids
from .gui_widgets import CheckList


FINGERPRINTS = {
    "关闭": "off",
    "设备级收敛": "device",
    "会话级收敛": "session",
    "完整收敛": "full",
}
WS_MODES = {'上下文池（默认）': 'ctx_pool', '关闭': 'off', '透传': 'passthrough', 'HTTP桥接': 'http_bridge'}


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
        dialog.title("Sub2API · 连接、上传与自动维护设置")
        dialog.geometry("740x600")
        dialog.minsize(660, 570)
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
        recovery = ttk.Frame(book, padding=14, style='Card.TFrame')
        book.add(recovery, text='自动维护')
        display = ttk.Frame(book, padding=14, style='Card.TFrame')
        book.add(display, text='列表显示')
        default_groups = tk.StringVar(value=str(cfg.get('default_list_group_ids', '2')))
        ttk.Label(display, text='默认列表分组 ID', style='Card.TLabel').pack(anchor='w', pady=(8,4))
        ttk.Entry(display, textvariable=default_groups).pack(fill='x')
        ttk.Label(display, text='默认 2。多个分组用逗号分隔，留空显示全部。\n\n启动程序、重置筛选时使用；保存修改后立即应用。列表中仍可临时多选其他分组。\n\n仅影响右侧账号列表的默认筛选，不改变上传分组或自动维护的账号范围。', wraplength=560, style='CardSubtle.TLabel').pack(anchor='w', pady=16)
        reauthorize = tk.BooleanVar(value=bool(cfg.get('auto_reauthorize_401', True)))
        auto_monitor = tk.BooleanVar(value=bool(cfg.get('auto_monitor_uploaded_accounts', True)))
        auto_schedule = tk.BooleanVar(value=bool(cfg.get('auto_enable_schedulable', True)))
        test_enabled = tk.BooleanVar(value=bool(cfg.get('recovery_test_enabled', True)))
        test_model = tk.StringVar(value=str(cfg.get('recovery_test_model') or 'gpt-5.5'))
        ttk.Checkbutton(recovery, text='401 后使用已保存的 2FA 资料自动重新授权', variable=reauthorize).pack(anchor='w', pady=8)
        ttk.Checkbutton(recovery, text='已成功上传且唯一匹配的账号自动纳入维护（无需手动点“监控选中”）', variable=auto_monitor).pack(anchor='w', pady=8)
        ttk.Checkbutton(recovery, text='恢复后自动开启 Sub2API 调度并读回确认', variable=auto_schedule).pack(anchor='w', pady=8)
        ttk.Checkbutton(recovery, text='补授权并核验启用、调度状态后，执行一次 Sub2API 模型测试', variable=test_enabled).pack(anchor='w', pady=8)
        ttk.Label(recovery, text='测试模型（默认 gpt-5.5）', style='Card.TLabel').pack(anchor='w', pady=(16, 4))
        ttk.Entry(recovery, textvariable=test_model).pack(fill='x')
        ttk.Label(recovery, text='仅处理左侧已监控账号；需要先在 2FA 页加密保存资料，再启动自动维护。\n\n恢复流程：核对账号 → 刷新或重新授权 → 校验邮箱及工作区 → 写回原账号 → 检查启用和调度 → 模型测试 → 持续轮询。\n\n模型测试会消耗少量额度，每份新凭据只自动测试一次；不按轮询周期反复测试。主动停用的账号不会自动启用；验证码、登录拦截和身份不一致时等待人工处理。', wraplength=570, style='CardSubtle.TLabel').pack(anchor='w', pady=18)
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
        field(10, "proxy_id", "代理池 ID（逗号分隔）", "")
        values['proxy_id'].set(','.join(map(str, proxy_candidates(cfg))))
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
        field(12, 'ws_mode', 'WS mode', 'ctx_pool', options=list(WS_MODES))
        values['ws_mode'].set(next((k for k,v in WS_MODES.items() if v == cfg.get('ws_mode', 'ctx_pool')), '上下文池（默认）'))
        pause = tk.BooleanVar(value=bool(cfg.get("auto_pause_on_expired", True)))
        ttk.Checkbutton(parameters, text="账号到期时自动暂停", variable=pause).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=6
        )
        catalog = tk.StringVar(value="代理可多选：每账号随机分配一个；重复上传保留池内原代理。留空或0为直连，401恢复不换代理。")
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
            params['ws_mode'] = WS_MODES[params['ws_mode']]
            params.update(auto_reauthorize_401=reauthorize.get(), auto_monitor_uploaded_accounts=auto_monitor.get(), auto_enable_schedulable=auto_schedule.get(), recovery_test_enabled=test_enabled.get(), recovery_test_model=test_model.get().strip())
            group_ids = default_list_group_ids(default_groups.get())
            params['default_list_group_ids'] = ','.join(str(i) for i in sorted(group_ids))
            if not params['recovery_test_model'] or len(params['recovery_test_model']) > 160 or any(c.isspace() for c in params['recovery_test_model']):
                raise ValueError('请填写有效的测试模型名称')
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
            if updated['integrations']['sub2api']['default_list_group_ids'] != cfg.get('default_list_group_ids', '2'):
                self.reset_sub2api_default_groups()
                self.populate_sub2api_tree()
            self.status_var.set("Sub2API 配置已保存")
            dialog.destroy()

        ttk.Button(parameters, text='多选分组 / 多选代理…', command=load_choices).grid(row=8, column=0, columnspan=2, sticky='ew', pady=6)
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
        picker.geometry("720x470")
        picker.transient(owner)
        picker.grab_set()
        picker.protocol("WM_DELETE_WINDOW", lambda: (picker.destroy(), owner.grab_set()))
        box = ttk.Frame(picker, padding=16)
        box.pack(fill="both", expand=True)
        columns = ttk.Frame(box)
        columns.pack(fill='both', expand=True)
        columns.columnconfigure((0,1), weight=1)
        columns.rowconfigure(1, weight=1)
        ttk.Label(columns, text="上传分组（多选）").grid(row=0,column=0,sticky='w')
        ttk.Label(columns, text="代理池（多选，随机分配）").grid(row=0,column=1,sticky='w')
        available = [
            g
            for g in data["groups"]
            if g.get("platform") == "openai" and g.get("status") == "active"
        ]
        selected = values["group_ids"].get().replace("，", ",").split(",")
        group_list = CheckList(columns, [(g['id'], f"#{g['id']}  {g['name']}") for g in available], [s.strip() for s in selected])
        group_list.grid(row=1,column=0,sticky='nsew',pady=8,padx=(0,8))
        proxies = [p for p in data["proxies"] if p.get("status") in ("active", "")]
        selected_proxies = proxy_candidates({'proxy_id': values['proxy_id'].get()})
        proxy_list = CheckList(columns, [(p['id'], f"#{p['id']}  {p['name']}") for p in proxies], selected_proxies)
        proxy_list.grid(row=1,column=1,sticky='nsew',pady=8)
        ttk.Button(columns,text='全选代理',command=proxy_list.select_all).grid(row=2,column=1,sticky='w')
        ttk.Button(columns,text='清空代理（直连）',command=lambda: proxy_list.select_all(False)).grid(row=3,column=1,sticky='w',pady=4)
        ttk.Label(box,text='不勾选代理表示直连。多选后每个账号只绑定一个代理；不是每次请求轮换IP。',wraplength=660).pack(fill='x',pady=8)

        def apply():
            selected_ids = group_list.selected()
            if not selected_ids:
                messagebox.showerror(
                    "请选择分组", "至少选择一个上传分组", parent=picker
                )
                return
            values["group_ids"].set(",".join(str(value) for value in selected_ids))
            values["proxy_id"].set(','.join(map(str, proxy_list.selected())))
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
                    "manual_disabled": not enabled,
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
            "② Sub2API恢复：默认自动纳入已成功上传且唯一匹配的账号；也可手动指定监控范围。\n"
            "③ 远端error且明确401时刷新OAuth凭据，再写回原账号并校验；普通429与临时停调度交由服务器处理。\n"
            "④ 保留原分组、并发、代理、指纹设置，不删除或新建远端账号。\n"
            "⑤ 开启自动重新授权后，401不可刷新时使用加密保存的2FA资料登录；身份校验后落盘并补授权。缺资料、登录拦截或身份不符时等待人工。\n"
            "⑥ 补授权后读回凭据；active账号若关闭了持久调度，会自动开启并读回确认，再执行一次Sub2API原生模型测试。模型在Sub2API设置→自动维护中调整。\n"
            "⑦ 失败5/10/20分钟退避，最多3次；一小时内重新授权最多3次。上传失败只重试上传，不重复登录。主动停用账号不启用。\n\n"
            "停止维护会阻止后续操作；正在进行的网络请求需等待返回。",
        )
