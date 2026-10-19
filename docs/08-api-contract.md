# 08 API 契約說明（草案）

> 權威定義：[`api/openapi.yaml`](../api/openapi.yaml)（OpenAPI 3.1）。範例與反例：[`api/examples/contract-cases.yaml`](../api/examples/contract-cases.yaml)。事件：[12](12-event-contract.md)。**這是草案，尚未實作，也尚未凍結。**

## 1. 通用慣例（【建議預設】）
| 項目 | 規則 |
|------|------|
| Base URL | `http://127.0.0.1:8787/api/v1`（只開本機，Q-014） |
| 驗證 | `X-API-Key`；`/health` 不需要 |
| 格式 | JSON UTF-8；欄位用 `snake_case`；時間用 ISO-8601 UTC；金額用整數（元） |
| 回應欄位 | schema 中的 `required` 欄位一定會出現；可以是 null 的欄位以 `type: [X, 'null']` 表示，**不會省略** |
| 自訂動作 | `:verb` 後綴（例如 `:split`、`:revert`、`:reassess`） |
| 錯誤 | RFC 9457 Problem Details，並加上機器可讀的 `code` |
| 分頁 | cursor；回應一定有 `next_cursor`（最後一頁為 null） |

## 2. 刊登識別規則
| 情況 | 行為 |
|------|------|
| 平台來源（不是 `SRC-MANUAL`）有 `external_id` | 直接使用；`external_id_origin = PLATFORM` |
| 平台來源只有 `url` | 依來源規則從網址取出編號（規則【待查證】）；取不出來時，`external_id = sha256(正規化網址)`，`external_id_origin = URL_HASH` |
| 平台來源兩者都缺 | **schema 拒絕（422）** |
| `SRC-MANUAL` 兩者都缺 | 伺服器產生 `external_id = man_<ULID>`，`external_id_origin = SYSTEM_GENERATED`；必須附 `evidence_origin`（原始出處） |
| `(source_code, external_id)` 已存在 | 409 `LISTING_EXISTS`，並回傳 `existing_listing_id`。要更新內容請用 `POST /listings/{listing_id}/snapshots` |
| 重送 | 同一個 `Idempotency-Key` 重播原本的回應。**沒有帶 key 的 `SRC-MANUAL` 重送會建立新刊登**，之後由同屋辨識產生配對候選（不會自動刪除） |

快照不可修改：更正內容 = 新增一筆快照（內容雜湊與上一筆相同時回 200，不會新增）。

## 3. 版本檢查（樂觀鎖）
- 可修改的資源（listing、source、alert_rule、property、review_task、evidence）都有 `version`；GET 回傳 `ETag: "v{version}"`。
- 修改類請求（`PATCH`、`:split`、`:mark-delisted`、`:retract`、覆核決定）**必須**帶 `If-Match`：
  - 缺少 → `428 PRECONDITION_REQUIRED`
  - 不一致 → `412 STALE_VERSION`，並回傳 `current_etag`
- **覆核防過期**：覆核任務所依據的評估被取代時（例如有新快照或拆分），任務的 `version` 會加 1。用舊版本送出的覆核一定得到 412，所以不會覆蓋較新的結果。
- `POST /properties:merge` 涉及多個 property：用 body 的 `expected_versions` 逐一檢查，任何一個不一致就回 412。

## 4. Idempotency-Key
| 項目 | 規則 |
|------|------|
| 適用 | 所有 POST。下列操作**必須**帶（缺少 → 400 `IDEMPOTENCY_KEY_REQUIRED`）：`:import`、覆核決定、`:split`、`:revert`、`:merge`、`:reassess`、`:retract`、`:mark-delisted`、觸發輪次 |
| 作用範圍 | `(API key, HTTP method, route 樣板, path 參數值)` |
| 有效期限 | 24 小時（【建議預設】） |
| 相同 key、相同內容 | 重播第一次的狀態碼與內容，並加上 header `Idempotent-Replayed: true` |
| 相同 key、不同內容 | `422 IDEMPOTENCY_KEY_REUSED`（內容比對用請求 body 的 SHA-256；JSON 先做標準化排序） |
| 相同 key、第一次還在處理 | `409 IDEMPOTENCY_IN_PROGRESS` |
| 第一次失敗（5xx） | 不保存，可以用同一個 key 重試；4xx 會保存並重播 |

## 5. PATCH 語意（JSON Merge Patch，RFC 7396）
- Content-Type：`application/merge-patch+json`。
- **省略欄位** = 不變；**欄位設為 null** = 清除（只限 schema 允許 null 的欄位）。
- 不可為 null 的欄位設成 null → 422（例如 `ListingPatch.lister_type`、`AlertRulePatch.enabled`）。
- 未定義的欄位 → 422（`additionalProperties: false`）；空物件 → 422（`minProperties: 1`）。
- 狀態類欄位（例如 listing.status）不能用 PATCH 修改，只能透過動作端點（例如 `:mark-delisted`）。

