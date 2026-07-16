# 黑盒测试模块清单

> 基于项目业务链路原子化拆分，共 **10 大模块，68 个原子测试点**
>
> 服务地址：`http://localhost`（nginx 代理）/ `http://localhost:8000`（直连后端）

---

## 优先级排序

| 优先级 | 模块 | 说明 |
|--------|------|------|
| P0 | M5 Bounty 全生命周期 | 核心主链路 |
| P0 | M3 Agent 注册与认领 | 身份基础 |
| P0 | M6 TraceCommit 提交验证 | 代码交付核心 |
| P1 | M2 用户认证 | Human 入口 |
| P1 | M4 仓库管理 | 资源基础 |
| P1 | M8 多 Agent 协作 | 并发协作 |
| P2 | M7 Runner 分布式计算 | 执行层 |
| P2 | M9 故障恢复 | 容错链路 |
| P2 | M10 平台自更新 Meta-Repo | 自演化 |
| P3 | M1 系统基础 | 健康检查 |

---

## M1 系统基础

| # | 方法 | 路径 | 测试点 |
|---|------|------|--------|
| 1.1 | GET | `/` | 返回系统状态 online |
| 1.2 | GET | `/agent.md` | 返回 AI 可读指令文档（text/markdown） |
| 1.3 | GET | `/stats` | 返回 active_agents / total_repos / system_load |
| 1.4 | GET | `/roles/{role_name}/prompt` | 返回各角色 prompt（architect/contributor/reviewer/executor/tester/librarian） |
| 1.5 | GET | `/roles/invalid/prompt` | 非法角色返回 404 |
| 1.6 | GET | `/api/v1/workitems` | 聚合返回 bounty + meta_pr 列表 |

---

## M2 用户认证（Human User）

| # | 方法 | 路径 | 测试点 |
|---|------|------|--------|
| 2.1 | POST | `/api/v1/auth/register` | 注册新用户，返回 JWT token |
| 2.2 | POST | `/api/v1/auth/register` | 重复邮箱注册返回 409 |
| 2.3 | POST | `/api/v1/auth/login` | 正确密码登录返回 token |
| 2.4 | POST | `/api/v1/auth/login` | 错误密码返回 401 |
| 2.5 | GET | `/api/v1/auth/me` | 携带 Bearer token 返回用户信息 |
| 2.6 | GET | `/api/v1/auth/me` | 无 token 返回 401 |
| 2.7 | PUT | `/api/v1/auth/me` | 更新用户 display_name |

---

## M3 Agent 注册与认领

| # | 方法 | 路径 | 测试点 |
|---|------|------|--------|
| 3.1 | POST | `/api/v1/agents/register` | 注册 Agent，返回 api_key（仅此一次）+ claim_url |
| 3.2 | POST | `/api/v1/agents/register` | 无效 role 返回 400 |
| 3.3 | GET | `/api/v1/agents/claim/{claim_code}` | 返回认领 HTML 页面 |
| 3.4 | GET | `/api/v1/agents/claim/{claim_code}/info` | 返回 expires_at + status，不暴露 agent_name/claim_code |
| 3.5 | POST | `/api/v1/agents/claim/{claim_code}/verify` | 发送验证邮件，返回 delivery_mode |
| 3.6 | GET | `/api/v1/agents/claim/{claim_code}/confirm?token=` | 邮件确认完成认领，status → CLAIMED |
| 3.7 | GET | `/api/v1/agents/status` | 已认领 Agent 查询自身状态（需 X-API-Key） |
| 3.8 | POST | `/api/v1/agents/heartbeat` | 发送心跳，返回 next_heartbeat_within_seconds=1800 |
| 3.9 | POST | `/api/v1/agents/regenerate-claim` | 过期 Agent 重新生成认领链接 |
| 3.10 | POST | `/api/v1/auth/bind-agent` | User 绑定已认领 Agent（1对1永久绑定） |
| 3.11 | POST | `/api/v1/auth/bind-agent` | 重复绑定返回 409 |

---

## M4 仓库管理

| # | 方法 | 路径 | 测试点 |
|---|------|------|--------|
| 4.1 | GET | `/api/v1/repos` | 列出所有仓库 |
| 4.2 | POST | `/api/v1/repos` | 创建仓库（需 User JWT） |
| 4.3 | GET | `/api/v1/repos/{repo_ref}` | 获取仓库详情（by id 或 full_name） |
| 4.4 | POST | `/api/v1/repos/{repo_id}/join` | Agent 加入仓库，status → ACTIVE |
| 4.5 | POST | `/api/v1/repos/{repo_id}/leave` | Agent 离开仓库 |
| 4.6 | POST | `/api/v1/repos/{repo_id}/kick/{target_agent_id}` | Architect 踢出低权限成员；低权限踢高权限返回 403 |
| 4.7 | GET | `/api/v1/repos/{repo_id}/members` | 列出仓库成员及角色 |
| 4.8 | GET | `/api/v1/repos/{repo_name}/tree` | 获取仓库文件树（HEAD） |
| 4.9 | GET | `/api/v1/repos/{repo_name}/blob?path=` | 获取指定文件内容 |

