# Journal Database Schema

## 数据文件位置

- `~/journal-finder/data/journals_economics.json` — 经济学期刊
- `~/journal-finder/data/journals_demography.json` — 人口学期刊
- `~/journal-finder/data/manual_supplement.json` — 手动维护数据（JCR/中科院分区等）

## 单条记录 Schema

```json
{
  "issn_l": "string — ISSN-L 主标识",
  "name": "string — 期刊全称",
  "abbreviation": "string|null — 缩写",
  "publisher": "string — 出版商",
  "country_code": "string — 出版国 (ISO 2-letter)",
  "openalex_id": "string — OpenAlex source ID",
  "homepage_url": "string|null — 期刊主页",

  "topics": [
    {
      "name": "string — topic 名称",
      "score": "number — 该 topic 下的文章数",
      "subfield": "string — 所属子领域"
    }
  ],
  "scope_keywords": ["string — 范围关键词（小写）"],

  "jcr_quartile": "string|null — Q1/Q2/Q3/Q4 (手动维护)",
  "cas_zone": "number|null — 中科院分区 1-4 (手动维护)",
  "impact_factor": "number|null — 最新影响因子 (手动维护)",
  "citedness_2yr": "number — OpenAlex 2年平均被引 (IF近似值)",
  "h_index": "number — H指数",

  "is_oa": "boolean — 是否开放获取",
  "oa_type": "string — subscription/hybrid/gold/diamond",
  "apc_usd": "number|null — 版面费(美元)",
  "apc_waiver": "boolean|null — 是否有减免政策",

  "cn_author_ratio": "number|null — 国人占比(0-1), 近2年中国机构作者文章比例",
  "annual_volume_2024": "number|null — 2024年发文量",
  "annual_volume_2023": "number|null — 2023年发文量",

  "review_median_days": "number|null — 投稿到接收中位数(天)",
  "review_samples": "number — 计算样本量",
  "review_coverage": "number — 有日期数据的文章比例(0-1)",
  "accept_to_online_days": "number|null — 接收到上线中位数(天)",

  "word_limit_min": "number|null — 最低字数",
  "word_limit_max": "number|null — 最高字数",
  "review_type": "string|null — single_blind/double_blind",

  "warning_tags": ["string — 避坑标签，如'压稿严重','国人占比高','审稿快'"],
  "notes": "string — 备注",

  "_meta": {
    "last_api_update": "string — 最近API数据更新日期",
    "last_manual_update": "string|null — 最近手动更新日期",
    "has_manual_data": "boolean — 是否有手动补充数据"
  }
}
```

## 字段说明

### 关键指标解读

| 字段 | 含义 | 数据来源 | 注意事项 |
|------|------|---------|---------|
| `citedness_2yr` | OpenAlex 计算的2年平均被引次数 | OpenAlex API | 与 JCR IF 略有差异，但趋势一致 |
| `cn_author_ratio` | 近2-3年有中国机构挂靠作者的文章占比 | OpenAlex Works API | 基于机构国家，非作者国籍 |
| `review_median_days` | Crossref 中有 received/accepted 日期的文章的中位审稿天数 | Crossref API | 仅反映最终录用论文的审稿时间，不含拒稿 |
| `review_coverage` | 有完整日期数据的采样文章比例 | Crossref | <0.3 时数据可信度较低 |

### 缺失数据约定

- `null` 表示数据未获取到或不适用
- 在推荐表格中显示为 "—"
- `review_coverage < 0.3` 时，`review_median_days` 可信度低，应提醒用户

### 预留扩展字段（Phase 2）

- `desk_reject_rate` — Desk Reject 率（来自 UGC 数据）
- `rejection_median_days` — 拒稿中位时间
- `user_ratings` — 用户评分
- `similar_journals` — 相似期刊列表
