# 05 資料模型

> 【建議預設】先用 SQLite 實作，型別寫法盡量相容 PostgreSQL（ADR-002）。時間存 UTC ISO-8601；金額用整數，單位是元；面積用坪，`NUMERIC(8,2)`。`json` 欄位在 SQLite 存成 TEXT，由應用層驗證。
> 本文件是欄位名稱與 enum 的**唯一定義來源**。`api/openapi.yaml` 與 [12 事件契約](12-event-contract.md) 都要與本文件一致。

## 1. ERD（一）名冊
```mermaid
erDiagram
  community ||--o{ building : has
  community ||--o{ unit_type : has
  community ||--o{ sales_unit : has
  building |o--o{ sales_unit : groups
  unit_type |o--o{ sales_unit : typed_as
  sales_unit ||--o{ dwelling : floors
  community ||--o{ address_unit : has
  registry_version ||--o{ registry_mapping : contains
  registry_version ||--o{ registry_coverage : declares
  registry_version ||--o{ address_derivation_rule : contains
  address_derivation_rule |o--o| address_derivation_rule : supersedes

  address_derivation_rule {
    int id PK
    int community_id FK
    int registry_version_id FK
    string scope_type "COMMUNITY or BUILDING or SALES_UNIT"
    json scope_ids "棟別或戶別 id；COMMUNITY 時為空"
    int floor_min "必填"
    int floor_max "必填"
    string rule_type "SAME_NUMBER_ALL_FLOORS or EXPLICIT_LIST"
    string floor_label_format "例如 {floor}樓"
    json exceptions "SHOP, NO_DWELLING, MERGED_UNIT, DIFFERENT_NUMBER"
    string record_status
    int supersedes_rule_id FK "nullable"
    int version
    datetime created_at
    json evidence_ids "驗證或撤回時引用的佐證"
  }
  community {
    int id PK
    string code UK "lianshang-world"
    string name "聯上世界"
    string city "桃園市"
    string district "大園區"
    string area_name "青埔"
    json aliases
  }
  building {
    int id PK
    int community_id FK
    string label "棟別"
    int floors_above "nullable"
    string record_status
  }
  unit_type {
    int id PK
    int community_id FK
    string code
    float area_ping_min "權狀坪"
    float area_ping_max
    string parking_basis "INCLUDED or EXCLUDED or UNKNOWN"
    int rooms
    int halls
    int baths
    json layout_fingerprint "nullable；16 §2"
    string record_status
  }
  sales_unit {
    int id PK
    int community_id FK
    int building_id FK "nullable"
    int unit_type_id FK "nullable"
    string code "A1, D6"
    string orientation "nullable"
    string record_status
  }
  dwelling {
    int id PK
    int sales_unit_id FK
    int floor
    string record_status "含 HYPOTHETICAL"
  }
  address_unit {
    int id PK
    int community_id FK
    string addr_key UK
    string raw_address
    string road
    int section
    string lane "nullable"
    string alley "nullable"
    int number
    int number_sub "nullable"
    string floor "nullable；null 代表號層級"
    int floor_sub "nullable"
    string precision "FLOOR or NUMBER"
    string origin
    string record_status
  }
  registry_mapping {
    int id PK
    int registry_version_id FK
    string mapping_type
    string from_type
    int from_id
    string to_type
    int to_id
    string record_status
    int version
    json evidence_ids "驗證或撤回時引用的佐證"
  }
  registry_version {
    int id PK
    string label
    int parent_id FK "nullable"
    datetime created_at
  }
  registry_coverage {
    int id PK
    int registry_version_id FK
    string scope_type
    string scope_key
    string dimension
    string completeness "COMPLETE or PARTIAL or UNKNOWN"
  }
```

