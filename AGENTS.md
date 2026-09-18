# OpenAI Token Manager fork

- 上游：xiaopenghuang/Token-Manager；fork：blackdm666/Token-Manager。
- 本地独立仓库；维护分支codex/functional-improvements，origin为fork，upstream为上游。
- 保留原项目名称、LICENSE和作者署名。运行数据tokens/、outputs/、token_manager_config.json和构建dist/均不入Git。
- Sub2API业务配置使用官方管理API，服务器规则仍遵守工作区AGENTS。生产只读验收可使用sub2api-maintenance/AGENTS.md中的凭据读取方式，禁止复制真实Key或账号token到源码、测试、日志、报告或打包产物。
- 上传配置：并发、优先级、倍率、proxy_id、group_ids、extra.codex_fingerprint_mode。以Sub2API运行版本API契约为准，不自行编造字段。
- 账号自动恢复必须显式开启，限制到已导入本地且唯一匹配的OpenAI OAuth账号。普通429、主动停用、临时停调度不自动恢复。永久撤销需重新授权，不尝试绕过登录或封禁。
- 恢复使用apply-oauth-credentials官方入口更新原账号，不新建/删除账号，不覆盖原分组/并发/代理/指纹。必须检查工作区身份、并发凭据变化、读回结果；本地刷新结果先落盘，网络失败重试上传，不反复旋转refresh token。
- 回归命令：`python -m unittest discover -s tests -v`、`python -m compileall -q token_manager tests`、`git diff --check`。GUI测试使用临时目录和合成账号，禁止载入生产凭据。
- 打包：`python build.py --name OpenAI-Token-Manager-88API --entry-point main.py`，无需`--clean`；不删除用户已有dist内运行数据。
- GitHub发布、全量生产自动恢复需依据当次用户授权，不因为凭据可用而自行启用。
