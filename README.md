# 数据 Agent 架构审查与最小实现

这个仓库从收到的一份架构推演文档出发，提供审查结论、修订后的技术文章，以及一条使用**虚构数据**的可运行链路。原工作目录没有可供审查的应用代码，因此这里的程序是新建的参考实现，不代表原方案已经在生产环境落地。

## 阅读顺序

1. [架构审查](docs/architecture_review.md)：哪些主张成立，哪些要收紧或补证据。
2. [从零理解数据 Agent](docs/article.md)：面试可用的完整技术文章。
3. [最小实现](demo/data_agent.py)：认证指标、市场权限、日批完成检查、变化分解与证据链。
4. [个人证据填写表](docs/interview_evidence_template.md)：把岗位职责与真实经历逐项对齐。
5. [超市 Data Agent 产品手册](docs/product_manual.md)：按版本追加产品、架构与思路变化。
6. [补货关注清单方法](docs/replenishment_method.md)：首版销售驱动补货口径与限制。

## 超市试点数据底座

本机试点使用独立 PostgreSQL 数据库 `supermarket_agent`。数据库结构位于 `db/schema.sql`，POS 导出导入器位于 `scripts/import_pos_exports.py`。首版只读取本地导出副本，不连接或写回 POS。

首次初始化与导入：

```bash
createdb supermarket_agent
psql -X -v ON_ERROR_STOP=1 -d supermarket_agent -f db/schema.sql
python3 scripts/import_pos_exports.py \
  --database supermarket_agent \
  --sales data/raw/sales_detail_2026-06-29_2026-09-20.csv \
  --inventory data/raw/inventory_all_normal_2026-09-20.csv \
  --snapshot-date 2026-09-20
```

查看每日销售与商品汇总：

```bash
psql -d supermarket_agent -c "SELECT * FROM analytics.daily_sales ORDER BY sales_date DESC;"
psql -d supermarket_agent -c "SELECT * FROM analytics.product_sales ORDER BY sales_amount DESC LIMIT 20;"
psql -d supermarket_agent -c "SELECT * FROM analytics.replenishment_attention WHERE supply_mode <> 'manual_review' ORDER BY sales_amount_28d DESC LIMIT 20;"
```

## 中文经营问答

首版使用固定指标和固定 SQL，不调用外部模型：

```bash
python3 app/sales_agent.py "今天、昨天和本周销售额是多少？"
python3 app/sales_agent.py "最近哪些商品卖得最好？"
python3 app/sales_agent.py "哪些商品销量明显上升或下降？"
python3 app/sales_agent.py "哪些类别贡献最多？"
python3 app/sales_agent.py "客单价和订单量如何变化？"
```

每个回答都会显示数据覆盖和截止时间。最新数据日期按可能未完整处理，趋势使用前一日作为完整数据截止日。

运行首批经营问答 Golden Set：

```bash
# 只检查 30 个问题的意图路由，不连接数据库
python3 scripts/evaluate_sales_agent.py

# 额外连接本机 PostgreSQL，核对 5 个基准问题的真实答案
python3 scripts/evaluate_sales_agent.py --live
```

Golden Set 位于 `evals/sales_agent_golden_set.json`。当导入了更新的 POS 数据后，真实答案快照会主动失败，提醒先核对新数据再更新基准值。

## 本机网页聊天

启动网页：

```bash
python3 app/web_server.py
```

然后在浏览器打开：

```text
http://127.0.0.1:8765
```

不要把 `web/index.html` 当作普通文件直接打开。页面检测到 `file://` 地址时会自动跳转到默认本机服务地址；如果使用了自定义端口，请手动打开启动命令显示的地址。

网页当前使用“LLM 规划 + 本机校验 + 只读工具执行”链路：LLM 只选择白名单工具和填写参数，固定程序校验后才查询本机 PostgreSQL，最终经营数字由固定模板输出。服务默认只监听本机地址，不对局域网或互联网开放；关闭运行它的终端或按 `Control+C` 即可停止。

网页模式由 `.env` 中的 `AI_WEB_MODE` 控制：

```text
AI_WEB_MODE=validated_llm
```

经营问题、数据日期边界以及多轮所需的上一轮问题和语义计划会发送给已配置的 OpenAI API；销售明细、数据库连接信息、工具查询结果和最终经营数字不会发送给模型。网页会在每条回答下显示 LLM 选择的工具、“参数已校验”和是否使用上一轮上下文。

## 经营分析 Skill

经营分析结果在网页中以图文报告展示：核心指标卡、类别与商品增减条形图、可展开的完整明细表及文字证据。仅经营分析 Skill 返回图文报告，普通问数保留文字。刷新页面后需重新生成报告，暂不支持独立归档与导出。