## 2. ERD（二）來源、刊登與監控
```mermaid
erDiagram
  source ||--o{ fetch_run : runs
  source ||--o{ listing : publishes
  source ||--o{ source_document : provides
  fetch_run ||--o{ run_observation : records
  listing ||--o{ run_observation : seen_in
  listing ||--o{ listing_snapshot : observed_as
  listing ||--o{ listing_cycle : cycles
  source_document |o--o{ listing_snapshot : backs
  listing_event }o--o| listing : about
  alert_rule ||--o{ notification : triggers
  listing_event ||--o{ notification : notified_by

  source {
    int id PK
    string code UK
    string name
    string priority "PRIMARY or SECONDARY"
    string access_method
    json rights "acquire, store, image_use, republish"
    bool auto_fetch_enabled
    int min_interval_sec
    json canonical_scope
    string canonical_scope_fingerprint "nullable"
    datetime baseline_completed_at "nullable"
    json compliance
    int version
  }
  fetch_run {
    int id PK
    int source_id FK
    string run_type
    string status
    string coverage
    json coverage_reasons
    string import_mode "nullable"
    string query_fingerprint "nullable"
    int pages_fetched
    int page_cap
    bool page_cap_hit
    bool pagination_exhausted
    int reported_total "nullable"
    int enumerated_count
    int list_parse_failures
    int detail_failures
    datetime started_at "nullable"
    datetime finished_at "nullable"
    string error_summary "nullable"
  }
  run_observation {
    int fetch_run_id PK
    int listing_id PK
    string presence "SEEN or DETAIL_FAILED"
    int snapshot_id FK "nullable"
  }
  listing {
    int id PK
    int source_id FK
    string external_id
    string external_id_origin
    string url "nullable"
    string deal_type
    string status
    int current_cycle_no
    int consecutive_missing_complete_runs
    datetime first_observed_at
    string first_observed_context
    string new_listing_status
    datetime last_seen_at
    string lister_type
    string lister_company "nullable"
    int property_id FK "目前所屬，與 property_membership 一致"
    int version
  }
  listing_cycle {
    int id PK
    int listing_id FK
    int cycle_no
    datetime started_at
    string start_reason
    datetime ended_at "nullable"
    string end_reason "nullable"
  }
  listing_snapshot {
    int id PK
    int listing_id FK
    int fetch_run_id FK "nullable"
    int source_document_id FK "nullable"
    datetime observed_at
    string content_hash
    int total_price
    int parking_price "nullable"
    string parking_included
    float building_ping "nullable，權狀坪"
    float main_ping "nullable"
    float parking_ping "nullable"
    string floor_text "nullable，原文"
    int floor_min "nullable"
    int floor_max "nullable"
    bool floor_exact
    int total_floors "nullable"
    int rooms "nullable"
    int halls "nullable"
    int baths "nullable"
    string orientation "nullable"
    float age_years "nullable"
    string address_text "nullable"
    string address_precision
    string community_text "nullable"
    string platform_community_ref "nullable"
    string sales_unit_text "nullable"
    int units_per_floor "nullable，刊登揭露的該層戶數"
    string map_address "nullable，地圖定位地址（不觸發 N1）"
    string title "nullable"
    datetime posted_at "nullable"
    string parser_version "nullable"
  }
  source_document {
    int id PK
    int source_id FK
    string origin_source_code "nullable"
    string doc_kind
    string url "nullable"
    string content_hash
    string raw_ref "nullable，可刪除"
    datetime raw_deleted_at "nullable"
  }
  listing_event {
    int id PK
    string event_id UK
    string event_type
    int schema_version
    string idempotency_key UK
    int listing_id FK "nullable"
    int property_id FK "nullable"
    int source_id FK "nullable"
    json caused_by "fetch_run, snapshot, operation, review, assessment"
    json payload
    bool notify_eligible "是否可以通知；事件一律保存"
    string suppression_reason "nullable"
    datetime occurred_at
    datetime recorded_at
  }
  alert_rule {
    int id PK
    string name
    bool enabled
    json event_types
    int max_total_price "nullable"
    float min_price_drop_pct "nullable"
    int min_rooms "nullable"
    float min_building_ping "nullable"
    json membership_statuses
    string channel
    bool notify_corrections
    int version
  }
  notification {
    int id PK
    string notification_kind "EVENT or CORRECTION"
    int alert_rule_id FK
    string event_id FK
    string channel
    json rendered "發送當時的內容快照"
    string status
    int attempts
    string last_error "nullable"
    datetime sent_at "nullable"
    int corrects_notification_id FK "nullable"
  }
```

