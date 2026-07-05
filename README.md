# Journal Finder — 学术期刊推荐工具

一个 [Claude Code](https://claude.ai/code) Skill，帮助人文社科研究者根据论文内容匹配合适的投稿期刊。

当前覆盖学科：**全 SSCI/AHCI 人文社科**（经济学、社会学、政治学、心理学、教育学、法学、传播学、人文地理、管理学、人口学、人类学、语言学等，共 **7289 个期刊**）

## 功能

- 根据论文标题、摘要或关键词，从 2000+ 期刊中智能匹配推荐
- 展示多维信息：JCR 分区、中科院分区、影响因子、审稿周期、国人占比、APC 费用等
- 支持交叉学科匹配（如"人口老龄化 + 劳动经济学"）
- 支持偏好筛选：审稿速度优先 / 期刊声望优先 / 预算有限 / 国人友好

## 快速使用

### 前置条件

- [Claude Code](https://claude.ai/code)（CLI 或桌面版）
- Python 3.10+

### 安装

```bash
# 1. 克隆仓库
git clone https://github.com/yennhi85219-glitch/journal-finder.git ~/journal-finder

# 2. 安装 Python 依赖
cd ~/journal-finder
pip install -r requirements.txt

# 3. 部署 Skill 到 Claude Code
cp -r skill ~/.claude/skills/find-journal
```

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
│   ├── fetch_journals.py       # 从 OpenAlex 拉取期刊元数据
│   ├── fetch_metrics.py        # 计算国人占比、年发文量
│   ├── fetch_review_times.py   # 从 Crossref 提取审稿时间线
│   ├── import_excel_data.py    # 导入 JCR/中科院分区 Excel 数据
│   └── build_database.py       # 合并所有数据源 → 最终数据库
├── data/
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
| 期刊基本信息、Topics | [OpenAlex](https://openalex.org/) (免费 API) | `fetch_journals.py` |
| 国人占比、年发文量 | OpenAlex Works API | `fetch_metrics.py` |
| 审稿周期（received→accepted） | [Crossref](https://www.crossref.org/) API | `fetch_review_times.py` |
| JCR 分区、影响因子 | JCR 2026 (Excel 导入) | `import_excel_data.py` |
| 中科院分区 | 2025 最终版 (Excel 导入) | `import_excel_data.py` |

## 更新数据

克隆后数据库已可直接使用。如需更新数据：

```bash
# 重新拉取 OpenAlex 数据（需联网，约数小时）
python3 scripts/fetch_journals.py
python3 scripts/fetch_metrics.py
python3 scripts/fetch_review_times.py

# 重建最终数据库
python3 scripts/build_database.py
```

## 每个期刊包含的信息

- 期刊名称 / 缩写 / 出版商
- JCR 分区 (Q1-Q4) + 中科院分区 (1-4区)
- 影响因子 (JIF 2026)
- 是否 OA / APC 费用 / 是否有 Waiver
- 国人占比（近 2 年中国机构作者文章比例）
- 年发文量
- 审稿周期中位数（天）
- 字数限制 / 审稿形式（单盲/双盲）
- 避坑标签

## 路线图

- [ ] 扩展到全 SSCI（政治学、社会学、心理学、教育学等）
- [ ] 偏好多选 + 分层细化（预算区间、速度区间等）
- [ ] 支持整篇文章输入（PDF/Word），自动分析方法论和字数
- [ ] 接入 LetPub 拒稿数据
- [ ] 用户反馈机制（UGC 审稿体验）

## 许可

MIT
