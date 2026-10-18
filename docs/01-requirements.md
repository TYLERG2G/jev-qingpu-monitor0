# 01 需求規格與追溯

## 1. 背景
見 [專案背景](project/background.md)。JEV 是專案代稱；試點社區是桃園市大園區青埔「聯上世界」【已確認】。

## 2. 目標【已確認】
- G1 房源監控：追蹤試點社區的刊登，並辨識首次觀測、確認新刊登、同一住宅的新刊登、重新上架、價格與欄位變化、下架。
- G2 跨刊登同屋辨識：把指向同一間實體住宅的刊登歸成一組；證據不足時不自動合併；可以拆分與撤回。
- G3 完整門牌推斷：推斷完整門牌，並附證據與可信度；支援多候選、部分確認、資訊不足、證據衝突、人工確認與棄答。

## 3. 範圍
| 項目 | 標記 | 內容 |
|------|------|------|
| 本階段產出 | 【已確認】 | 只有規劃文件、圖表、API 與事件契約 |
| 試點社區 | 【已確認】 | 聯上世界（第一個試點社區） |
| 社區數 | 【建議預設】 | MVP 只做 1 個社區 |
| 優先來源 | 【已確認】 | 591、樂居 |
| 交易類型 | 【建議預設】 | 中古屋買賣（Q-020） |
| 使用者 | 【建議預設】 | 單一使用者、個人非商業（Q-021） |
| 介面 | 【建議預設】 | CLI、本機 REST API、通知（Q-012、Q-014） |
| 部署 | 【建議預設】 | 本機 Windows + SQLite（Q-006） |

### 非目標（MVP，【建議預設】）
估價模型、投資建議；主動聯繫刊登者或平台；繞過任何存取限制；多社區與多使用者權限；店面與單獨車位（Q-016）。

## 4. 功能需求
| ID | 需求 | 優先 | 依據 |
|----|------|------|------|
| FR-01 | 建立並核對名冊：棟別、坪型、銷售戶別、住宅、門牌**分開表示**；每筆 mapping 都有狀態、證據與版本；記錄涵蓋度 | Must | D-006、D-007 |
| FR-02 | 人工記錄刊登：明確的識別規則（含沒有網址與編號的情況）；記錄原始出處 | Must | D-006 |
| FR-03 | 自動擷取：依來源權利與條件啟用，預設關閉 | Should | D-005 |
| FR-04 | 正規化：價格、面積口徑、樓層區間（模糊樓層不轉成確定樓層）、地址 | Must | D-007 |
| FR-05 | 社區歸屬判定（與門牌判定分開）；名冊缺少紀錄不等於不屬於社區 | Must | D-007 |
| FR-06 | 實體住宅與完整門牌：候選生成、排除、排序、逐欄位部分確認、確認條件、棄答 | Must | D-003、D-007 |
| FR-07 | 證據模型：種類（事實、推論、人工）、出處與版本、定位、支持或反駁的標的與欄位、獨立性、生命週期 | Must | D-009 |
| FR-08 | 人工覆核：可以多次、可以撤回；必須附理由與佐證；有版本檢查 | Must | D-009 |
| FR-09 | 同屋配對候選與合併治理：自動合併的必要條件、衝突條件、人工確認；不無條件傳播門牌 | Must | D-008 |
| FR-10 | 拆分、撤回合併、重新評估（MVP），並重新計算衍生結果 | Must | D-008 |
| FR-11 | 監控生命週期：完整輪次、上架週期、四種「新」、初始化不通知、人工增量匯入不影響其他刊登 | Must | D-010 |
| FR-12 | 價格與欄位變化事件 | Must | D-003 |
| FR-13 | 事件契約：依類型定義 payload、事件 ID、版本、冪等 | Must | D-010 |
| FR-14 | 通知規則、通知紀錄與更正通知；歷史通知可追溯 | Must | D-008 |
| FR-15 | API 一致性：Idempotency-Key、If-Match 版本檢查、Merge Patch 語意、條件必填 | Must | — |
| FR-16 | 來源權利紀錄：取得、保存、圖片利用、再發布 | Must | D-005 |
| FR-17 | 原始資料刪除後的可追溯性 | Must | D-009 |
| FR-18 | 匯出物件與價格歷史 | Could | — |
| FR-19 | 健康狀態與來源告警 | Should | — |
| FR-20 | 門牌推導規則：與涵蓋度分開記錄；有範圍、樓層區間、例外、狀態、版本、證據；可以覆核、撤回，並觸發重新評估（v0.3，【建議預設】設計） | Must | 第二輪審閱 |
| FR-21 | 人工確認完整門牌的充分性判定（H1–H6）、逐欄位確認狀態、撤回後依剩餘佐證重新評估（v0.3，【建議預設】設計） | Must | 第二輪審閱 |
| FR-22 | 來源相依與獨立性：根來源、具型別衍生參照、關係判定（UNKNOWN 不算獨立）、連鎖撤回（v0.3，【建議預設】設計） | Must | 第二輪審閱 |
| FR-23 | 事件保存與通知分開；初始化資料不當成新案；同屋關係與真正新增刊登分開（v0.3，【建議預設】設計） | Must | D-010、第二輪審閱 |