## 6. 覆核 API
- `GET /review-tasks?task_type=...` 回傳 `ReviewTask`（依 `task_type` 區分的 oneOf）：
  - `COMMUNITY_MEMBERSHIP` → 附 `MembershipAssessment`
  - `ADDRESS_CANDIDATE` → 附 `AddressAssessment`（候選、排除原因、逐欄位狀態）
  - `LISTING_MATCH` → 附 `IdentityMatch` 與 `MatchAssessment`（衝突條件）
  - `REGISTRY_MAPPING` → 附 `RegistryMapping`
- `POST /review-tasks/{task_id}/decisions`：`ReviewDecision` 依 `decision` 區分；schema 層級強制：
  - `SET_UNIT` 必須有非 null 的 `address_unit_id`；`CONFIRM_CANDIDATE`、`REJECT_CANDIDATE` 必須有 `candidate_id`。
  - 確認類決定（`CONFIRM_*`、`SET_UNIT`、`VERIFY_MAPPING`）必須有 ≥ 1 筆 `evidence_ids` 或 `new_evidence`。
  - 所有決定都必須有 `rationale`（≥ 5 字）。
  - decision 與 task_type 不相符 → 422 `DECISION_NOT_ALLOWED`（伺服器檢查）。
  - **schema 合法不等於佐證充分**（v0.3）：伺服器依 06 §5.2 的 H1–H6 判定，結果寫在 `Review.support_assessment`（`SUFFICIENT`、`INSUFFICIENT`、`NOT_APPLICABLE`，並列出未達成的條件與確認的欄位）。佐證不充分時仍然回 201（覆核與證據都會保存），但評估維持原本的候選狀態，任務保持 OPEN。
  - 新的使用者證據（`UserEvidenceInput`）必須標明 `evidence_role` 與 `derivation_type`：`ORIGINAL` 必須有 `capture`；其他衍生方式必須有具型別的 `derived_from`（`ref_type` + `id` + `role`）；`LISTING_LINKAGE` 必須有 `link_method` 與 `linked_listing`。
- 覆核任務類型新增 `DERIVATION_RULE`（決定 `VERIFY_RULE`、`REJECT_RULE`）。
- `POST /reviews/{review_id}:retract`：撤回覆核，並重新評估。

## 7. 端點總覽
| 群組 | 端點 |
|------|------|
| 系統 | `GET /health`、`GET /community` |
| 推導規則（v0.3） | `GET /registry/derivation-rules`、`POST /registry/derivation-rules`、`GET /registry/derivation-rules/{rule_id}`、`POST /registry/derivation-rules/{rule_id}:retract` |
| 來源獨立性（v0.3） | `POST /evidence:independence`、`GET /evidence/{evidence_id}/dependents`、`GET /source-origins/{origin_id}`、`POST /origin-relations`、`POST /origin-relations/{relation_id}:retract` |
| 名冊 | `GET /registry/buildings`、`GET /registry/unit-types`、`GET /registry/sales-units`、`GET /registry/dwellings`、`GET /registry/address-units`、`GET /registry/mappings`、`GET /registry/coverage`、`POST /registry:import`、`POST /addresses:normalize` |
| 刊登 | `GET /listings`、`POST /listings`、`POST /listings:import`、`GET /listings/{listing_id}`、`PATCH /listings/{listing_id}`、`GET /listings/{listing_id}/snapshots`、`POST /listings/{listing_id}/snapshots`、`POST /listings/{listing_id}:mark-delisted` |
| 證據與評估 | `GET /listings/{listing_id}/evidence`、`GET /listings/{listing_id}/candidates`、`GET /listings/{listing_id}/assessments`、`POST /evidence`、`GET /evidence/{evidence_id}`、`POST /evidence/{evidence_id}:retract`、`GET /assessments/{assessment_id}`、`POST /assessments:reassess` |
| 同屋 | `GET /properties`、`GET /properties/{property_id}`、`GET /properties/{property_id}/history`、`POST /properties:merge`、`POST /properties/{property_id}:split`、`GET /properties:export`、`GET /identity-matches`、`GET /identity-matches/{match_id}`、`GET /merge-operations/{operation_id}`、`POST /merge-operations/{operation_id}:revert` |
| 覆核 | `GET /review-tasks`、`GET /review-tasks/{task_id}`、`POST /review-tasks/{task_id}/decisions`、`POST /reviews/{review_id}:retract` |
| 事件與通知 | `GET /events`、`GET /alert-rules`、`POST /alert-rules`、`PATCH /alert-rules/{rule_id}`、`DELETE /alert-rules/{rule_id}`、`GET /notifications` |
| 來源 | `GET /sources`、`PATCH /sources/{source_id}`、`GET /sources/{source_id}/runs`、`POST /sources/{source_id}/runs` |
| 社區總覽（v0.6） | `GET /community/overview` |
| 決策模型（v0.6，D-020） | `GET /decisions/status`、`GET /decisions/calls`、`GET /listings/{listing_id}/decisions`、`POST /listings/{listing_id}/decisions`、`POST /identity-matches/{match_id}/decisions` |