## 3. ERD（三）證據、評估、覆核與同屋
```mermaid
erDiagram
  evidence ||--o{ evidence_link : links
  evidence |o--o{ evidence : supersedes
  evidence ||--o{ evidence_derivation : derived_via
  source_origin ||--o{ evidence : roots
  source_origin ||--o{ origin_relation : relates
  candidate ||--o{ assessment_candidate : scored_in
  assessment ||--o{ assessment_candidate : ranks
  assessment ||--o{ assessment_evidence : uses
  evidence ||--o{ assessment_evidence : used_by
  assessment |o--o| assessment : superseded_by
  review_task ||--o{ review : has
  assessment |o--o{ review_task : opens
  review ||--o{ review_evidence : cites
  evidence ||--o{ review_evidence : cited_in
  review |o--o| review : supersedes
  property ||--o{ property_membership : history
  identity_match ||--o{ assessment : assessed_by
  merge_operation ||--o{ merge_operation_listing : affects
  merge_operation |o--o| merge_operation : reverts

  evidence {
    int id PK
    string kind "SOURCE_FACT or INFERENCE or HUMAN_ASSERTION"
    string origin
    int listing_id FK "nullable"
    int snapshot_id FK "nullable"
    int source_document_id FK "nullable"
    int review_id FK "nullable"
    string field
    json value
    json locator
    string excerpt "nullable，≤200字，已遮罩"
    string excerpt_hash
    datetime observed_at
    string evidence_role "DIRECT_ADDRESS, DIRECT_UNIT, LISTING_LINKAGE, FEATURE_OBSERVATION, OTHER"
    string derivation_type "ORIGINAL, COPY, REPOST, CROP, OCR, TRANSCRIPTION, REVIEW_CITATION, INFERENCE"
    json origin_ids "根來源集合（快取，可由 evidence_derivation 重算）"
    json capture "nullable：取得者、時間、方式"
    string link_method "nullable，LISTING_LINKAGE 才有"
    string content_hash "nullable，只作重複線索"
    string extractor_version "nullable"
    string status
    string status_reason "nullable，例如 UPSTREAM_RETRACTED"
    int superseded_by_id FK "nullable"
    int version
  }
  evidence_derivation {
    int evidence_id PK
    string ref_type PK "EVIDENCE, IDENTITY_MATCH, REVIEW, ASSESSMENT, CANDIDATE, REGISTRY_MAPPING, DERIVATION_RULE, SOURCE_DOCUMENT"
    int ref_id PK
    string role "COPIED_FROM, EXTRACTED_FROM, CITES, INFERRED_FROM, VIA_MATCH"
  }
  source_origin {
    int id PK
    string origin_type "PLATFORM_LISTING, AGENT_STATEMENT, OWNER_STATEMENT, USER_SITE_VISIT, DOCUMENT, SALES_CONTROL_TABLE, GOV_RECORD, PRIOR_TRANSCRIPTION, SYSTEM"
    string asserting_party_type "AGENT, OWNER, PLATFORM, USER, GOVERNMENT, BUILDER, SYSTEM, UNKNOWN"
    string asserting_party_key "nullable，例如正規化公司名"
    string captured_by "nullable"
    datetime captured_at "nullable"
    string root_content_hash "nullable，只作重複線索"
    string description
  }
  origin_relation {
    int id PK
    int origin_a_id FK
    int origin_b_id FK
    string relation "SAME, DEPENDENT, INDEPENDENT, UNKNOWN"
    string basis "RULE_I1, RULE_I2, RULE_I3, HUMAN_REVIEW"
    string rationale
    json evidence_ids
    string status "ACTIVE or RETRACTED"
    datetime created_at
  }
  evidence_link {
    int id PK
    int evidence_id FK
    string target_type
    int target_id
    string target_field
    string stance "SUPPORTS or REFUTES or NEUTRAL"
    string rule_code "nullable"
  }
  candidate {
    int id PK
    int listing_id FK
    int dwelling_id FK "nullable"
    int address_unit_id FK "nullable"
    string generated_by
  }
  assessment {
    int id PK
    string subject_type
    int listing_id FK "nullable"
    int identity_match_id FK "nullable"
    int snapshot_id FK "nullable"
    string ruleset_version
    int registry_version_id FK
    string evidence_set_hash
    string outcome
    float rule_score "nullable"
    float calibrated_probability "nullable"
    string calibration_version "nullable"
    string basis
    json resolved_fields "nullable；每欄位 status、value、basis、evidence_ids"
    json registry_items_used "名冊 mapping 與推導規則的 id 與版本"
    json flags
    string trigger
    string status "CURRENT or SUPERSEDED"
    int superseded_by_id FK "nullable"
    datetime created_at
  }
  assessment_evidence {
    int assessment_id PK
    int evidence_id PK
    int evidence_version
    json used_by_rules
  }
  assessment_candidate {
    int assessment_id PK
    int candidate_id PK
    float rule_score
    bool excluded
    string excluded_by_rule "nullable"
    json rule_hits
  }
  review_task {
    int id PK
    string task_type
    int listing_id FK "nullable"
    int identity_match_id FK "nullable"
    int registry_mapping_id FK "nullable"
    int derivation_rule_id FK "nullable"
    int base_assessment_id FK "nullable"
    string status
    int priority
    int version
    datetime created_at
  }
  review {
    int id PK
    int task_id FK
    string decision
    string rationale
    string actor
    int base_assessment_id FK "nullable"
    int result_assessment_id FK "nullable"
    int candidate_id FK "nullable"
    int address_unit_id FK "nullable"
    string status "ACTIVE or SUPERSEDED or RETRACTED"
    json support_assessment "SUFFICIENT or INSUFFICIENT，未達成條件"
    int supersedes_review_id FK "nullable"
    string retraction_rationale "nullable"
    datetime created_at
  }
  review_evidence {
    int review_id PK
    int evidence_id PK
    string role "CITED or CREATED"
  }
  property {
    int id PK
    int community_id FK
    string status
    int resolved_dwelling_id FK "nullable"
    int current_min_price "nullable，衍生欄位"
    int active_listing_count "衍生欄位"
    datetime first_seen_at "衍生欄位"
    int version
  }
  property_membership {
    int id PK
    int property_id FK
    int listing_id FK
    datetime valid_from
    datetime valid_to "nullable"
    int operation_id FK "nullable"
  }
  identity_match {
    int id PK
    int listing_a_id FK
    int listing_b_id FK
    string status
    int current_assessment_id FK "nullable"
    json conflicts
    int version
  }
  merge_operation {
    int id PK
    string op_type
    string actor
    string rationale
    string ruleset_version
    int identity_match_id FK "nullable"
    int reverts_operation_id FK "nullable"
    json evidence_ids "人工合併與拆分的佐證"
    datetime created_at
  }
  merge_operation_listing {
    int operation_id PK
    int listing_id PK
    int from_property_id FK
    int to_property_id FK
  }
```
> 關聯修正：第一版的「每個驗證結果最多一筆 review」已改成 `review_task ||--o{ review`；覆核可以多次、可以被取代（`supersedes_review_id`），也可以撤回（`status = RETRACTED`）。

