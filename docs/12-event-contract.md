# 12 事件契約

> Schema 的權威定義在 [`api/openapi.yaml`](../api/openapi.yaml) 的 `components.schemas.Event` 與各個 `*Event` schema。本文件說明語意、冪等與版本規則。範例放在 [`api/examples/contract-cases.yaml`](../api/examples/contract-cases.yaml)。

## 1. 信封（所有事件共用，所有欄位都必填）
| 欄位 | 型別 | 說明 |
|------|------|------|
| `event_id` | string（ULID，26 字元） | 每次發出都是唯一值 |
| `event_type` | enum | 見 §2 |
| `schema_version` | integer | 該事件類型的 payload 版本，目前全部是 1 |
| `idempotency_key` | string | 由規則決定的業務鍵（§3）；DB UNIQUE |
| `occurred_at` | date-time | 業務上發生的時間（例如判定下架的那一輪結束時間） |
| `recorded_at` | date-time | 寫入時間 |
| `subject` | object | `listing_id`、`property_id`、`source_id`（都可以是 null，但要符合 §2 的必填規則） |
| `caused_by` | object | `fetch_run_id`、`snapshot_id`、`operation_id`、`review_id`、`assessment_id`（都可以是 null） |
| `notify_eligible` | boolean | v0.3 新增。是否可以拿來比對通知規則（§2.1）；**事件一律保存**，與是否通知無關 |
| `suppression_reason` | enum 或 null | `notify_eligible = false` 時必填：`BASELINE_CONTEXT`、`MANUAL_INITIAL_CONTEXT`、`NOT_CONFIRMED_NEW`、`INTERNAL_EVENT`；`true` 時必須是 null |
| `payload` | object | 依類型而定（§2） |

## 2. 事件類型與 payload 必填欄位
| `event_type` | subject 必填 | payload 必填欄位 | `notify_eligible` | 預設規則訂閱 |
|--------------|--------------|------------------|-------------------|--------------|
| `LISTING_FIRST_OBSERVED` | listing_id | `context`、`new_listing_status`、`cycle_no`、`snapshot_id` | false（context 是 BASELINE → `BASELINE_CONTEXT`；MANUAL_INITIAL → `MANUAL_INITIAL_CONTEXT`） | — |
| `LISTING_NEW_CONFIRMED` | listing_id | `cycle_no`、`snapshot_id`、`confirmation_basis`（`COMPLETE_RUN_DIFF`、`POSTED_AT`、`USER_FLAG`）、`late_confirmation` | true | 是 |
| `DWELLING_NEW_LISTING` | listing_id、property_id | `anchor_listing_id`、`other_listing_ids`（≥ 1）、`joining_listing_context`（只能是 INCREMENTAL 或 MANUAL_INCREMENTAL）、`new_listing_status`（必須是 CONFIRMED_NEW）、`trigger`、`trigger_operation_id`、`property_was_inactive` | true | 是 |
| `LISTING_RELISTED` | listing_id | `previous_cycle_no`、`cycle_no`、`delisted_at`、`snapshot_id`（可以是 null） | true | 是 |
| `LISTING_PRICE_CHANGED` | listing_id | `cycle_no`、`from_snapshot_id`、`to_snapshot_id`、`old_price`、`new_price`、`diff`、`pct`、`direction` | true | DOWN 才訂閱 |
| `LISTING_FIELDS_CHANGED` | listing_id | `cycle_no`、`from_snapshot_id`、`to_snapshot_id`、`changed_fields`（≥ 1） | true | 否 |
| `LISTING_DELISTED` | listing_id | `cycle_no`、`reason`、`last_snapshot_id`（可以是 null）、`last_seen_at`、`missing_complete_run_ids` | true | 是 |
| `PROPERTY_MERGED` | property_id | `operation_id`、`op_type`、`into_property_id`、`merged_property_ids`、`listing_ids`、`initial_context_listing_ids`、`new_listing_ids` | false（`INTERNAL_EVENT`） | — |
| `PROPERTY_SPLIT` | property_id | `operation_id`、`groups`（≥ 2 組，每組有 property_id 與 listing_ids） | false（`INTERNAL_EVENT`；更正通知另計） | — |
| `MERGE_REVERTED` | property_id | `operation_id`、`reverted_operation_id`、`restored`（≥ 1） | false（`INTERNAL_EVENT`；更正通知另計） | — |
| `COMMUNITY_MEMBERSHIP_CHANGED` | listing_id | `assessment_id`、`previous_assessment_id`、`old_status`、`new_status`、`basis` | false（`INTERNAL_EVENT`） | — |
| `ADDRESS_ASSESSMENT_CHANGED` | listing_id | `assessment_id`、`previous_assessment_id`、`old_status`、`new_status`、`full_address_status`、`candidate_count`、`basis`、`trigger` | false（`INTERNAL_EVENT`） | — |
| `SOURCE_HEALTH_CHANGED` | source_id | `old_health`、`new_health`、`consecutive_failures`、`fetch_run_id`、`auto_fetch_disabled` | true | 系統告警 |
| `REGISTRY_UPDATED` | — | `registry_version_id`、`previous_registry_version_id`、`changes`（≥ 1，含 item_type、item_id、新舊狀態）、`affected_listing_ids` | false（`INTERNAL_EVENT`） | — |