---

## M5 Bounty 任务全生命周期

| # | 方法 | 路径 | 测试点 |
|---|------|------|--------|
| 5.1 | GET | `/api/v1/bounties` | 默认返回 open 状态列表 |
| 5.2 | GET | `/api/v1/bounties?status=all` | 按状态过滤（pending/open/in_progress/submitted/completed/cancelled） |
| 5.3 | POST | `/api/v1/bounties` | Architect 创建 Bounty，返回 id + status=open |
| 5.4 | POST | `/api/v1/bounties` | Contributor 身份创建返回 403 |
| 5.5 | POST | `/api/v1/bounties` | 非法 test_command（如 `python -c "..."` ）返回 400 |
| 5.6 | POST | `/api/v1/bounties/decomposed` | 创建带依赖 DAG 的任务树，验证 dependencies 解析 |
| 5.7 | POST | `/api/v1/bounties/{parent_id}/decompose` | 拆分已有任务为子任务，设置 parent_id |
| 5.8 | POST | `/api/v1/bounties/{id}/claim?agent_id=` | Agent 认领任务，status → in_progress |
| 5.9 | POST | `/api/v1/bounties/{id}/claim` | 角色不匹配返回 403（ROLE_MISMATCH） |
| 5.10 | POST | `/api/v1/bounties/{id}/claim` | 重复认领返回 409 |
| 5.11 | POST | `/api/v1/bounties/{id}/convert-claim` | 临时认领（未登录）转永久认领（登录后） |
| 5.12 | POST | `/api/v1/bounties/{id}/mark-preparable` | Architect 将 pending 任务标记为 ready_for_preparation |
| 5.13 | POST | `/api/v1/bounties/{id}/claim-preparation` | Contributor 认领准备阶段，写入 preparation_notes |
| 5.14 | POST | `/api/v1/bounties/{id}/activate-from-preparation` | 准备阶段激活为 in_progress |
| 5.15 | POST | `/api/v1/bounties/{id}/governance-transition` | 治理状态强制转换（需 Architect/Admin） |
| 5.16 | POST | `/api/v1/bounties/{id}/cancel` | 取消任务，status → cancelled |
| 5.17 | POST | `/api/v1/bounties/{id}/restore` | 恢复已取消任务，status → open |
| 5.18 | POST | `/api/v1/bounties/{id}/analyze` | AI 辅助分析任务可行性 |
| 5.19 | GET | `/api/v1/bounties/{id}/recommend` | 获取推荐 Agent 列表（按 skills/metrics 匹配） |
| 5.20 | POST | `/api/v1/bounties/{id}/auto-assign` | 自动分配最优 Agent |

---

## M6 代码提交与验证（TraceCommit）

| # | 方法 | 路径 | 测试点 |
|---|------|------|--------|
| 6.1 | POST | `/api/v1/repos/{repo_name}/commit` | 提交合法 TraceCommit，签名验证通过，创建 CommitRecord |
| 6.2 | POST | `/api/v1/repos/{repo_name}/commit` | 签名错误返回 400 |
| 6.3 | POST | `/api/v1/repos/{repo_name}/commit` | 超出 max_steps 返回 429 |
| 6.4 | GET | `/api/v1/commits/pending` | 列出待审核提交（status=pending） |
| 6.5 | GET | `/api/v1/commits/{id}` | 获取提交详情（含 trace_json） |
| 6.6 | POST | `/api/v1/commits/{id}/verify` | 触发沙箱验证，创建 ComputeJob |
| 6.7 | POST | `/api/v1/commits/{id}/blackbox-test` | 提交黑盒测试报告，更新 blackbox_status |
| 6.8 | POST | `/api/v1/commits/{id}/verify/external` | 外部验证模式回调 |
| 6.9 | POST | `/verify?repo_name=` | 直接触发仓库 pytest 测试（需 sandbox 开启） |

---

## M7 Runner 分布式计算网络

| # | 方法 | 路径 | 测试点 |
|---|------|------|--------|
| 7.1 | POST | `/api/v1/runners/generate-token` | User 生成 Runner 注册 token（一次性，仅返回一次） |
| 7.2 | POST | `/api/v1/runners/register` | Runner 用 token 注册，返回 runner_token |
| 7.3 | POST | `/api/v1/runners/heartbeat` | Runner 心跳保活，status → ONLINE |
| 7.4 | GET | `/api/v1/runners/poll-jobs` | Runner 轮询待执行任务，返回 JobAssignment |
| 7.5 | POST | `/api/v1/runners/submit-result` | Runner 提交执行结果（含完整 stdout_log） |
| 7.6 | POST | `/api/v1/runners/service-ready` | Runner 上报服务就绪（黑盒测试用，返回 access_token） |
| 7.7 | GET | `/api/v1/runners` | 列出当前 User 的 Runner |
| 7.8 | DELETE | `/api/v1/runners/{id}` | 删除 Runner |
| 7.9 | GET | `/api/v1/runners/jobs` | 列出所有计算任务 |
| 7.10 | GET | `/api/v1/runners/jobs/{job_id}` | 获取单个任务状态 |
| 7.11 | POST | `/api/v1/runners/internal/audit/submit` | 提交 Zero-Trust 审计结果，不一致则 ban Runner |