## 4. 共用 enum（與 OpenAPI 一致）
| enum | 值 |
|------|----|
| `record_status`（名冊） | `VERIFIED`、`TRANSCRIBED_UNVERIFIED`、`INFERRED`、`HYPOTHETICAL`、`CONFLICTING`、`RETRACTED` |
| `mapping_type` | `SALES_UNIT_TO_NUMBER`、`DWELLING_TO_ADDRESS`、`SALES_UNIT_TO_BUILDING`、`SALES_UNIT_TO_UNIT_TYPE` |
| `coverage.completeness` | `COMPLETE`、`PARTIAL`、`UNKNOWN` |
| `source.priority` | `PRIMARY`、`SECONDARY` |
| `access_method` | `PUBLIC_WEB`、`AUTHORIZED_API`、`AUTHORIZED_ACCOUNT`、`MANUAL_ONLY`、`NOT_ALLOWED` |
| `rights.*` | `ALLOWED`、`RESTRICTED`、`PROHIBITED`、`UNKNOWN` |
| `doc_kind` | `LISTING_PAGE`、`LISTING_LIST_PAGE`、`LISTING_PHOTO`、`FLOOR_PLAN_IMAGE`、`LAYOUT_DIAGRAM`、`SALES_CONTROL_TABLE`、`GOV_DATASET`、`TRANSACTION_RECORD`、`USER_OBSERVATION`、`PRIOR_TRANSCRIPTION` |
| `assessment.flags` | `REGISTRY_GAP`、`MAPPING_DISPUTE`、`INTERNAL_INCONSISTENCY`、`STALE_EVIDENCE`、`PHOTO_REUSE`、`AREA_BASIS_INFERRED`、`AREA_BASIS_AMBIGUOUS`、`MAP_ADDRESS_IGNORED`、`MODEL_INFERENCE_USED`（v0.6：決策模型的推論參與了計分） |
| `fetch_run.run_type` | `BASELINE`、`SCHEDULED`、`MANUAL_TRIGGER`、`MANUAL_IMPORT` |
| `fetch_run.status` | `QUEUED`、`RUNNING`、`SUCCEEDED`、`PARTIAL`、`FAILED`、`SKIPPED`、`CANCELLED` |
| `fetch_run.coverage` | `PENDING`、`COMPLETE`、`INCOMPLETE`、`NOT_APPLICABLE` |
| `coverage_reasons[]` | `RUN_NOT_SUCCEEDED`、`PAGE_CAP_HIT`、`PAGINATION_NOT_EXHAUSTED`、`LIST_PARSE_FAILURE`、`SCOPE_CHANGED`、`COUNT_MISMATCH`、`TRUNCATED_RESULT`、`SUSPICIOUS_EMPTY`、`MANUAL_IMPORT` |
| `import_mode` | `INITIAL`、`INCREMENTAL` |
| `listing.status` | `ACTIVE`、`MISSING`、`DELISTED` |
| `external_id_origin` | `PLATFORM`、`URL_HASH`、`SYSTEM_GENERATED` |
| `deal_type` | `SALE`、`RENT`（【建議預設】MVP 只用 SALE，Q-020） |
| `first_observed_context` | `BASELINE`、`INCREMENTAL`、`MANUAL_INITIAL`、`MANUAL_INCREMENTAL` |
| `new_listing_status` | `CONFIRMED_NEW`、`NOT_NEW`、`UNCONFIRMED` |
| `cycle.start_reason` | `FIRST_OBSERVED`、`RELISTED` |
| `cycle.end_reason` | `MISSING_IN_COMPLETE_RUNS`、`MARKED_SOLD`、`MARKED_REMOVED`、`MANUAL` |
| `parking_included`、`parking_basis` | `INCLUDED`、`EXCLUDED`、`UNKNOWN` |
| `address_precision` | `FLOOR`、`NUMBER`、`LANE`、`ROAD`、`NONE` |
| `evidence.kind` | `SOURCE_FACT`、`INFERENCE`、`HUMAN_ASSERTION` |
| `evidence.origin` | `LISTING_SNAPSHOT`、`SOURCE_DOCUMENT`、`PRIOR_TRANSCRIPTION`、`USER_OBSERVATION`、`REVIEW`、`RULE` |
| `evidence.status` | `ACTIVE`、`STALE`、`SUPERSEDED`、`RETRACTED` |
| `locator.type` | `JSON_PATH`、`CSS_SELECTOR`、`TEXT_SPAN`、`IMAGE_REGION`、`TABLE_CELL`、`FREE_TEXT` |
| `evidence_link.target_type` | `COMMUNITY_MEMBERSHIP`、`CANDIDATE`、`IDENTITY_MATCH`、`REGISTRY_MAPPING`、`DERIVATION_RULE` |
| `evidence_role` | `DIRECT_ADDRESS`、`DIRECT_UNIT`、`LISTING_LINKAGE`、`FEATURE_OBSERVATION`、`OTHER` |
| `derivation_type` | `ORIGINAL`、`COPY`、`REPOST`、`CROP`、`OCR`、`TRANSCRIPTION`、`REVIEW_CITATION`、`INFERENCE` |
| `derivation.ref_type` | `EVIDENCE`、`IDENTITY_MATCH`、`REVIEW`、`ASSESSMENT`、`CANDIDATE`、`REGISTRY_MAPPING`、`DERIVATION_RULE`、`SOURCE_DOCUMENT` |
| `derivation.role` | `COPIED_FROM`、`EXTRACTED_FROM`、`CITES`、`INFERRED_FROM`、`VIA_MATCH` |
| `link_method` | `AGENT_VIEWING_WITH_LISTING_REF`、`DOCUMENT_CITES_LISTING`、`INTERIOR_FEATURE_MATCH`、`OTHER` |
| `capture.method` | `PHOTO`、`DOCUMENT`、`SITE_VISIT_NOTE`、`AGENT_STATEMENT`、`SCREEN_RECORD`、`OTHER` |
| `origin_type` | `PLATFORM_LISTING`、`AGENT_STATEMENT`、`OWNER_STATEMENT`、`USER_SITE_VISIT`、`DOCUMENT`、`SALES_CONTROL_TABLE`、`GOV_RECORD`、`PRIOR_TRANSCRIPTION`、`SYSTEM` |
| `asserting_party_type` | `AGENT`、`OWNER`、`PLATFORM`、`USER`、`GOVERNMENT`、`BUILDER`、`SYSTEM`、`UNKNOWN` |
| `origin_relation.relation` | `SAME`、`DEPENDENT`、`INDEPENDENT`、`UNKNOWN` |
| `origin_relation.basis` | `RULE_I1`、`RULE_I2`、`RULE_I3`、`HUMAN_REVIEW` |
| `support_assessment.result` | `SUFFICIENT`、`INSUFFICIENT`、`NOT_APPLICABLE` |
| `derivation_rule.scope_type` | `COMMUNITY`、`BUILDING`、`SALES_UNIT` |
| `derivation_rule.rule_type` | `SAME_NUMBER_ALL_FLOORS`、`EXPLICIT_LIST` |
| `rule_exception.exception_type` | `SHOP`、`NO_DWELLING`、`MERGED_UNIT`、`DIFFERENT_NUMBER` |
| `suppression_reason` | `BASELINE_CONTEXT`、`MANUAL_INITIAL_CONTEXT`、`NOT_CONFIRMED_NEW`、`INTERNAL_EVENT` |
| `stance` | `SUPPORTS`、`REFUTES`、`NEUTRAL` |
| `candidate.generated_by` | `G1`、`G2`、`G3` |
| `assessment.subject_type` | `COMMUNITY_MEMBERSHIP`、`ADDRESS_RESOLUTION`、`LISTING_MATCH` |
| `membership_status` | `MEMBER`、`LIKELY_MEMBER`、`NON_MEMBER`、`UNDETERMINED`、`CONFLICTING` |
| `address_status` | `CONFIRMED`、`SINGLE_CANDIDATE`、`MULTIPLE_CANDIDATES`、`INSUFFICIENT_INFO`、`NO_CANDIDATE`、`CONFLICTING`、`ABSTAINED`、`NOT_EVALUATED` |
| `match_outcome` | `AUTO_MERGE_ELIGIBLE`、`REVIEW_REQUIRED`、`CANDIDATE_ONLY`、`CONFLICTING` |
| `field_status`（resolved_fields） | `CONFIRMED`、`NARROWED`、`AMBIGUOUS`、`UNKNOWN`、`CONFLICTING` |
| `basis` | `SYSTEM_RULES`、`HUMAN_REVIEW` |
| `assessment.trigger` | `NEW_SNAPSHOT`、`EVIDENCE_CHANGED`、`REGISTRY_UPDATED`、`REVIEW`、`REVIEW_RETRACTED`、`MERGE`、`SPLIT`、`REVERT`、`RULESET_UPDATED`、`MANUAL` |
| `assessment.status` | `CURRENT`、`SUPERSEDED` |
| `review_task.task_type` | `COMMUNITY_MEMBERSHIP`、`ADDRESS_CANDIDATE`、`LISTING_MATCH`、`REGISTRY_MAPPING`、`DERIVATION_RULE` |
| `review_task.status` | `OPEN`、`RESOLVED`、`OBSOLETE` |
| `review.decision` | 歸屬：`CONFIRM_MEMBER`、`CONFIRM_NON_MEMBER`；門牌：`CONFIRM_CANDIDATE`、`REJECT_CANDIDATE`、`SET_UNIT`、`MARK_INSUFFICIENT`；配對：`CONFIRM_SAME`、`REJECT_SAME`；名冊：`VERIFY_MAPPING`、`REJECT_MAPPING`、`VERIFY_RULE`、`REJECT_RULE`；通用：`ABSTAIN` |
| `review.status` | `ACTIVE`、`SUPERSEDED`、`RETRACTED` |
| `identity_match.status` | `CANDIDATE`、`AUTO_MERGED`、`CONFIRMED`、`REJECTED`、`REVERTED` |
| `merge_operation.op_type` | `AUTO_MERGE`、`MANUAL_MERGE`、`SPLIT`、`REVERT` |
| `actor` | `SYSTEM`、`USER` |
| `property.status` | `ACTIVE`、`INACTIVE` |
| `notification.status` | `PENDING`、`SENT`、`FAILED` |
| `notification_kind` | `EVENT`、`CORRECTION` |
| `channel` | `EMAIL`、`TELEGRAM`（Q-005） |