**v0.6 實作補充**：
- 只接受 Host 為 127.0.0.1／localhost 的請求（其他回 403 `FORBIDDEN_HOST`）。沒有設定 `JEV_API_KEY` 時，伺服器第一次啟動會產生隨機金鑰（`data/api_key.txt`），本機網頁自動帶入。
- 每個寫入請求是一個交易：任何一步失敗都整個還原。非預期錯誤回 500 `INTERNAL_ERROR`，細節只寫在 `data/logs/jev.log`。
- 決策模型端點：預設 409 `DECISIONS_DISABLED`；請求必須有 `consent: true`（否則 422 `CONSENT_REQUIRED`）；本月預算用完回 402 `BUDGET_EXCEEDED`；外部模型錯誤回 502。照片以 data URL 傳入，只在記憶體中處理，不保存。
- 實作狀態（哪些端點已實作）見 [17 §3](17-implementation-status.md#3-實作範圍)。

## 8. 請求與回應範例
**成功：SET_UNIT**
```http
POST /api/v1/review-tasks/7/decisions
If-Match: "v3"
Idempotency-Key: rv-7-20261101-01
Content-Type: application/json

{"decision":"SET_UNIT","address_unit_id":302,"rationale":"由刊登 591#123456 的仲介帶看，現場門牌 79 號 12 樓",
 "new_evidence":[
  {"field":"full_address","value":{"address_text":"高鐵南路一段79號12樓"},"description":"現場門牌照片（只存描述與雜湊）",
   "observed_at":"2026-11-01T06:00:00Z","evidence_role":"DIRECT_ADDRESS","derivation_type":"ORIGINAL",
   "capture":{"captured_by":"user","captured_at":"2026-11-01T06:00:00Z","method":"PHOTO"}},
  {"field":"listing_identity","value":{"listing_id":11},"description":"帶看仲介出示的刊登編號與看房單",
   "observed_at":"2026-11-01T06:00:00Z","evidence_role":"LISTING_LINKAGE","derivation_type":"ORIGINAL",
   "capture":{"captured_by":"user","captured_at":"2026-11-01T06:00:00Z","method":"SITE_VISIT_NOTE"},
   "link_method":"AGENT_VIEWING_WITH_LISTING_REF","linked_listing":{"listing_id":11,"listing_ref_seen":"591#123456"}}]}
```
→ `201`，回傳 `Review`：`created_evidence_ids` 是新的 HUMAN_ASSERTION；`support_assessment = {"result":"SUFFICIENT","unmet_conditions":[],"confirmed_fields":["full_address","address_number","floor"]}`；`result_assessment_id` 是重新評估後的結果（sales_unit 不會因此變成 CONFIRMED）。同樣格式的請求如果只有第一筆證據（沒有連結證據），也會回 201，但 `support_assessment.result = INSUFFICIENT`、`unmet_conditions = ["H2"]`。

**錯誤：過期覆核** → `412`
```json
{"type":"https://jev.local/problems/stale-version","title":"Precondition failed","status":412,
 "code":"STALE_VERSION","detail":"任務 #7 目前版本為 \"v4\"，請求帶的是 \"v3\"","current_etag":"\"v4\""}
```

**錯誤：SET_UNIT 缺少 address_unit_id** → `422 VALIDATION_ERROR`（反例 C-RD-02、C-RD-03）。

**錯誤：相同 key、不同內容** → `422 IDEMPOTENCY_KEY_REUSED`（C-PB-01）。

更多正例與反例見 `contract-cases.yaml`（v0.3 共 88 筆：39 筆正例、49 筆反例；schema 層面已經離線驗證，見 15。業務行為尚未測試）。

## 9. 契約治理
- 實作時由 FastAPI 產生 schema，並在 CI 與 `api/openapi.yaml` 做 diff；`contract-cases.yaml` 作為契約測試的固定資料。
- 欄位與 enum 以 05 為準；不一致時，先修 05，再修契約。