网页可问“最近一周生意怎么样？帮我分析变化体现在哪些类别和商品”，或点击“经营分析报告”。LLM 选择项目内的 `skills/business-review/SKILL.md` 对应能力和日期；本机执行三项只读汇总，核对全店差额与类别、商品差额，再生成证据报告。支持当前1至31个完整日与此前等长期间；只分析全店，不支持指定商品/类别的完整经营报告。普通问数继续使用 NL2Metrics。

这是固定流程 Skill，尚未加入让 LLM 根据查询结果自主追加分析的循环；汇总结果仍保留在本机。修改 SKILL.md 的文字不会自动改变 Python 的计算逻辑，方法变更必须同步更新执行器和测试。

运行规划回归：`python3 scripts/evaluate_ai_shadow.py --golden-set evals/business_review_golden_set.json`。

## 多轮上下文

网页会为当前浏览器标签页维护一个随机会话编号。服务端只在内存中保存上一轮语义计划、澄清问题和必要的商品候选，不保存数据库查询结果；状态最多保留1小时，服务重启后清空。

可以连续测试：

```text
牛奶最近7天销售额是多少？
第一个
```

或者：

```text
鸡蛋最近30天卖得怎么样？
改成最近7天每天看销量
再和此前7天对比
```

点击“清空对话”会生成新的会话编号，后续问题不再继承此前计划。

## NL2Metrics 通用查询

网页已经支持第一版 NL2Metrics。模型不生成 SQL，而是生成受控语义计划：指标、单一维度、商品或类别筛选、日期范围、前期对比、排序和数量限制。本机程序解析商品名称、校验计划并编译参数化 SQL。

可以测试：

```text
鸡蛋最近30天卖得怎么样？
鸡蛋最近7天每天销量是多少？
鸡蛋最近7天和此前7天的销售额对比
牛奶最近7天销售额是多少？
```

“牛奶”会因为匹配多个具体商品而要求澄清。当前指标和维度定义见 [NL2Metrics 指标语义目录](docs/metric_catalog.md)。运行专用规划评估：

```bash
python3 scripts/evaluate_ai_shadow.py \
  --golden-set evals/nl2metrics_golden_set.json
```

## 参数化经营分析工具

五类经营分析已统一为带严格参数的只读工具。查看工具定义：

```bash
python3 scripts/run_sales_tool.py --list
```

例如，查询 2026-09-13 至 2026-09-19 按销量排序的前 5 个商品：

```bash
python3 scripts/run_sales_tool.py query_top_products \
  --arguments '{"start_date":"2026-09-13","end_date":"2026-09-19","limit":5,"sort_by":"sales_quantity","category":null}'
```

这些工具是下一阶段 AI Function Calling 的边界。AI 只能选择白名单工具并提供经过校验的参数，不能提交任意 SQL。

## AI 工具调用影子模式

影子模式只让 AI 选择工具和参数，不执行 AI 选择的工具，也不改变网页当前答案。检查本机是否已配置：

```bash
python3 -m pip install -r requirements.txt
python3 scripts/run_ai_shadow.py --check
```

进行第一次真实影子测试前，把本机配置写入项目根目录的 `.env`（该文件已被 Git 忽略）：

```bash
OPENAI_API_KEY="你的本机API密钥"
OPENAI_MODEL="你选择的模型ID"
SUPERMARKET_DATABASE="supermarket_agent"
AI_SHADOW_MODE="true"
```

然后运行：

```bash
python3 scripts/run_ai_shadow.py "最近30天按销量排前5名商品"
```

运行首批 10 个真实 AI 影子评估案例：

```bash
python3 scripts/evaluate_ai_shadow.py
```

评估器严格核对决策、工具和参数，但不会执行模型选择的数据库工具。也可以使用 `--case explicit_top_products` 只运行一个案例，使用 `--repeat 3` 检测重复调用是否漂移，或使用 `--json` 查看完整结果。

不要把真实密钥写入代码、`.env.example`、聊天记录或 Git。请求使用 OpenAI Responses API、严格工具结构和 `store: false`；影子阶段发送用户问题、数据日期边界和工具定义，不发送销售明细或执行结果。

## 运行

```bash
python3 demo/data_agent.py
python3 -m unittest discover -s tests -v
```

演示程序的样例数据由程序内置生成，不连接任何真实业务系统，也不发送消息；AI 影子模式额外使用 `certifi` 校验 HTTPS 证书，并在显式运行时调用外部模型。示例中的 GMV 定义是为演示而设，**不是阿里国际的真实指标口径**。

## 能力边界

最小实现只覆盖一条查询与分析链路。文中提及的 MCP、LLM、时序异常检测、报告审批、IM 触达、评测平台和数据驻留策略属于目标架构，需要在实际组织的数据与权限环境中单独设计、实现和验证。