## 5. 資料字典重點
### 5.1 證據 `evidence`
| 欄位 | 說明 |
|------|------|
| `kind` | 來源事實、推論、人工主張三者分開；推論必須有衍生紀錄（`evidence_derivation`） |
| `origin` + `snapshot_id`、`source_document_id`、`review_id` | 證據出處：刊登的哪個版本、哪份文件、哪次覆核 |
| `observed_at` | 來源內容的觀測時間（不是寫入時間） |
| `field` + `value` | 陳述的欄位與值，例如 `{"field":"floor","value":{"min":12,"max":12,"exact":true}}` |
| `locator` | 內容定位，例如 `{"type":"TEXT_SPAN","value":"title[3:7]"}`、`{"type":"IMAGE_REGION","value":"plan-4of4#x=120,y=40,w=60,h=20"}` |
| `excerpt`、`excerpt_hash` | 短摘錄（≤ 200 字，已遮罩個資）與雜湊；原始內容刪除後，可以用雜湊比對 |
| `derivation_type`、`evidence_derivation` | 這筆證據是原始觀測，還是由其他實體複製、轉載、裁切、OCR、轉錄、覆核引用或推論而來。每筆衍生都以 **`(ref_type, ref_id)` 具型別參照**記錄，不使用沒有型別的整數 ID |
| `origin_ids` | 根來源集合（見 §5.4）。`ORIGINAL` 證據會建立或指向一個 `source_origin`；其他衍生方式沿用上游的根來源；`INFERENCE` 的根來源是所有上游根來源再加上 SYSTEM |
| `evidence_role`、`capture`、`link_method` | 用於人工確認（06 §5.2）：直接門牌、直接戶別、刊登連結證據的角色、取得資訊與連結方式 |
| `content_hash` | 只作為「可能重複」的線索，**不能單獨**證明兩筆證據同源或獨立 |
| ~~`dependency_group`~~ | **v0.3 移除**。舊做法（刊登者＋標題雜湊、`user:{review_id}`）會把同一張照片的兩次覆核、改標題後的轉載誤算成獨立來源 |
| `status`、`superseded_by_id` | 證據生命週期：新快照改寫同一欄位 → 舊證據 SUPERSEDED；來源被認定錯誤 → RETRACTED；超過新鮮期限 → STALE |
| `version` | 狀態每次變更都加 1；`assessment_evidence.evidence_version` 記錄評估當時用的版本 |