### 2.1 保存與通知分開（v0.3，【建議預設】）
- **保存**：所有事件都寫入 `listing_event`，供稽核與追溯。
- **可通知**：只有 `notify_eligible = true` 的事件會交給 Notifier 比對 `alert_rule`；`notify_eligible` 在**產生事件時**依來源脈絡決定，之後不會改變。
- **同屋關係 vs. 同一住宅真正新增刊登**：
  - `PROPERTY_MERGED`：系統發現新的同屋關係（包括初始化資料之間、或延遲完成的合併）。只保存，不通知。
  - `DWELLING_NEW_LISTING`：只在「加入群組的刊登本身是 `CONFIRMED_NEW`」**而且**「群組中有比它更早首次觀測的其他刊登」這兩個條件**都成立的那一刻**發出（`trigger = MERGE` 或 `NEW_LISTING_CONFIRMED`）。刊登是否來自初始化，看刊登自己的 `first_observed_context` 與 `new_listing_status`，**不看合併發生的時間**。
  - 因此 BASELINE、MANUAL_INITIAL 的舊刊登，即使在初始化結束後很久才合併，也只會產生 `PROPERTY_MERGED`（schema 層也禁止 `joining_listing_context` 是初始化值）。

- `LISTING_DELISTED` **不需要新快照**：判定依據是完整輪次的缺席（`missing_complete_run_ids`），或來源明示（`evidence_document_id`），或人工標記（`caused_by.review_id` 或 API 呼叫紀錄）。
- `LISTING_RELISTED` 如果只在列表看到、詳情頁失敗，`snapshot_id` 可以是 null。

## 3. 冪等鍵
| 事件 | `idempotency_key` 格式 |
|------|------------------------|
| LISTING_FIRST_OBSERVED | `first_observed:{listing_id}` |
| LISTING_NEW_CONFIRMED | `new_confirmed:{listing_id}`（同一則刊登一生只發一次） |
| DWELLING_NEW_LISTING | `dwelling_new_listing:{listing_id}`（v0.3.1 改：**每則刊登一生最多一次**；不依賴 operation_id，也不依賴可變的群組成員或 anchor）。之後不論拆分、撤回、再合併回同一群組，或改併到其他群組，都**不重發**新案，只發更正通知（§5）。payload 的 `anchor_listing_id` 只是發出當時的參考資訊 |
| REGISTRY_UPDATED | `registry_updated:{registry_version_id}` |
| LISTING_RELISTED | `relisted:{listing_id}:{cycle_no}` |
| LISTING_PRICE_CHANGED | `price:{listing_id}:{to_snapshot_id}` |
| LISTING_FIELDS_CHANGED | `fields:{listing_id}:{to_snapshot_id}` |
| LISTING_DELISTED | `delisted:{listing_id}:{cycle_no}` |
| PROPERTY_MERGED、PROPERTY_SPLIT、MERGE_REVERTED | `{event_type 小寫}:{operation_id}` |
| COMMUNITY_MEMBERSHIP_CHANGED、ADDRESS_ASSESSMENT_CHANGED | `{event_type 小寫}:{assessment_id}` |
| SOURCE_HEALTH_CHANGED | `source_health:{source_id}:{fetch_run_id}` |

規則：
- 產生事件時，如果 `INSERT ... ON CONFLICT(idempotency_key) DO NOTHING` 沒有寫入，代表重複，不能再通知。
- 重新處理同一輪（例如程式中斷後重跑）不會產生重複事件。
- 重複的「下架 → 重新上架 → 下架」：每次都帶不同的 `cycle_no`，所以都是新事件；同一個週期內不會有兩次下架。

## 4. 版本規則
- `schema_version` 依事件類型分開計算。
- **相容變更**（新增選填欄位）：版本不變；消費端必須忽略不認識的欄位。
- **破壞性變更**（刪除或改名欄位、改變必填、改變語意）：版本 +1；新舊兩版至少並行一個里程碑；`GET /events?schema_version=` 可以指定版本。

## 5. 傳遞與消費
- 事件存在 `listing_event` 表，依 `id` 遞增；`GET /events` 提供 cursor 分頁。
- 傳遞語意是**至少一次**：Notifier 以 `notification` 的 UNIQUE(`alert_rule_id`, `event_id`) 防止重送。
- 通知保存發送當時的內容快照（`rendered`），以及 `event_id`。之後的合併、拆分不會改寫歷史通知；需要更正時，另發一則更正通知（`corrects_notification_id`）。
- **更正通知**（`notification_kind = CORRECTION`）：已發過 DWELLING_NEW_LISTING 通知的刊登，之後因拆分、撤回或合併而改變所屬群組時，如果 alert_rule 的 `notify_corrections = true`，Notifier 就依觸發的事件（PROPERTY_SPLIT、MERGE_REVERTED、PROPERTY_MERGED；這些事件本身是 notify_eligible = false）發一則更正通知，`corrects_notification_id` 指向原通知。更正通知不受 `notify_eligible` 影響，也不會重發新案。
- 撤回合併後又合併回同一群組、或改併到另一個群組：DWELLING_NEW_LISTING 的冪等鍵已存在，不會產生新事件；只會發引用原通知的更正通知。

## 6. 範例（LISTING_DELISTED，沒有新快照）
```json
{
  "event_id": "01JB2Z8Q4K7M3N5P6R8S9T0V1W",
  "event_type": "LISTING_DELISTED",
  "schema_version": 1,
  "idempotency_key": "delisted:11:2",
  "occurred_at": "2026-11-05T13:10:00Z",
  "recorded_at": "2026-11-05T13:10:02Z",
  "subject": {"listing_id": 11, "property_id": 5, "source_id": 2},
  "caused_by": {"fetch_run_id": 88, "snapshot_id": null, "operation_id": null, "review_id": null, "assessment_id": null},
  "notify_eligible": true,
  "suppression_reason": null,
  "payload": {
    "cycle_no": 2,
    "reason": "MISSING_IN_COMPLETE_RUNS",
    "last_snapshot_id": 340,
    "last_seen_at": "2026-11-04T01:05:00Z",
    "missing_complete_run_ids": [87, 88],
    "evidence_document_id": null,
    "days_listed": 21
  }
}
```