## 5. 非功能需求（【建議預設】；數值是【建議目標】）
| ID | 類別 | 需求 |
|----|------|------|
| NFR-01 | 合規 | 自動擷取預設關閉；啟用必須符合 03 §5 |
| NFR-02 | 禮貌存取 | 單一連線；請求間隔 ≥ 10 秒；遇到 403 或 CAPTCHA 立即停用 |
| NFR-03 | 即時性 | 從一輪結束到通知 ≤ 15 分鐘【建議目標】 |
| NFR-04 | 正確性 | 門牌「已作答」（CONFIRMED）的正確率、覆蓋率、棄答率分開報告；目標值是【建議目標】，要在評估集上驗證（10 §6） |
| NFR-05 | 可追溯 | 每個事件與評估都能追溯到證據、版本與操作者 |
| NFR-06 | 個資 | 不保存刊登者姓名與電話（Q-015） |
| NFR-07 | 可維運 | 單機運作；備份就是複製一個檔案 |
| NFR-08 | 可擴充 | 保留 `community_id`、`deal_type` |
| NFR-09 | 決定性 | 相同規則版本、名冊版本、證據集合 → 相同評估結果 |

## 6. 驗收標準（MVP）
| AC | 內容 | 測試 |
|----|------|------|
| AC-01 | 匯入 17 筆轉錄對照後，mapping 全部是 `TRANSCRIBED_UNVERIFIED`；任何刊登的門牌狀態都不會是 `CONFIRMED` | T-REG-01、T-ADDR-07 |
| AC-02 | `SRC-MANUAL` 沒有網址也沒有編號 → 產生 `man_` ID；同一個 Idempotency-Key 重送 → 回傳同一筆；平台來源兩者都缺 → 422 | T-API-01～03 |
| AC-03 | 名冊沒有該號碼、涵蓋度不完整 → 不判 NON_MEMBER，並設 `REGISTRY_GAP` | T-MEM-04 |
| AC-04 | 同樓不同戶（B1、B2、B3 的 12 樓）→ MULTIPLE_CANDIDATES，`floor = NARROWED` | T-ADDR-03 |
| AC-05 | 抄錄自同一刊登者的兩則刊登，不算兩個獨立來源 | T-EVD-01 |
| AC-06 | 用過期的任務版本送出覆核 → 412；確認類決定沒有佐證 → 422；撤回覆核 → 重新評估 | T-REV-01～04 |
| AC-07 | 同樓不同戶、坪型相同 → 不自動合併，只建立配對候選或覆核任務 | T-IDN-01 |
| AC-08 | 拆分或撤回合併 → 依賴的推論證據被撤回、門牌重新評估、價格彙總重新計算、歷史通知不變 | T-IDN-04～06 |
| AC-09 | PARTIAL 或撞到頁數上限的輪次 → 不累加未出現次數；連續 2 個完整輪次缺席 → DELISTED | T-LIFE-03～05 |
| AC-10 | 初始化匯入 → 不發 NEW_CONFIRMED；人工增量匯入 → 其他刊登不會下架 | T-LIFE-01、T-LIFE-06 |
| AC-11 | 降價 → `LISTING_PRICE_CHANGED(direction = DOWN)` | T-LIFE-08 |
| AC-12 | 同一輪重跑 → 不產生重複事件；下架、重新上架、再下架 → 冪等鍵各不相同 | T-EVT-01、T-EVT-02 |
| AC-13 | 同一事件不重複通知；拆分後發出更正通知，引用原本的通知 | T-NTF-01、T-NTF-02 |
| AC-14 | Merge Patch：省略、null、不可為 null 的欄位行為正確 | T-API-05 |
| AC-15 | 來源權利是 UNKNOWN 時，無法啟用自動擷取（409） | T-RUN-04 |
| AC-16 | 刪除原始頁面後，評估仍然可以重現；API 顯示 `raw_available = false` | T-RET-01 |
| AC-17 | 只有已驗證、而且適用於該戶別與樓層的推導規則，才能支持系統確認；沒有規則時不依總樓高推定樓層 | T-RULE-01～06 |
| AC-18 | 人工確認需要直接門牌證據＋刊登連結證據；只填地址、只有照片、只按確認都不充分；撤回後依剩餘佐證重新評估 | T-ADDR-09a～09g、T-ADDR-11a～11c |
| AC-19 | 複製、轉載、OCR、再次覆核不增加獨立來源數；UNKNOWN 不算獨立；撤回會連鎖追蹤 | T-EVD-06～12 |
| AC-20 | 初始化資料（BASELINE、MANUAL_INITIAL）即使延遲合併也不發新案通知；拆分、撤回後再合併不重發新案，只發更正通知；新案去重不依賴可變的群組 anchor；真正新增刊登正常通知 | T-LIFE-13～20 |