**新鮮期限（【建議預設】）**：價格與狀態類證據超過 14 天沒有再觀測到 → STALE；實體屬性（樓層、坪數、格局）不會因時間變成 STALE，只會因新快照而 SUPERSEDED。

### 5.2 唯一性與索引
| 表 | 約束 | 用途 |
|----|------|------|
| `listing` | UNIQUE(`source_id`, `external_id`) | 刊登身分 |
| `listing_cycle` | UNIQUE(`listing_id`, `cycle_no`) | 上架週期 |
| `address_unit` | UNIQUE(`community_id`, `addr_key`) | 門牌去重 |
| `identity_match` | UNIQUE(`listing_a_id`, `listing_b_id`)，CHECK(a < b) | 配對唯一 |
| `listing_event` | UNIQUE(`idempotency_key`)、UNIQUE(`event_id`) | 事件冪等 |
| `notification` | UNIQUE(`alert_rule_id`, `event_id`) WHERE `notification_kind = EVENT`；UNIQUE(`event_id`, `corrects_notification_id`) WHERE `notification_kind = CORRECTION` | 不重複通知；同一次更正不重複 |
| `candidate` | CHECK(`dwelling_id` 或 `address_unit_id` 至少一個不是 null) | 候選類型 |
| `property_membership` | 每個 listing 同一時間只能有一筆 `valid_to IS NULL` | 成員唯一 |
| `idempotency_record` | PK(`api_key_id`, `method`, `route`, `key`) | API 冪等（見 08 §4） |
| `evidence_derivation` | PK(`evidence_id`, `ref_type`, `ref_id`)；INDEX(`ref_type`, `ref_id`) | 由上游反查所有依賴者（連鎖撤回） |
| `origin_relation` | UNIQUE(`origin_a_id`, `origin_b_id`) WHERE status = ACTIVE，CHECK(a < b) | 來源關係唯一 |
| `address_derivation_rule` | CHECK(`floor_min` ≤ `floor_max`) | 規則樓層範圍必填 |

