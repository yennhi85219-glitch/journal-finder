# Journal Finder — 学术期刊推荐工具

一个 [Claude Code](https://claude.ai/code) Skill，帮助人文社科研究者根据论文内容匹配合适的投稿期刊。

当前 canonical 数据库包含 **5,183 本可由 OpenAlex 解析的当前 JCR 白名单期刊**：

- 4,802 本 SSCI/AHCI 期刊
- 381 本环境健康交叉期刊

旧版 economics/demography topic 库不再默认混入 canonical 数据库。

## 功能

- 根据论文标题、摘要或关键词，从 5,183 本 canonical 期刊中进行关键词 + SPECTER2/FAISS 混合匹配
- 地区、方法和样本人群作为上下文降权，主题贴合度先过门槛，再按声望、速度、费用等偏好排序
- 默认排除可明确识别的综述/约稿型期刊，避免把非普通投稿入口当作候选
- 展示多维信息：JCR 分区、中科院分区、影响因子、审稿周期、国人占比、APC 费用等
- 支持交叉学科匹配（如"人口老龄化 + 劳动经济学"）
- 支持偏好筛选：审稿速度优先 / 期刊声望优先 / 预算有限 / 国人友好

## 快速使用

### 前置条件

- [Claude Code](https://claude.ai/code)（CLI 或桌面版）
- Python 3.10+

### 安装

```bash
# 1. 克隆到任意目录
git clone https://github.com/yennhi85219-glitch/journal-finder.git
cd journal-finder

# 2. 安装 Python 依赖
python -m pip install -r requirements.txt

# 3. 构建本地语义索引（推荐；首次运行会下载约 440MB 的 SPECTER2）
python scripts/build_embeddings.py

# 4. 安装 Skill，并记录当前仓库的实际数据路径
python scripts/install_skill.py
```

不构建语义索引也能使用关键词推荐，但跨学科召回质量会下降。安装脚本会运行
健康检查；需要完整验证语义能力时运行：

```bash
python ~/.claude/skills/find-journal/scripts/doctor.py --strict
```

项目可以放在任意目录。运行时的数据目录优先级为：

1. 命令行 `--data-dir`
2. 环境变量 `JOURNAL_FINDER_DATA_DIR`
3. 安装脚本写入的 `journal-finder-config.json`
4. 源码仓库相对路径自动发现
5. 兼容旧版的 `~/journal-finder/data`

移动仓库后，重新运行 `python scripts/install_skill.py` 即可更新安装配置。

### 使用

在 Claude Code 中直接调用：

```
/find-journal 我的论文研究最低工资对青年就业的影响，使用了DID方法，数据来自中国
```

或者：

```
/find-journal I have a paper about population aging effects on labor supply, using panel data from OECD countries
```

## 项目结构

```
journal-finder/
├── scripts/                    # 数据管道脚本
│   ├── fetch_ssci_journals.py  # 从当前 JCR 白名单拉取 OpenAlex 元数据
│   ├── fetch_metrics.py        # 计算国人占比、年发文量
│   ├── fetch_review_times.py   # 从 Crossref 提取审稿时间线
│   ├── import_excel_data.py    # 导入 JCR/中科院分区 Excel 数据
│   ├── build_database.py       # 合并 canonical 数据源 → 最终数据库
│   └── build_embeddings.py     # 生成 SPECTER2 向量与 FAISS 索引
├── data/
│   ├── journals_ssci.json      # canonical 主数据库
│   ├── journals_economics.json # 经济学期刊数据库（可直接使用）
│   ├── journals_demography.json# 人口学期刊数据库（可直接使用）
│   └── manual_supplement.json  # 手动维护数据（JCR/中科院分区、APC等）
├── skill/                      # Claude Code Skill 定义
│   ├── SKILL.md                # Skill 主文件
│   ├── references/             # 数据 schema 文档
│   └── scripts/query_db.py     # 数据库查询脚本
├── CLAUDE.md                   # Claude Code 开发上下文
├── requirements.txt
└── .gitignore
```

## 数据来源

| 数据 | 来源 | 更新方式 |
|------|------|---------|
| 期刊基本信息、Topics | [OpenAlex](https://openalex.org/) (免费 API) | `fetch_ssci_journals.py` |
| 国人占比、年发文量 | OpenAlex Works API | `fetch_metrics.py` |
| 审稿周期（received→accepted） | [Crossref](https://www.crossref.org/) API | `fetch_review_times.py` |
| JCR 分区、影响因子 | JCR 2026 (Excel 导入) | `import_excel_data.py` |
| 中科院分区 | 2025 最终版 (Excel 导入) | `import_excel_data.py` |

## 更新数据

克隆后关键词数据库即可使用；语义索引需要本地构建。如需更新数据：

```bash
# 刷新当前 JCR 白名单与 OpenAlex 元数据
python scripts/fetch_ssci_journals.py --jcr-file /path/to/jcr.xlsx
python scripts/fetch_metrics.py
python scripts/fetch_review_times.py

# 导入本地 JCR/CAS 数据并重建 canonical 数据库
python scripts/import_excel_data.py \
  --jcr-file /path/to/jcr.xlsx \
  --cas-file /path/to/cas.xlsx
python scripts/build_database.py
python scripts/build_embeddings.py

# 运行快速单元/质量回归测试
python -m pytest -q

# 可选：运行真实数据 + SPECTER2 Top-K 基准
python scripts/evaluate_recommendations.py
```

`build_database.py` 默认只使用 `sources_ssci_all.json`。只有明确需要诊断旧数据时才使用
`--include-legacy`；legacy 条目会标成 `legacy_economics` / `legacy_demography`，不会冒充 SSCI/AHCI。

## 每个期刊包含的信息

- 期刊名称 / 缩写 / 出版商
- JCR 分区 (Q1-Q4) + 中科院分区 (1-4区)
- 影响因子 (JIF 2026)
- 是否 OA / APC 费用 / 是否有 Waiver
- 国人占比（近 2 年中国机构作者文章比例）
- 年发文量
- 审稿周期中位数（天）
- 字数限制 / 审稿形式（仅在有来源证据时提供）
- 避坑标签

缺失字段统一为 `null`，推荐结果显示为“—”。当前 Aims & Scope、APC、审稿周期等字段仍不完整，工具不会对缺失值进行猜测。

## 常见问题

### 找不到数据库

先运行：

```bash
python ~/.claude/skills/find-journal/scripts/doctor.py
```

如果仓库移动过，回到仓库重新执行 `python scripts/install_skill.py`。也可以临时指定：

```bash
export JOURNAL_FINDER_DATA_DIR="/path/to/journal-finder/data"
```

### 只使用了关键词搜索

查询结果中的 `query.semantic_status` 为 `fallback` 时，查看
`query.semantic_error`。通常需要在仓库中运行 `python scripts/build_embeddings.py`，
再用 `doctor.py --strict` 验证。

## 路线图

- [x] 当前 JCR SSCI/AHCI 白名单 + 环境健康交叉子集
- [x] 多目标偏好排序与统一硬筛选
- [x] 建立首批 8 个代表性论文 Top-K 推荐回归基准
- [ ] 将真实推荐基准扩展到 30–50 篇论文
- [ ] 支持整篇文章输入（PDF/Word），自动分析方法论和字数
- [ ] 接入 LetPub 拒稿数据
- [ ] 用户反馈机制（UGC 审稿体验）

## 许可

MIT
