# 15 文件完整性與跨文件一致性檢查表

> 每次修訂結束前都要執行。「結果」欄只記錄**實際執行過**的檢查。
> 最近一次執行：2026-10-18，v0.3.1 修訂。工具：Python（Anaconda）的 PyYAML 與 jsonschema 4.26（Draft 2020-12），加上兩個暫存腳本（沒有放進 repo，見 handoff）。沒有安裝任何新工具。
> **這些全部是文件與 schema 層面的檢查**；10 §3 的業務行為測試尚未執行（只有規劃）。

## 1. 自動檢查（已執行）
| # | 檢查項 | 方法 | v0.3 結果 |
|---|--------|------|-----------|
| A-01 | `openapi.yaml` 可以解析，版本是 3.1.0 | `yaml.safe_load` | 通過（53 個路徑、60 個操作、151 個 schema） |
| A-02 | 所有 `$ref` 都能解析 | 逐一走訪 | 通過（471 個） |
| A-03 | 每個 component schema 都是合法的 JSON Schema 2020-12 | `Draft202012Validator.check_schema` | 通過 |
| A-04 | discriminator 的 mapping 與 oneOf 一致，每個目標都有對應的 const | 腳本 | 通過 |
| A-05 | operationId 唯一；path 參數都有宣告；每個操作都有 responses | 腳本 | 通過 |
| A-06 | openapi 內嵌的請求與回應範例符合 schema（包括更新後的 SET_UNIT 範例） | jsonschema | 通過 |
| A-07 | `contract-cases.yaml`：正例全部合法、反例全部被拒絕 | jsonschema | 通過（40 筆正例、52 筆反例） |
| A-07b | **反例是因預期原因被拒絕**：依 discriminator 選出子 schema，列出全部錯誤，逐筆人工比對 note | 暫存腳本 `neg_reasons.py` | 通過；修正了 5 筆有附帶錯誤的反例（理由或描述太短，會混淆原因）：C-UE-02、C-SP-02、C-RA-02、C-DR-03、C-RD-09 |
| A-08 | 14 種事件類型都有至少一個正例 | 腳本 | 通過 |
| A-09 | 12、13 文件中的 JSON 範例符合 schema（Event、MergeOperation） | jsonschema | 通過 |
| A-10 | 05 §4 的 enum 表與 OpenAPI enum 完全一致 | 腳本（60 組） | 通過 |
| A-11 | 05 ERD 的欄位涵蓋 API schema 的欄位（衍生欄位除外） | 腳本（27 個實體，包括 source_origin、origin_relation、address_derivation_rule） | 通過 |
| A-12 | 文件中以 `METHOD /path` 提到的端點都存在於 OpenAPI | 腳本 | 通過（119 處） |
| A-13 | 12 的事件表與 `EventType` enum 一致 | 腳本 | 通過（14 種） |
| A-14 | 內部相對連結與錨點都有效 | 腳本（GitHub 風格錨點） | 通過（30 個檔案、66 個連結） |
| A-15 | 所有引用的 `Q-xxx` 都在 00 中定義 | 腳本 | 通過（28 題） |
| A-16 | 禁用寫法：「JEV 社區」「沒有異議就…」「零合規風險」、舊事件名、舊狀態 MATCH/LIKELY、未查證的實價登錄結論、未經確認標成「已接受」 | regex（引述舊版的紀錄除外） | 通過（v0.3.1 依 D-013 修正 04 之後） |
| A-17 | 「樂屋網」只出現在次要或區分語境 | regex | 通過 |
| A-18 | 建議預設項目沒有被標成【已確認】 | regex | 通過 |
| A-19 | Mermaid 基本語法 lint（圖表類型、subgraph/end、括號平衡、未加引號的特殊字元、erDiagram 欄位與關聯、stateDiagram 轉移、sequence 區塊） | 腳本（15 張圖，包括外部改寫的 04 的 4 張） | 通過。v0.3 修正了 lint 本身的誤報：`-- "文字" -->` 是合法語法 |