### 5.3 輔助表（未畫在 ERD 中）
- `idempotency_record`：`key`、`request_hash`、`response_status`、`response_body`、`created_at`、`expires_at`。
- `ruleset`：`version`、`params_json`（權重、門檻、容差）、`created_at`。
- `calibration`：`version`、`dataset_split_id`、`method`、`created_at`（M1 之後才會有）。
- **v0.6 實作新增**（`jev/db.py`；`meta.schema_version` 目前是 5，啟動時自動備份並補上新增欄位）：
  - `decision_call`：每次呼叫外部決策模型的紀錄（`purpose`、`provider`、`model`、`endpoint`、`wire_format`、`questions`、`request_hash`、`image_count`、`status`、`http_status`、`error`、`input_tokens`、`cost_usd`、`latency_ms`、`created_at`）。不存請求內容與圖片；`cost_usd` 用於每月預算上限（D-113）。
  - `lvr_transaction`：官方實價登錄中聯上世界的成交（`serial` 唯一、`season`、`number`、`floor`、`trade_date`、`rooms`、`ping_ex_parking`、`main_ping`、`total_price`、`parking_price`、`source_file`）。
  - `alert_rule.deleted_at`：刪除規則時保留紀錄（歷史通知仍然指得到規則）。
  - 模型答案存成 `evidence`（`kind = INFERENCE`、`origin = RULE`、`field` 為 `layout.front_row`、`floor.band`、`membership.text`、`identity.same`）；使用者提供的照片只存雜湊（`field = listing_photo`、`content_hash`）。
- **尚未實作**（MVP 不需要）：`building`、`registry_coverage`、`run_observation`、`source_document`；`assessment.calibrated_probability` 在 API 一律回傳 null。

### 5.4 來源相依與獨立性（v0.3，【建議預設】）
**根來源**（`source_origin`）代表一個原始資訊出處：誰陳述（`asserting_party_type`、`asserting_party_key`）、以什麼形式（`origin_type`）、何時由誰取得。

| 衍生方式 | 根來源 |
|----------|--------|
| `ORIGINAL`（第一次觀測，例如一張現場照片、一則刊登內容） | 建立新的 source_origin（必須有 `capture` 或快照出處） |
| `COPY`、`REPOST`、`CROP`、`OCR`、`TRANSCRIPTION`、`REVIEW_CITATION` | **沿用上游**的根來源，不新增 |
| `INFERENCE` | 所有上游根來源的聯集，再加上 SYSTEM |

