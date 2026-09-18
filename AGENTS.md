# OpenAI Token Manager fork

- 上游：xiaopenghuang/Token-Manager；fork：blackdm666/Token-Manager。
- 本地独立仓库；维护分支codex/functional-improvements，origin为fork，upstream为上游。
- 保留原项目名称、LICENSE和作者署名。运行数据tokens/、outputs/、token_manager_config.json和构建dist/均不入Git。
- Sub2API业务配置使用官方管理API，服务器规则仍遵守工作区AGENTS。生产只读验收可使用sub2api-maintenance/AGENTS.md中的凭据读取方式，禁止复制真实Key或账号token到源码、测试、日志、报告或打包产物。
- 上传配置：并发、优先级、倍率、proxy_id、group_ids、extra.codex_fingerprint_mode；WS默认ctx_pool，对应extra.openai_oauth_responses_websockets_v2_mode及enabled。以Sub2API运行版本API契约为准，不自行编造字段。
- 2.2.0-88api.2起已移除CPA功能，勿恢复CPA界面/上传/导出依赖；历史磁盘文件不得因代码清理而删除。分组选择使用CheckList复选框，支持多选。
- 2.2.0-88api.5起本地proxy_id配置兼容逗号分隔/列表代理池，服务端API仍只提交单个proxy_id。新账号随机分配并保存本地assignment，重试沿用，远端已有池内代理优先保留；自动401恢复不改代理。切勿把代理ID列表直接发送到Sub2API的proxy_id字段。
- 默认左右50/50；大标题已移除，状态与控制放底部“运行信息”，日志和远端详情为页签，收起后仍保留控制栏。
- 2.2.0-88api.6右侧默认及重置筛选均oauth；排序显式请求name/asc并保留服务端顺序，本地已匹配账号跟随远端顺序。右侧无嵌套列表页签与身份详情占位，账号操作在菜单，用量明细双击查看底部页签。
- 5h/7d用量先读账号extra里的codex快照；手动更新最多50账号，官方usage/batch每批20、force=false（服务器可能按自身规则刷新过期快照）。缺失不是0，过期显示待更新。禁止估算剩余Token；不能将列表账号名排序等同于用户浏览器保存的任意自定义排序。
- 2.2.0-88api.8覆盖上述旧版排序/布局描述：默认ID数值升序，右侧表头可升降序，用量列按数字排序，暂无项放后。日志常显且无用量明细页签，清空按钮浮于日志角落；规则与布局切换在右侧导航行；本地统计在标题旁。使用UsageTreeview为可见额度单元格绘制进度条。已配置连接启动后读列表，每60秒同步服务器已有快照（GET账号列表、不调用批量用量），自动维护运行时复用其状态轮询；手动更新用量仍显式调用usage/batch。
- 2FA返回兼容JSON会话与Cookie、本地RFC6238生成验证码；不得改为将2FA密匙发送给第三方网站，不绕过Cloudflare/CAPTCHA等安全检查，异常必须明确阶段和处理建议。
- 账号自动恢复必须显式开启，限制到已导入本地且唯一匹配的OpenAI OAuth账号。普通429、主动停用、临时停调度不自动恢复。永久撤销需重新授权，不尝试绕过登录或封禁。
- 恢复使用apply-oauth-credentials官方入口更新原账号，不新建/删除账号，不覆盖原分组/并发/代理/指纹。必须检查工作区身份、并发凭据变化、读回结果；本地刷新结果先落盘，网络失败重试上传，不反复旋转refresh token。
- 回归命令：`python -m unittest discover -s tests -v`、`python -m compileall -q token_manager tests`、`git diff --check`。GUI测试使用临时目录和合成账号，禁止载入生产凭据。
- 打包：`python build.py --name OpenAI-Token-Manager-88API --entry-point main.py`，无需`--clean`；不删除用户已有dist内运行数据。
- GitHub发布、全量生产自动恢复需依据当次用户授权，不因为凭据可用而自行启用。

- 2.2.0-88api.9：任务忙碌或自动维护运行时，重复点击刷新等操作仅记录日志提示，不再弹出阻塞提示框；禁止在任务锁内调用Tk弹窗或其他UI回调，避免定时器重入死锁。