## 2. 人工檢查（已執行）
| # | 檢查項 | 結果 |
|---|--------|------|
| M-01 | 每一處【已確認】都能在 decision-log 找到出處；v0.3 的新設計都標成【建議預設】 | 已檢視；D-106～D-109 都是「提議中」 |
| M-02 | 10 的每個測試預期，都能由 06、13、05 §5.4–5.5、12 的規則**實際推導**（不只是欄位名稱一致） | 已逐筆推導，並寫在「推導」欄：T-ADDR-01～04、09a～09g、11a～11c、T-RULE-01～06、T-EVD-06～12、T-IDN-02、T-LIFE-13～19。v0.2 中無法由規則推出的 T-ADDR-04、09、11、T-EVD-01、T-IDN-02 已修正（10 §4） |
| M-03 | 「名冊核對前不產生 CONFIRMED」與人工確認的關係 | 已統一：06 §5.3 改成「SYSTEM_RULES 不產生；HUMAN_REVIEW 在 H1–H6 成立時可以確認 full_address，但不確認戶別」；10 的 T-ADDR-09a 與 C-AS-03 一致 |
| M-04 | 涵蓋度與規則正確性分開 | 06 §4.1a、§8；05 §5.5；OpenAPI `RegistryCoverage.dimension` 說明；ADR-006 |
| M-05 | 所有 dependency_group 用法都已移除或標為舊版 | 05、06、10、13、ADR-004、ADR-005、04（v0.3.1）都已更新 |
| M-06 | 初始化通知：07 §1、12 §2.1、§3、13 §6–7、ADR-007、OpenAPI 事件 schema、contract-cases、10 T-LIFE-13～19 的語意一致 | 已對照 |
| M-08 | v0.3.1：不同仲介不自動獨立（05 §5.4 規則 I1 修訂、T-EVD-09c、T-IDN-02 重新推導）；新案去重只看 listing_id（OpenAPI pattern、12 §3、§5、07、13、ADR-007、T-LIFE-16、T-LIFE-20、C-EVT-26、C-NT-*） | 已對照，並重新推導 T-IDN-02（UNKNOWN → REVIEW_REQUIRED，分數 0.6） |
| M-07 | 沒有把未執行的測試、未查證的來源、未取得的授權寫成已完成 | 已檢查；10 開頭明確區分 schema 驗證與業務測試 |

## 3. 未能檢查或尚未解決的項目（如實記錄）
| # | 項目 | 原因 | 建議 |
|---|------|------|------|
| U-01 | Mermaid **實際渲染** | 本機沒有 mermaid-cli；依規定不安裝全域工具 | 在 GitHub 或支援 Mermaid 的編輯器預覽；或經同意後在專案內安裝 `@mermaid-js/mermaid-cli` |
| U-02 | 用官方 OpenAPI 3.1 meta-schema 驗證整份文件 | 本機沒有 openapi-spec-validator | 經同意後在專案虛擬環境安裝 |
| U-03 | `format`（date-time、uri）嚴格驗證 | jsonschema 只有在安裝對應套件時才檢查 format | 實作階段加入 |
| U-04 | 來源網址的內容、條款、可用性 | 本階段禁止連線 | M0 的 V-01～V-07 |
| U-05 | 伺服器端規則：佐證充分性（H1–H6）、來源關係判定、連鎖撤回、decision 與 task_type 相符、If-Match、冪等、`floor_min ≤ floor_max`、規則衝突 | schema 無法表達，屬於實作行為 | 實作後以 10 的業務測試驗證 |
| U-06 | （已解決）04 曾被外部改寫而與契約不一致 | v0.3.1 依使用者授權（D-013）修正 | 處理紀錄見 [handoff](project/handoff.md#04-架構文件的處理紀錄) |

## 4. 每次修訂的檢查清單
- [x] 00：新的未決事項已經編號（Q-026～Q-028）；已解決的保留歷史
- [x] decision-log：新的提議已經記錄（D-106～D-109、v0.3 修訂紀錄）
- [x] 05 ↔ openapi ↔ 12 ↔ 10 ↔ 04：欄位、enum、事件、測試同步
- [x] contract-cases：新的 schema 有正例與反例
- [x] 01 追溯矩陣：FR-20～FR-23、AC-17～AC-20
- [x] handoff：目前狀態、外部修改、已知限制、下一步已更新
- [x] 重新執行 §1；把結果與無法檢查的項目寫回本檔