**兩筆證據的關係** `indep(e1, e2)`，依序判定：
1. 根來源集合有交集 → `SAME`。
2. 任一方的根來源含 SYSTEM → 不視為獨立（`DEPENDENT`）。
3. 有 ACTIVE 的 `origin_relation` 是 `SAME` 或 `DEPENDENT` → 依紀錄。
4. **規則 I2**：雙方 `asserting_party_key` 相同（例如同一家仲介在不同平台刊登，即使標題不同）→ `DEPENDENT`。
5. 有 ACTIVE 的 `origin_relation = INDEPENDENT`（人工覆核，必須附理由與佐證）→ `INDEPENDENT`。
6. **規則 I3**：`content_hash` 或 `root_content_hash` 相同或高度相似 → `UNKNOWN`（只是重複線索，需要覆核才能判定）。
7. **規則 I1（需要正面佐證）**：一方是**使用者本人的原始觀測**（`origin_type = USER_SITE_VISIT`，有 `capture`，沒有任何 `derived_from`），另一方是第三方內容，而且使用者證據不是由對方衍生；取得紀錄本身就是「獨立觀測」的正面佐證 → `INDEPENDENT`。
8. 其他情況 → `UNKNOWN`。**包括兩家不同仲介**：陳述者不同，只代表「沒有已知的相依」，不是獨立的正面佐證（仲介之間常共用屋主提供的照片與資料），所以在有 `origin_relation = INDEPENDENT`（附佐證）之前維持 `UNKNOWN`（v0.3.1 修訂，D-011）。

規則：**`UNKNOWN` 一律視為不獨立**；`review_id` 只代表操作紀錄，不是來源；同一張照片被兩次覆核引用，根來源仍然只有一個。

**連鎖撤回**：任何證據、配對、覆核、名冊 mapping 或推導規則被撤回時，沿著 `evidence_derivation`（依 `ref_type`、`ref_id` 反查）找出所有直接與間接依賴的證據，標為 `RETRACTED`（`status_reason = UPSTREAM_RETRACTED`），再用 `assessment_evidence` 與 `registry_items_used` 找出所有受影響的評估並重新評估。

**不得升級**：人工主張如果只引用推論、候選清單或評估（`ref_type` 是 `ASSESSMENT`、`CANDIDATE`，或上游是 INFERENCE），根來源仍然含有 SYSTEM，不能當成決定性證據（06 §5.1 K3、§5.2 H4）。

### 5.5 門牌推導規則 `address_derivation_rule`（v0.3，【建議預設】）
| 欄位 | 說明 |
|------|------|
| `scope_type`、`scope_ids` | 適用範圍：整個社區、指定棟別，或指定戶別 |
| `floor_min`、`floor_max` | 適用的樓層區間（必填）；**不能**因為知道總樓高就自動填成 1–總樓層 |
| `rule_type` | `SAME_NUMBER_ALL_FLOORS`：門牌 =「戶別→號」+ `floor_label_format`（例如「{floor}樓」）；`EXPLICIT_LIST`：沒有通則，只能依 `DWELLING_TO_ADDRESS` 逐戶列舉 |
| `exceptions` | 陣列；每筆有 `floor_min`、`floor_max`、`sales_unit_ids`（空陣列 = 範圍內全部）、`exception_type`（`SHOP`、`NO_DWELLING`、`MERGED_UNIT`、`DIFFERENT_NUMBER`）、`override_number`（只有 DIFFERENT_NUMBER 才有）、`note` |
| `record_status` | 與名冊相同的 enum；只有 VERIFIED，而且適用於該戶別與樓層（不在例外內）的規則，才能支持系統確認（06 §4.1a） |
| `supersedes_rule_id`、`version` | 修改 = 新增一條規則取代舊規則；舊規則保留 |
| 證據 | 透過 `evidence_link(target_type = DERIVATION_RULE)` 連結；覆核任務 `DERIVATION_RULE` |

規則狀態變更 → 新的 `registry_version` → `REGISTRY_UPDATED` 事件 → 重新評估 `registry_items_used` 中含有該規則的評估，以及範圍內的戶別 × 樓層候選。

`registry_coverage` 維持只記錄**完整度**；`dimension = FLOOR` 代表「哪些樓層有住宅」這份清單的完整度，不代表推導規則正確。

## 6. 原始資料刪除與可追溯性
| 刪除後**保留** | 刪除後**無法再做到** |
|----------------|-----------------------|
| 結構化欄位（快照欄位、證據 `value`） | 用新版 parser 重新抽取原始內容 |
| 證據的 `locator`、`excerpt`（已遮罩）、`excerpt_hash`、`content_hash` | 驗證摘錄以外的原文內容 |
| `source_document` 的網址、取得時間、內容雜湊、權利快照 | 重新檢視圖片（除非權利允許保存圖片） |
| 評估、候選分數、使用的證據集合與版本 | 對已刪除內容做新的人工判讀 |
| 通知內容快照、事件 payload | — |

刪除原始內容時：`source_document.raw_ref = null`、填入 `raw_deleted_at`。依賴這份文件的證據**不改狀態**（證據值仍然有效），但 API 會回傳 `raw_available = false`。評估在相同規則與證據下仍然可以重現；**重新抽取**則無法重現。

## 7. 個資
- 不保存 `lister_name`、`lister_phone`（Q-015）；摘錄要先遮罩電話模式。
- 使用者提供的 USER_OBSERVATION（例如現場照片）只存描述與雜湊；要不要保存檔案，【待確認】。
