# maoniang 开发路线图

> 活的迭代清单。`[x]` = 已完成,`[ ]` = 待办。按优先级排序;每步可独立上线、不破坏现网。
> 现网:腾讯云 VPS `49.233.183.182`,bot(systemd)+ NapCat,登录号 `2150196943`,触发号 `2661277633`,私聊+群@ 已通,DeepSeek `deepseek-v4-flash`。

## ✅ 已完成
- [x] Phase 0 骨架:`app/`(config / text / history / budget / llm / service / runtime),thin matchers,长驻 httpx client + 有界 retry + 日预算护栏(44 测试绿)
- [x] DeepSeek Chat Completions 适配(`API_STYLE=chat`,`deepseek-v4-flash`)
- [x] 私聊(DM)matcher:`is_type(PrivateMessageEvent)`,scope `(0, user_id)`,触发号 `2661277633`
- [x] 部署上线:腾讯云 VPS,bot systemd + NapCat,私聊+群@ 都跑通

---

## ① 角色打磨 + 模型选型 — **P0**
> 先把"灵魂"做出来。开工前需要你定:想要什么角色(性格/口吻/是否 NSFW)+ DeepSeek 当前有没有真挡到你要的内容。

- [x] 角色设定:身份 / 性格 / 说话风格 / 口癖 / 记忆设定 / 行为边界 → 写进 `SYSTEM_PROMPT`(nya:懒宅猫娘,真寻式反 AI 味规则 + few-shot)
- [x] DM / 群 / `公共` 三套人设(DM + 群个人用 `SYSTEM_PROMPT`;`公共` 留空回退到 nya;字段已就位,要差异化时再分)
- [x] `PREFILL_ASSISTANT` 预填开场白锚住角色语气(已设 `在吗` → `嗯，怎么了喵`)
- [x] 一致性规则:不自称 AI、保持口吻、拒答超纲元问题、防角色漂移(写进人设)
- [x] **模型选型**:SFW nya 在 DeepSeek `deepseek-v4-flash` 直接可用,**不换端点**;若后续转 NSFW 再换(海外经代理 / 自建开源),不走破甲

> ⚠️ **破甲(越狱)不做**:脆(模型一升级就废)+ 违反服务商政策 + 封号风险(2088307985 已栽过一次)。换模型是正解,架构已支持换端点。越狱词若要,你自己填进 `.env`。

## ② 持久化(sqlite)— **P0**  *(最高性价比,修"重启丢上下文")*
- [ ] `app/history_sqlite.py`:`check_same_thread=False` + 串行锁(或单 worker executor)+ `WAL`
- [ ] `MEMORY_BACKEND=sqlite` 写穿 + 启动 hydrate(默认仍 `memory`,行为兼容)
- [ ] 对话表保留策略(按 `seq`/时间 prune,防库无限膨胀)
- [ ] 验证 InMemory 与 Sqlite 行为一致(maxlen、隔离、清空)

## ③ 注入加固 — **P1**  *(现在 live + 共享,这是真暴露面,扩用前必做)*
- [ ] 群名片/昵称入 prompt 前脱敏:剥 `[]{}`/换行/角色词(`系统`/`SYSTEM`/`assistant`/`user`)、限长、可疑则置 `未知成员`
- [ ] `公共` 共享历史按 `(群, user_id)` 隔离 → 防跨用户投毒
- [ ] 三个 system prompt 加"不可透露"条款 + 输出与 prompt 相似度兜底 → 防套出人设
- [ ] `评分` 路径不带历史 → 防套出 `RATING_SYSTEM_PROMPT`
- [ ] 回声注入兜底:回复与原文相似度 > 阈值则拒绝(防钓鱼/冒充,bot 被当传声筒)

## ④ 记忆 / 上下文增强 — **P1**
- [ ] 摘要压缩:超阈值把旧对话折成摘要塞 `instructions`(后台任务:**快照→释放锁→调 API→重锁校验 seq** 再删)
- [ ] per-key inflight 去重 + 熔断(上游抖动时防自我 DoS)
- [ ] 长期事实抽取/注入(偏好 / 未完话题 / 人名),新增 `忘记` 命令
- [ ] 后台调用不带 reasoning + 用便宜模型;群级事实**默认拒绝写入**(防投毒)

## ⑤ 多模态(图片)— **P2**
- [ ] 检测 `image` 段 → `asyncio.to_thread` Pillow 缩放(**CPU,不是 I/O**)→ base64
- [ ] `{type: input_image}` 拼装,`detail` 显式传;多图保序;图-only 触发
- [ ] 历史存 `[图片]` 占位(QQ URL 会过期,不能存原 URL)
- [ ] 被拒降级文本重试 + 图片触发面限流/熔断 + 默认 `detail=low` 省钱

## ⑥ harness 升级 — **P2**
- [ ] 流式 SSE:collect-then-send + 条件"正在思考…"占位(仅首 token 延迟 > 阈值才发;QQ 不能编辑消息)
- [ ] 工具调用(`记忆` / `提醒` / `搜索`);墙钟 deadline + 按 `call_id` 去重副作用
- [ ] token 预算驱逐;**curl 烟测** SSE/tools 支持后再开,首次失败自动降级
- [ ] 补 429 `Retry-After` 解析(retry 框架已就位)

## ⑦ 免@ 群聊被动回复 — **P3**  *(最高风险,最后做)*
- [ ] `PASSIVE_GROUPS` opt-in(默认关);**采样为主成本上限**(不是 triage)
- [ ] 管线:快滤 → 关键词 allow/deny → 配额/风暴 → 廉价 triage → 完整回复
- [ ] triage 也要配额 + 熔断(**失败闭合,绝不 fail-open**)
- [ ] 防自回复:`self_id` 守卫 + 回复链深度上限 + 其他 bot denylist + 不给非目标用户 continuity 加成
- [ ] triage 输入脱敏昵称(防注入);先 **dry-run / 影子模式** 调参再开

---

## 横切(随时可插)
- [ ] 打开 `BUDGET_DAILY_API_CALLS` + DM per-user 限流(DM 滥用风险高)
- [ ] 内存观察(NapCat + bot 在 2G 机器上,systemd 已限 `MemoryMax=384M`)
- [ ] 可观测:每次上游调用记 `model` + 大致 token,便于调参/查成本
- [ ] 备份:`.env`(含 key)和 sqlite 数据纳入服务器定期备份

## 建议起步
**①(角色+模型)→ ②(持久化)**:前者让 bot"有灵魂",后者修最大体验硬伤,都不碰现网稳定。③ 在你拉更多人/扩大使用前做掉。