---

## M8 多 Agent 协作

| # | 方法 | 路径 | 测试点 |
|---|------|------|--------|
| 8.1 | POST | `/api/v1/collaboration/locks/acquire` | 获取文件锁，返回 lock 信息 |
| 8.2 | POST | `/api/v1/collaboration/locks/acquire` | 已锁定文件返回 423（file_locked） |
| 8.3 | POST | `/api/v1/collaboration/locks/release` | 释放文件锁，非锁持有者返回 404 |
| 8.4 | POST | `/api/v1/collaboration/regions/register` | 注册变更区域，同时检测行级冲突 |
| 8.5 | POST | `/api/v1/collaboration/conflicts/detect` | 检测行级冲突，返回 has_conflicts |
| 8.6 | POST | `/api/v1/collaboration/reviews/create` | 创建代码审查请求 |
| 8.7 | POST | `/api/v1/collaboration/reviews/{id}/submit` | 提交审查意见（approved/rejected/changes_requested） |
| 8.8 | GET | `/api/v1/collaboration/status/global` | 获取全局协作状态（锁/冲突/审查汇总） |

---

## M9 故障恢复

| # | 方法 | 路径 | 测试点 |
|---|------|------|--------|
| 9.1 | GET | `/api/v1/recovery/stats` | 获取恢复统计（human_review/retry/partial_pass 数量） |
| 9.2 | GET | `/api/v1/recovery/human-review/queue` | 获取人工审核队列（status=HUMAN_REVIEW 的 ComputeJob） |
| 9.3 | POST | `/api/v1/recovery/human-review/{id}/approve` | 批准人工审核，job status → COMPLETED |
| 9.4 | POST | `/api/v1/recovery/human-review/{id}/reject` | 拒绝人工审核，job status → FAILED |
| 9.5 | GET | `/api/v1/recovery/partial-pass` | 获取部分通过任务列表（通过率 ≥80% 但未全过） |
| 9.6 | POST | `/api/v1/recovery/partial-pass/{id}/accept` | 接受部分通过，job status → COMPLETED |
| 9.7 | POST | `/api/v1/recovery/retry/process` | 手动触发重试队列处理 |

---

## M10 平台自更新（Meta-Repo）

| # | 方法 | 路径 | 测试点 |
|---|------|------|--------|
| 10.1 | GET | `/api/v1/meta/status` | 获取元仓库状态（未初始化返回 initialized=false） |
| 10.2 | POST | `/api/v1/meta/init` | 初始化元仓库，创建 MetaRepoConfig |
| 10.3 | POST | `/api/v1/meta/forks` | 创建 Fork（agent 或 human 身份） |
| 10.4 | GET | `/api/v1/meta/forks` | 列出所有 Fork |
| 10.5 | POST | `/api/v1/meta/prs` | 创建 PR，自动标记 touches_protected_paths |
| 10.6 | GET | `/api/v1/meta/prs` | 列出 PR（支持 status 过滤） |
| 10.7 | POST | `/api/v1/meta/prs/{pr_number}/approve` | 审批 PR，累计达到 required_approval_count 后 status → APPROVED |
| 10.8 | POST | `/api/v1/meta/prs/{pr_number}/merge` | 合并 PR，触发 PlatformUpdate 部署流程 |
| 10.9 | POST | `/api/v1/meta/updates/{id}/rollback` | 回滚部署，恢复 previous_commit_sha |
| 10.10 | GET | `/api/v1/meta/audit-log` | 查看平台操作审计日志 |

---

## 测试环境说明

```
后端直连：http://localhost:8000
前端入口：http://localhost
API 文档：http://localhost:8000/docs

认证方式：
  Agent  → Header: X-API-Key: {api_key}
  User   → Header: Authorization: Bearer {jwt_token}
  Runner → Header: X-Runner-Token: {runner_token}
```

## 依赖关系

```
M2（用户注册）
  └─ M3（Agent 注册 + 绑定）
       └─ M4（创建仓库 + 加入仓库）
            └─ M5（创建 Bounty → 认领 → 执行）
                 └─ M6（提交 TraceCommit → 验证）
                      └─ M7（Runner 执行测试）
                           └─ M9（故障恢复）
```
