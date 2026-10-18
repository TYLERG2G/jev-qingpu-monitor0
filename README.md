# JEV 專案：青埔房源監控、同屋辨識與完整門牌推斷

> 狀態：**規劃中（文件階段）**。repo 內只有規劃文件、Mermaid 圖表、API 與事件契約草案，**沒有應用程式碼**。尚未執行任何爬蟲、尚未部署，也沒有聯繫過任何外部平台。

- **JEV** 是專案代稱，縮寫意義待確認（Q-019）。JEV **不是**社區名稱。
- **試點社區**：桃園市大園區青埔「聯上世界」【已確認】。

## 目標【已確認】
1. **房源監控**：追蹤試點社區的刊登，並辨識上架、價格變動、下架與重新上架。
2. **跨刊登同屋辨識**：判斷不同平台、不同刊登者的刊登是否為同一間實體住宅，而且要能撤回與拆分。
3. **完整門牌推斷**：為刊登推斷完整門牌（路段＋號＋樓），並附上證據與可信度；證據不足時保留多個候選或棄答。

這三項判斷分開設計，互不取代：
**社區歸屬** ≠ **實體住宅身分** ≠ **完整門牌**。社區歸屬判定為 `MEMBER`，不代表已經確認完整門牌。

## MVP 範圍（全部是【建議預設】，尚未確認）
| 項目 | 建議預設 | 待確認 |
|------|----------|--------|
| 社區數 | 1（聯上世界） | 擴充時程 |
| 交易類型 | 中古屋買賣 | Q-020（買賣或租賃） |
| 使用者 | 單一使用者、個人非商業用途 | Q-021 |
| 通知 | Email 或 Telegram 擇一 | Q-005 |
| 部署 | 本機 Windows + SQLite | Q-006 |
| 預算 | 見成本模型 | Q-011 |

## 標記規則
詳見 [`AGENTS.md`](AGENTS.md#2-標記規則)。重點：**【建議預設】沒有被反對，不等於【已確認】**。所有未決事項集中在 [`docs/00-open-questions.md`](docs/00-open-questions.md)。

## 文件導覽
| 類別 | 文件 |
|------|------|
| 專案脈絡 | [背景](docs/project/background.md) · [聯上世界來源紀錄](docs/project/source-log-lianshang-world.md) · [決策紀錄](docs/project/decision-log.md) · [交接](docs/project/handoff.md) |
| 需求 | [00 待決事項](docs/00-open-questions.md) · [01 需求與追溯](docs/01-requirements.md) · [02 名詞與地址規則](docs/02-domain-and-glossary.md) |
| 資料 | [03 資料來源與權利](docs/03-data-sources-and-compliance.md) · [05 資料模型](docs/05-data-model.md) |
| 判斷邏輯 | [06 社區歸屬與完整門牌推斷](docs/06-address-verification.md) · [13 同屋辨識與合併治理](docs/13-identity-resolution.md) · [16 坪型指紋與照片比對](docs/16-layout-photo-matching.md) |
| 查證 | [M0 可行性報告](docs/research/m0-feasibility-report.md) · [門牌對照 CSV](docs/research/lianshang-world-address-mapping.csv) · [房源抽樣](docs/research/listing-sample-analysis.md) · [樂居整理樣板](docs/research/leju-layout-intake-template.csv) · [樂居格局分析](docs/research/leju-layout-analysis.md) · [坪數參考表](docs/research/unit-area-reference.csv) · [候選試算](docs/research/poc-candidate-trials.md) |
| 流程 | [04 架構](docs/04-architecture.md) · [07 監控生命週期](docs/07-monitoring-pipeline.md) |
| 契約 | [08 API 說明](docs/08-api-contract.md) · [`api/openapi.yaml`](api/openapi.yaml) · [12 事件契約](docs/12-event-contract.md) · [`api/examples/contract-cases.yaml`](api/examples/contract-cases.yaml) |
| 品質與維運 | [09 資安・個資・維運](docs/09-security-privacy-ops.md) · [10 測試與評估](docs/10-testing-strategy.md) · [15 一致性檢查表](docs/15-consistency-checklist.md) |
| 規劃 | [11 里程碑與風險](docs/11-roadmap-and-risks.md) · [14 成本模型](docs/14-cost-model.md) · [ADR](docs/adr/) |
| 協作規則 | [`AGENTS.md`](AGENTS.md) · [`CLAUDE.md`](CLAUDE.md) |

## 建議審閱順序
`project/background → 00 → 01 → project/source-log → 03 → 06 → 13 → 05 → 07 → 12 → 08/openapi → 10 → 15 → 其餘`