## 7. 追溯矩陣（需求 → 資料模型 → API／事件 → 驗收與測試）
| FR | 資料表（05） | API（openapi） | 事件（12） | AC / 測試（10） |
|----|--------------|----------------|------------|------------------|
| FR-01 | building、unit_type、sales_unit、dwelling、address_unit、registry_mapping、registry_version、registry_coverage | `GET /registry/mappings`、`GET /registry/coverage`、`POST /registry:import`、覆核 `REGISTRY_MAPPING` | — | AC-01；T-REG-* |
| FR-02 | listing、listing_snapshot、source_document、evidence | `POST /listings`、`POST /listings:import`、`POST /listings/{listing_id}/snapshots` | LISTING_FIRST_OBSERVED | AC-02；T-API-01～03 |
| FR-03 | source、fetch_run、run_observation | `PATCH /sources/{source_id}`、`POST /sources/{source_id}/runs` | SOURCE_HEALTH_CHANGED | AC-15；T-RUN-* |
| FR-04 | listing_snapshot（floor_min、floor_max、floor_exact、parking_included） | `POST /addresses:normalize` | — | T-NORM-* |
| FR-05 | assessment（COMMUNITY_MEMBERSHIP）、evidence_link、registry_coverage | `GET /listings/{listing_id}/assessments` | COMMUNITY_MEMBERSHIP_CHANGED | AC-03；T-MEM-* |
| FR-06 | candidate、assessment（ADDRESS_RESOLUTION）、assessment_candidate | `GET /listings/{listing_id}/candidates`、`GET /assessments/{assessment_id}` | ADDRESS_ASSESSMENT_CHANGED | AC-01、AC-04；T-ADDR-* |
| FR-07 | evidence、evidence_link、assessment_evidence | `GET /evidence/{evidence_id}`、`POST /evidence`、`POST /evidence/{evidence_id}:retract`、`GET /listings/{listing_id}/evidence` | 評估變更事件 | AC-05；T-EVD-* |
| FR-08 | review_task、review、review_evidence | `GET /review-tasks`、`POST /review-tasks/{task_id}/decisions`、`POST /reviews/{review_id}:retract` | 評估變更事件 | AC-06；T-REV-* |
| FR-09 | identity_match、property、property_membership、merge_operation、merge_operation_listing | `GET /identity-matches`、`POST /properties:merge` | PROPERTY_MERGED、DWELLING_NEW_LISTING | AC-07；T-IDN-01～03 |
| FR-10 | merge_operation、property_membership、assessment | `POST /properties/{property_id}:split`、`POST /merge-operations/{operation_id}:revert`、`POST /assessments:reassess` | PROPERTY_SPLIT、MERGE_REVERTED | AC-08；T-IDN-04～06 |
| FR-11 | fetch_run（coverage）、run_observation、listing_cycle、listing | `POST /listings/{listing_id}:mark-delisted`、`GET /sources/{source_id}/runs` | LISTING_NEW_CONFIRMED、LISTING_RELISTED、LISTING_DELISTED | AC-09、AC-10；T-LIFE-01～07 |
| FR-12 | listing_snapshot、listing_event | `GET /events` | LISTING_PRICE_CHANGED、LISTING_FIELDS_CHANGED | AC-11；T-LIFE-08 |
| FR-13 | listing_event | `GET /events` | 全部 | AC-12；T-EVT-* |
| FR-14 | alert_rule、notification | `POST /alert-rules`、`PATCH /alert-rules/{rule_id}`、`GET /notifications` | — | AC-13；T-NTF-* |
| FR-15 | idempotency_record、各表 `version` | 所有寫入操作 | — | AC-02、AC-06、AC-14；T-API-* |
| FR-16 | source.rights、source_document | `GET /sources`、`PATCH /sources/{source_id}` | — | AC-15；T-RUN-04 |
| FR-17 | source_document（raw_ref、raw_deleted_at）、evidence（excerpt_hash） | `GET /evidence/{evidence_id}`（raw_available） | — | AC-16；T-RET-01 |
| FR-18 | property、listing_snapshot | `GET /properties:export` | — | T-EXP-01 |
| FR-19 | fetch_run、source | `GET /health` | SOURCE_HEALTH_CHANGED | T-RUN-03 |
| FR-20 | address_derivation_rule、registry_version、evidence_link（DERIVATION_RULE）、assessment.registry_items_used | `GET /registry/derivation-rules`、`POST /registry/derivation-rules`、`POST /registry/derivation-rules/{rule_id}:retract`、覆核 `DERIVATION_RULE` | REGISTRY_UPDATED、ADDRESS_ASSESSMENT_CHANGED | AC-17；T-RULE-*；C-DR-* |
| FR-21 | review.support_assessment、evidence（evidence_role、capture、link_method）、assessment.resolved_fields（basis、evidence_ids） | `POST /review-tasks/{task_id}/decisions`、`POST /reviews/{review_id}:retract` | ADDRESS_ASSESSMENT_CHANGED | AC-18；T-ADDR-09*、T-ADDR-11*；C-RD-01、C-RD-10、C-UE-*、C-SA-*、C-FRS-*、C-AS-03 |
| FR-22 | source_origin、origin_relation、evidence_derivation、evidence（derivation_type、origin_ids） | `POST /evidence:independence`、`GET /evidence/{evidence_id}/dependents`、`GET /source-origins/{origin_id}`、`POST /origin-relations`、`POST /origin-relations/{relation_id}:retract` | 評估變更事件 | AC-19；T-EVD-06～12；C-EV-*、C-OR-*、C-IR-* |
| FR-23 | listing_event（notify_eligible、suppression_reason）、listing（first_observed_context、new_listing_status） | `GET /events` | DWELLING_NEW_LISTING、PROPERTY_MERGED、LISTING_FIRST_OBSERVED | AC-20；T-LIFE-13～20；C-EVT-03、C-EVT-08、C-EVT-21～26、C-NT-* |
