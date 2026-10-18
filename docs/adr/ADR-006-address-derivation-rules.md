# ADR-006 門牌推導規則與涵蓋度分開

- 狀態：**提議中**（【建議預設】）
- 日期：2026-10-18（v0.3）

## 背景
v0.2 要求「同一戶別各樓層共用同一個號」這條規則必須是 VERIFIED，卻把它記在 `registry_coverage` 的 FLOOR 維度。但 `registry_coverage` 只有完整度欄位，沒辦法記錄規則內容、適用範圍、例外、驗證狀態與證據。另外，v0.2 的候選生成會用「1..總樓高」推定每一層都有相同的戶別安排。

## 決策（提議）
1. 新增 `address_derivation_rule`（05 §5.5）：適用範圍（社區、棟別、戶別）、樓層區間（必填）、規則類型（`SAME_NUMBER_ALL_FLOORS`、`EXPLICIT_LIST`）、樓層標示格式、例外（`SHOP`、`NO_DWELLING`、`MERGED_UNIT`、`DIFFERENT_NUMBER`）、`record_status`、版本、取代關係，以及證據。
2. `registry_coverage` 只表示資料完整度，不代表任何規則正確。
3. 系統確認（K2）需要：已驗證的逐戶門牌 mapping；或者「戶別→號」mapping 已驗證，而且有已驗證、適用於該戶別與樓層、沒有衝突的規則。
4. 樓層候選只來自刊登的樓層事實、規則範圍（扣除例外），或名冊列出的 dwelling；**不依總樓高推定**。
5. 規則可以覆核（`DERIVATION_RULE` 任務）與撤回；狀態變更會產生新的 registry_version、發出 `REGISTRY_UPDATED`，並依 `registry_items_used` 重新評估。

## 替代方案
| 方案 | 不選的原因 |
|------|------------|
| 擴充 `registry_coverage` 加上規則欄位 | 會把「完整度」與「正確性」混在同一張表，語意不清 |
| 只用逐戶的 `DWELLING_TO_ADDRESS` mapping | 正確，但在只有戶別對照時無法表達「已驗證的通則」；保留為規則類型 `EXPLICIT_LIST` |

## 後果
多一張表與一種覆核任務；換來可以版本化、可以撤回、可以追溯的門牌推導。
