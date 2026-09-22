from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from copy import deepcopy

from .integrations import (
    fetch_sub2api_groups,
    fetch_sub2api_proxies,
    fetch_sub2api_accounts,
)
from .auth_proxy import authorization_proxy_pool
from .sub2api_policy import normalize_server_url, upload_options, match_remote, proxy_candidates, default_list_group_ids
from .gui_widgets import CheckmarkOption, ScrollableFrame, center_window


FINGERPRINTS = {
    "关闭": "off",
    "设备级收敛": "device",
    "会话级收敛": "session",
    "完整收敛": "full",
}
WS_MODES = {'上下文池（默认）': 'ctx_pool', '关闭': 'off', '透传': 'passthrough', 'HTTP桥接': 'http_bridge'}


def maintenance_rules_text(settings):
    """Describe the current implementation rather than a historical workflow."""
    try:
        interval = max(30, int(settings.get("auto_refresh_interval_seconds") or 60))
    except (TypeError, ValueError):
        interval = 60
    try:
        threshold = max(30, int(settings.get("auto_refresh_threshold_seconds") or 300))
    except (TypeError, ValueError):
        threshold = 300
    return (
        "需要手动点击“启动自动维护”；程序关闭后停止运行。\n\n"
        f"① 本地到期刷新：每 {interval} 秒检查一次。只刷新未纳入 Sub2API 监控、"
        f"尚未过期、剩余 1～{threshold} 秒且存在 Refresh Token 的本地凭据。\n\n"
        "② 监控范围：显式点击“监控选中”的账号会被检查；开启自动纳入后，"
        "只有本地记录显示 Sub2API 上传成功、且邮箱/稳定工作区唯一匹配的账号才自动纳入。"
        "手动取消监控会持续排除，远端未匹配时不会自动新建账号。\n\n"
        "③ 触发条件：仅远端 status=error，且错误明确包含 401 或属于永久撤销标记时才进入授权恢复。"
        "普通 429、非 401 错误、限流/过载/临时冷却和 status=inactive 均不会触发重新登录。\n\n"
        "④ 恢复顺序：可刷新的 401 先使用远端保存的 Refresh Token 刷新；"
        "token_revoked、token_invalidated、invalid_grant 等永久撤销类错误直接进入重新授权。"
        "重新授权必须已开启，并使用 DPAPI 加密保存的密码与 TOTP 资料。\n\n"
        "⑤ 身份与代理：新凭据必须通过邮箱、稳定工作区、完整性和有效期校验。"
        "本地刷新/重新授权只使用“授权代理”，Sub2API 管理接口固定直连，"
        "不会借用远端 proxy_id。验证码、Cloudflare、登录挑战或身份不一致会转人工。\n\n"
        "⑥ 写回边界：只调用 apply-oauth-credentials 更新原远端账号，不新建或删除账号，"
        "不覆盖分组、并发、优先级、倍率、代理和指纹等运营参数。远端删除重建时，"
        "仅在旧绑定已不存在且邮箱与稳定工作区唯一一致时自动重绑。\n\n"
        "⑦ 调度与模型测试：补授权写回后必须读回确认凭据一致且账号 active。"
        "若开启“恢复后自动开启调度”，只在恢复/待验证阶段开启 schedulable 并读回确认；"
        "限流或冷却结束前等待。模型测试仅在补授权后执行，每份新凭据最多一次；"
        "普通轮询只读取状态，模型测试失败不等于授权写回失败。\n\n"
        "⑧ 重试与冲突：恢复失败按 5/10/20 分钟退避，连续最多 3 次；"
        "一小时内自动重新授权最多 3 次，重新授权之间至少间隔 5 分钟。"
        "上传失败会保留新本地凭据，下轮只重试上传，不重复登录；"
        "待同步期间远端凭据被其他任务修改时停止覆盖并标记“凭据冲突”。\n\n"
        "⑨ 停止语义：停止维护会阻止后续账号和后续阶段；正在进行的网络调用会等待返回。"
        "已经生成的新凭据会先保存，再留待下次继续同步。\n\n"
        "有好的功能建议可以反馈，会考虑添加进去。\n"
        "VX：blackdm\n"
        "技术交流群：1004036018"
    )


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
        dialog.geometry("820x700")
        dialog.minsize(720, 600)
        dialog.transient(self.root)
        center_window(dialog, self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=20, style="Card.TFrame")
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        book = ttk.Notebook(frame)
        book.grid(row=0, column=0, sticky="nsew")
        connection = ttk.Frame(book, padding=14, style="Card.TFrame")
        parameters_scroll = ScrollableFrame(book)
        parameters = ttk.Frame(parameters_scroll.body, padding=12, style="Card.TFrame")
        parameters.pack(fill="both", expand=True)
        book.add(connection, text="服务器连接")
        book.add(parameters_scroll, text="上传参数")
        recovery_scroll = ScrollableFrame(book)
        recovery = ttk.Frame(recovery_scroll.body, padding=12, style="Card.TFrame")
        recovery.pack(fill="both", expand=True)
        book.add(recovery_scroll, text="自动维护")
        display_scroll = ScrollableFrame(book)
        display = ttk.Frame(display_scroll.body, padding=12, style="Card.TFrame")
        display.pack(fill="both", expand=True)
        book.add(display_scroll, text="列表显示")
        default_groups = tk.StringVar(value=str(cfg.get('default_list_group_ids', '2')))
        ttk.Label(display, text='默认列表分组 ID', style='Card.TLabel').pack(anchor='w', pady=(8,4))
        ttk.Entry(display, textvariable=default_groups).pack(fill='x')
        ttk.Label(display, text='默认 2。多个分组用逗号分隔，留空显示全部。\n\n启动程序、重置筛选时使用；保存修改后立即应用。列表中仍可临时多选其他分组。\n\n仅影响右侧账号列表的默认筛选，不改变上传分组或自动维护的账号范围。', wraplength=560, style='CardSubtle.TLabel').pack(anchor='w', pady=16)
        reauthorize = tk.BooleanVar(value=bool(cfg.get('auto_reauthorize_401', True)))
        auto_monitor = tk.BooleanVar(value=bool(cfg.get('auto_monitor_uploaded_accounts', True)))
        auto_schedule = tk.BooleanVar(value=bool(cfg.get('auto_enable_schedulable', True)))
        test_enabled = tk.BooleanVar(value=bool(cfg.get('recovery_test_enabled', True)))
        test_model = tk.StringVar(value=str(cfg.get('recovery_test_model') or 'gpt-5.5'))
        for variable, text in (
            (reauthorize, '401 后使用已保存的 2FA 资料自动重新授权'),
            (auto_monitor, '已成功上传且唯一匹配的账号自动纳入维护（无需手动点“监控选中”）'),
            (auto_schedule, '恢复后自动开启 Sub2API 调度并读回确认'),
            (test_enabled, '补授权并核验启用、调度状态后，执行一次 Sub2API 模型测试'),
        ):
            CheckmarkOption(
                recovery,
                text=text,
                variable=variable,
                palette=self.palette,
                wraplength=650,
            ).pack(fill='x', anchor='w', pady=7)
        ttk.Label(recovery, text='测试模型（默认 gpt-5.5）', style='Card.TLabel').pack(anchor='w', pady=(16, 4))
        ttk.Entry(recovery, textvariable=test_model).pack(fill='x')
        ttk.Label(recovery, text='处理显式监控账号，以及开启自动纳入后已成功上传且唯一匹配的本地账号；手动取消监控会保持排除。需要先在 2FA 页加密保存资料，再启动自动维护。\n\n恢复流程：核对账号 → 刷新或重新授权 → 校验邮箱及工作区 → 写回原账号 → 检查启用和调度 → 模型测试 → 持续轮询。\n\n模型测试会消耗少量额度，每份新凭据只自动测试一次；不按轮询周期反复测试。主动停用的账号不会自动启用；验证码、登录拦截和身份不一致时等待人工处理。', wraplength=570, style='CardSubtle.TLabel').pack(anchor='w', pady=18)
        connection.columnconfigure(1, weight=1)
        parameters.columnconfigure(1, weight=1)
        book.select(parameters_scroll)
        values = {}
        group_menu_holder = {}
        group_menu_vars = {}
        group_menu_labels = {}
        proxy_menu_holder = {}
        proxy_menu_vars = {}
        proxy_menu_labels = {}
        group_display_var = tk.StringVar(value='正在读取分组…')
        proxy_display_var = tk.StringVar(value='正在读取代理…')

        def selected_values(key):
            return [
                value.strip()
                for value in str(values[key].get() or "").replace("，", ",").split(",")
                if value.strip()
            ]

        def sync_group_menu():
            selected = [
                str(group_id)
                for group_id, variable in group_menu_vars.items()
                if variable.get()
            ]
            values['group_ids'].set(','.join(selected))
            if not selected:
                group_display_var.set('请选择至少一个分组')
            elif len(selected) == 1:
                group_display_var.set(group_menu_labels.get(selected[0], f"#{selected[0]}"))
            else:
                group_display_var.set(f"已选 {len(selected)} 个分组")

        def update_group_menu(groups):
            menu = group_menu_holder.get('menu')
            if menu is None:
                return
            menu.delete(0, 'end')
            group_menu_vars.clear()
            group_menu_labels.clear()
            selected = set(selected_values('group_ids'))
            choices = []
            known = set()
            for item in groups:
                if item.get("platform") != "openai" or item.get("status") != "active":
                    continue
                group_id = str(item['id'])
                known.add(group_id)
                choices.append((group_id, f"#{group_id}  {item.get('name') or '分组'}"))
            choices.extend(
                (group_id, f"#{group_id}") for group_id in sorted(selected - known)
            )
            for group_id, label in choices:
                variable = tk.BooleanVar(value=group_id in selected)
                group_menu_vars[group_id] = variable
                group_menu_labels[group_id] = label
                menu.add_checkbutton(
                    label=label,
                    variable=variable,
                    command=sync_group_menu,
                )
            if not choices:
                menu.add_command(label='暂无分组，请检查服务器连接', state='disabled')
            sync_group_menu()

        def update_proxy_menu(proxies):
            menu = proxy_menu_holder.get('menu')
            if menu is None:
                return
            menu.delete(0, 'end')
            proxy_menu_vars.clear()
            proxy_menu_labels.clear()
            try:
                selected = {str(value) for value in proxy_candidates({'proxy_id': values['proxy_id'].get()})}
            except ValueError:
                selected = set()
            choices = []
            known = set()
            for item in proxies:
                if item.get('status') not in ('active', ''):
                    continue
                proxy_id = str(item['id'])
                known.add(proxy_id)
                choices.append((proxy_id, f"#{proxy_id}  {item.get('name') or '代理'}"))
            choices.extend(
                (proxy_id, f"#{proxy_id}") for proxy_id in sorted(selected - known)
            )
            for proxy_id, label in choices:
                variable = tk.BooleanVar(value=proxy_id in selected)
                proxy_menu_vars[proxy_id] = variable
                proxy_menu_labels[proxy_id] = label
                menu.add_checkbutton(
                    label=label,
                    variable=variable,
                    command=sync_proxy_menu,
                )
            if not choices:
                menu.add_command(label='暂无代理，当前使用直连', state='disabled')
            sync_proxy_menu()

        def sync_proxy_menu():
            selected = [str(proxy_id) for proxy_id, variable in proxy_menu_vars.items() if variable.get()]
            values['proxy_id'].set(','.join(selected))
            if not selected:
                proxy_display_var.set('否（直连）')
            elif len(selected) == 1:
                proxy_display_var.set('是 · ' + proxy_menu_labels.get(selected[0], f"#{selected[0]}"))
            else:
                proxy_display_var.set(f"是 · 已选 {len(selected)} 个代理")

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
            if key == 'group_ids':
                widget = ttk.Menubutton(container, textvariable=group_display_var)
                menu = tk.Menu(widget, tearoff=False)
                widget.configure(menu=menu)
                group_menu_holder['menu'] = menu
            elif key == 'proxy_id':
                widget = ttk.Menubutton(container, textvariable=proxy_display_var)
                menu = tk.Menu(widget, tearoff=False)
                widget.configure(menu=menu)
                proxy_menu_holder['menu'] = menu
            elif options:
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
        field(1, "api_key", "管理员 API Key", secret=True)
        ttk.Label(
            connection,
            text="当前只使用管理员 API Key，不需要管理员邮箱或密码。Sub2API 管理接口固定直连。",
            wraplength=550,
            style="CardSubtle.TLabel",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        field(6, "group_ids", "添加到分组", "2")
        update_group_menu([])
        field(7, "concurrency", "并发数", 10)
        field(8, "priority", "调度优先级", 1)
        field(9, "rate_multiplier", "账号倍率", 1)
        field(10, "proxy_id", "账号是否使用代理", "")
        values['proxy_id'].set(','.join(map(str, proxy_candidates(cfg))))
        update_proxy_menu([])
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
        pause_choice = tk.StringVar(
            value="是" if bool(cfg.get("auto_pause_on_expired", True)) else "否"
        )
        ttk.Label(
            parameters,
            text="账号到期时自动暂停",
            style="Card.TLabel",
        ).grid(row=7, column=0, sticky="w", pady=6, padx=(0, 14))
        ttk.Combobox(
            parameters,
            textvariable=pause_choice,
            values=("是", "否"),
            state="readonly",
        ).grid(row=7, column=1, sticky="ew", pady=6)
        catalog = tk.StringVar(value="正在自动读取 Sub2API 分组和代理信息…")
        ttk.Label(
            frame, textvariable=catalog, wraplength=630, style="CardSubtle.TLabel"
        ).grid(row=1, column=0, sticky="ew", pady=(8, 0))
        actions = ttk.Frame(frame, style="Card.TFrame")
        actions.grid(row=2, column=0, sticky="ew", pady=(10, 0))

        def collect(validate=True):
            updated = deepcopy(settings)
            params = {k: v.get().strip() for k, v in values.items()}
            params["api_url"] = normalize_server_url(params["api_url"])
            params["codex_fingerprint_mode"] = FINGERPRINTS[
                params["codex_fingerprint_mode"]
            ]
            params["auto_pause_on_expired"] = pause_choice.get() == "是"
            params['ws_mode'] = WS_MODES[params['ws_mode']]
            params.update(auto_reauthorize_401=reauthorize.get(), auto_monitor_uploaded_accounts=auto_monitor.get(), auto_enable_schedulable=auto_schedule.get(), recovery_test_enabled=test_enabled.get(), recovery_test_model=test_model.get().strip())
            group_ids = default_list_group_ids(default_groups.get())
            params['default_list_group_ids'] = ','.join(str(i) for i in sorted(group_ids))
            if not params['recovery_test_model'] or len(params['recovery_test_model']) > 160 or any(c.isspace() for c in params['recovery_test_model']):
                raise ValueError('请填写有效的测试模型名称')
            if validate:
                upload_options(params)
            # A changed login or server must not retain another session.
            params.pop("auth_mode", None)
            params.pop("admin_email", None)
            params.pop("admin_password", None)
            params.pop("access_token", None)
            params.pop("refresh_token", None)
            params.pop("token_expires_at", None)
            updated["integrations"]["sub2api"].update(params)
            for legacy_key in (
                "auth_mode",
                "admin_email",
                "admin_password",
                "access_token",
                "refresh_token",
                "token_expires_at",
            ):
                updated["integrations"]["sub2api"].pop(legacy_key, None)
            return updated

        def load_choices():
            raw_url = values["api_url"].get().strip()
            api_key = values["api_key"].get().strip()
            if not raw_url or raw_url == "https://" or not api_key:
                catalog.set("未自动读取：请先配置并保存服务器地址与管理员 API Key。")
                return
            try:
                snapshot = collect(False)
            except ValueError as exc:
                catalog.set("未自动读取：" + str(exc))
                return

            def worker():
                return {
                    "groups": fetch_sub2api_groups(
                        snapshot, proxy_url=""
                    ),
                    "proxies": fetch_sub2api_proxies(
                        snapshot, proxy_url=""
                    ),
                }

            def done(result):
                self.set_running(False, "Sub2API 分组与代理读取完成")
                if not dialog.winfo_exists():
                    return
                if result.get("error"):
                    catalog.set("自动读取失败：" + result["error"])
                    return
                update_group_menu(result['groups'])
                update_proxy_menu(result['proxies'])
                catalog.set(
                    f"已自动读取 {len(result['groups'])} 个分组、{len(result['proxies'])} 个代理；可直接在上方下拉菜单选择。"
                )

            self.run_background("自动读取 Sub2API 分组与代理", worker, done)

        def save():
            if self.is_running():
                return
            try:
                updated = collect()
                authorization_proxy_pool(updated)
            except (ValueError, KeyError) as exc:
                messagebox.showerror("配置无效", str(exc), parent=dialog)
                return
            self.persist_runtime_settings(updated)
            for key, var in [
                ("api_url", self.sub2api_url_var),
                ("api_key", self.sub2api_key_var),
                ("group_ids", self.sub2api_group_ids_var),
            ]:
                var.set(updated["integrations"]["sub2api"].get(key, ""))
            if updated['integrations']['sub2api']['default_list_group_ids'] != cfg.get('default_list_group_ids', '2'):
                self.reset_sub2api_default_groups()
                self.populate_sub2api_tree()
            self.status_var.set("Sub2API 配置已保存")
            dialog.destroy()

        ttk.Button(actions, text="取消", command=dialog.destroy).pack(side="right")
        ttk.Button(
            actions, text="保存配置", command=save, style="Primary.TButton"
        ).pack(side="right", padx=8)
        self._install_default_tooltips(dialog)
        dialog.after_idle(load_choices)

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
                    proxy_url="",
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
        dialog = tk.Toplevel(self.root)
        dialog.title("自动维护规则")
        dialog.geometry("780x620")
        dialog.minsize(640, 440)
        dialog.transient(self.root)
        center_window(dialog, self.root)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=14, style="Card.TFrame")
        frame.pack(fill=tk.BOTH, expand=True)
        text = scrolledtext.ScrolledText(
            frame,
            wrap=tk.WORD,
            font=("Microsoft YaHei UI", 10),
            bg=self.palette["card"],
            fg=self.palette["text"],
            relief="flat",
            insertbackground=self.palette["text"],
            highlightthickness=1,
            highlightbackground=self.palette["border"],
            padx=10,
            pady=10,
        )
        text.pack(fill=tk.BOTH, expand=True)
        text.insert("1.0", maintenance_rules_text(self.current_settings()))
        text.config(state=tk.DISABLED)
        ttk.Button(
            frame,
            text="关闭",
            command=dialog.destroy,
            style="Primary.TButton",
        ).pack(anchor=tk.E, pady=(10, 0))
        self._install_default_tooltips(dialog)