- 2.2.0-88api.10经用户明确授权扩展自动维护：401/永久撤销可使用CredentialVault中的账号密码与TOTP重新登录，当前Windows用户DPAPI加密文件位于系统文档/OpenAI-Token-Manager/credentials/accounts.dpapi。仅处理已监控、唯一匹配账号；新凭据严格校验邮箱/工作区/有效期后落盘，再用原账号apply-oauth-credentials补授权。自动登录不写诊断报告，不绕过登录挑战。缺资料、挑战、身份不符或超过重试限制转人工。
- Sub2API设置新增自动维护页：auto_reauthorize_401/recovery_test_enabled默认true，recovery_test_model默认gpt-5.5（已用#384原生测试验证）。补授权后先GET核验active/schedulable/冷却，再POST accounts/:id/test解析SSE test_complete.success；每新凭据至多一次，持久化测试开始避免崩溃后重复计费。429/主动停用不触发登录，测试失败不等同授权失败。人工停用不自动启用。普通轮询仅GET不发模型请求。
- 凭据资料只在2FA导入、点击加密保存、启动批量授权或正常退出时保存；启动只显示资料数量，点击载入才解密显示。旧版本未持久化的输入不可自动恢复，需首次导入。测试必须patch资料库或使用临时路径，不能加载真实资料；禁止为测试人为制造生产401。

- 生产接口注意：列表与详情会隐藏OAuth Token。普通轮询显示“凭据已隐藏”，不误报不一致；仅恢复阶段通过官方GET accounts/data?ids=<单账号ID>&include_proxies=false读取凭据，在内存校验身份/并发变化/补授权读回，不落盘导出文件。接口403时停止恢复，不绕过二次验证。实测当前管理员接口可用。

- Sub2API列表默认筛选分组ID2（精确按ID，不依赖分组名称）。在“Sub2API设置→列表显示”编辑默认列表分组ID，支持逗号分隔多选，留空显示全部；启动、重置筛选和修改保存后应用。临时多选不改变默认配置。此字段default_list_group_ids独立于上传group_ids及监控范围。

- 2.2.0-88api.11：Sub2API列表在状态右侧增加“调度”列，可点击排序。依据schedulable、status及冷却截止时间显示参与调度/已关闭/开启·账号停用或异常/开启·临时、限流、过载冷却；未知不当作开启。调度表示服务器快照中的参与资格，不代表当前正在处理请求；随列表轮询更新，不修改远端调度开关。

- 2.2.0-88api.12：自动维护默认纳入已有Sub2API成功上传且唯一匹配的本地账号，不再要求先点击监控选中；“取消监控”写入manual_disabled，保持人工排除。恢复或检查active账号时，若schedulable=false且auto_enable_schedulable开启，调用官方POST /accounts/:id/schedulable true并读取确认，再进行模型测试。调度未知不当作参与调度。

- 2.2.0-88api.13：Sub2API账号操作菜单只提供“启用调度/停用调度”，调用官方POST /accounts/:id/schedulable；不再使用bulk-update status。status=active/inactive仅由账号状态操作或服务端返回决定。核对#333、#342时官方列表与详情均为active、schedulable=true、无冷却。

- 2.2.0-88api.14：右侧Sub2API列表使用官方accounts?lite=1读取current_concurrency和concurrency，每5秒同步；current_concurrency是当前请求占用计数，不等同active_sessions（OpenAI通常为空）。额度展示移除5h，仅保留7d。左侧身份标签按Sub2API plan_type：Plus、Pro 20x、Pro 5x、Business Standard、Business Premium、Free、Enterprise、Unknown。

- 2.2.0-88api.15：右侧Sub2API列表原“分组”列改为“账号标签”，标签按OpenAI plan_type定义并优先使用本地匹配账号的Sub2API标签。账号操作菜单统一称“刷新令牌（选中）”；该动作调用Sub2API POST /api/v1/admin/accounts/batch-refresh，刷新远端OAuth令牌，不是刷新列表或额度。

- 2.2.0-88api.16：Sub2API列表账号行支持右键菜单，右键行会自动选中，提供启用调度、停用调度、刷新令牌、删除选中；刷新令牌调用官方batch-refresh接口。
