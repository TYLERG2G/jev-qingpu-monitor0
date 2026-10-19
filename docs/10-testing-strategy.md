# 10 測試、驗收與評估

> **v0.6 更新**：MVP 已實作，120 個案例中 77 個已有自動測試並通過，其餘的分類見 [17 §5](17-implementation-status.md#5-測試結果)。以下保留原始規劃內容。本文件定義測試案例、預期結果，以及預期結果是由哪些規則推導出來的。所有數值目標都是【建議目標】。
> 測試不連外網；解析器只用本機 fixture。

**兩種驗證要分清楚**（v0.3）：
| 類型 | 內容 | 本階段狀態 |
|------|------|------------|
| Schema 驗證 | `api/examples/contract-cases.yaml` 的正例與反例是否符合 OpenAPI schema（結構、必填、條件必填、const） | **已離線執行**（見 15）。只代表「格式正確」 |
| 業務行為測試 | §3 的 T-* 案例：規則推導、狀態轉移、冪等、通知、連鎖撤回 | **v0.6 已執行 77 個**（`tests/`，69 個 pytest 測試全部通過）；其餘見 17 §5 |

例如 C-RD-10 在 schema 上合法，但依業務規則（T-ADDR-09b）佐證不充分；兩者並不矛盾。

## 1. 測試層級
| 層級 | 範圍 | 工具（【建議預設】） |
|------|------|----------------------|
| 單元 | 正規化、規則計分、候選排除、生命週期判定 | pytest，表格驅動 |
| 元件 | parser 對 fixture 頁面 | pytest + `tests/fixtures/<source>/` |
| 整合 | 匯入 → 評估 → 事件 → 通知（假的 Notifier）、合併與拆分 | pytest + 暫存 SQLite |
| 契約 | `api/openapi.yaml` 與 `api/examples/contract-cases.yaml` | jsonschema（本階段已做離線驗證）；實作後加上 schemathesis（只打本機） |
| 評估 | 門牌、歸屬、同屋的準確度 | 依 §6 的資料切分 |

## 2. 測試名冊 fixture（虛構，只供測試）
| 項目 | 內容 |
|------|------|
| sales_unit | 17 筆：A1–A3、A5、B1–B3、B5、C1–C3、C5、D1–D3、D5、D6（取自轉錄對照） |
| 號碼 mapping | 依測試設定成 `TRANSCRIBED_UNVERIFIED`（預設）或 `VERIFIED` |
| unit_type（fixture 中設為 VERIFIED） | T45：B1、B2、B3，權狀 45.0–45.4 坪，3/2/2；T38：A 區、C 區，38.0–38.5 坪，2/2/1；T52：D 區，52.0–52.6 坪，4/2/2 |
| building | 不設定（`building_id = null`） |
| 門牌推導規則 R-ALL（v0.3） | 範圍 COMMUNITY、1–24 樓、`SAME_NUMBER_ALL_FLOORS`、`{floor}樓`；例外：1 樓全部戶別為 `SHOP`（虛構，見 C-DR-01）。`record_status` 依測試設定，預設 `TRANSCRIBED_UNVERIFIED` |
| 樓層 | **只**由適用規則的範圍（扣除例外）或刊登自己的樓層事實提供；沒有規則時不推定樓層（不因總樓高推定） |
| 地址涵蓋度 | 高鐵南路一段 77–109 單號：預設 `PARTIAL` |
| 刊登者 | 仲介 X、Y 是不同公司（`asserting_party_key` 已知）；「未知刊登者」的 key 為 null |

## 3. 測試案例
### 3.1 正規化與名冊
| ID | 情境 | 預期 | 依據 |
|----|------|------|------|
| T-NORM-01～07 | 全形、中文數字、`12F`、`79-1號`、臺/台、空白、缺號 | 正規化欄位與 `addr_key` 正確 | 02 §4 |
| T-NORM-08 | `中樓層` | `floor_min = floor_max = null`、`floor_exact = false` | 02 §3 |
| T-NORM-09 | `10~15樓` | `10`、`15`、`false` | 02 §3 |
| T-NORM-10 | `頂樓`，總樓層已知 | 換算結果只產生 INFERENCE 證據；`floor_exact = false` | 02 §3 |
| T-REG-01 | 匯入 17 筆轉錄對照 | 全部是 `TRANSCRIBED_UNVERIFIED`，證據 origin 是 `PRIOR_TRANSCRIPTION`；地址涵蓋度是 PARTIAL 或 UNKNOWN | source-log §2.1 |
| T-REG-02 | `VERIFY_MAPPING`（附證據） | mapping 變成 VERIFIED；產生新的 registry_version；受影響刊登重新評估 | 06 §8 |
| T-REG-03 | `REJECT_MAPPING` | mapping 變成 RETRACTED；依賴的候選重新評估 | 06 §6 |

### 3.2 社區歸屬（06 §3）
| ID | 證據 | 推導 | 預期 |
|----|------|------|------|
| T-MEM-01 | 平台社區標記＋名稱＋路段 | 0.5 + 0.2 + 0.2 = 0.9 | `MEMBER` |
| T-MEM-02 | 名稱＋路段 | 0.2 + 0.2 = 0.4 | `UNDETERMINED` |
| T-MEM-03 | 平台標記＋路段＋「近聯上世界」 | 0.5 + 0.2 − 0.3 = 0.4（M3 不計） | `UNDETERMINED` |
| T-MEM-04 | 完整門牌「高鐵南路一段111號」（名冊沒有），涵蓋度 PARTIAL；平台標記＋名稱 | N5 不扣分；0.5 + 0.2 = 0.7 | `LIKELY_MEMBER`，`flags ∋ REGISTRY_GAP` |
| T-MEM-05 | 同 T-MEM-04，但涵蓋度 COMPLETE | N5 決定性反駁，沒有決定性支持 | `NON_MEMBER` |
| T-MEM-06 | 「79號12樓」，mapping VERIFIED；另一個獨立來源明寫不同路段 | M1 與 N1 都是決定性 | `CONFLICTING` |
| T-MEM-07 | 「79號12樓」，mapping 未核對；名稱 | M1u 0.6 + M3 0.2 = 0.8 | `MEMBER`（以分數判定，不代表門牌已確認） |

### 3.3 完整門牌（06 §4–5；歸屬皆為 MEMBER）
| ID | 刊登事實 | 推導 | 預期 |
|----|----------|------|------|
| T-ADDR-01 | 仲介 X 的刊登明寫「79號12樓」；17 筆號碼 mapping 與 R-ALL 都是 VERIFIED | G2（floor_exact = 12）→ 17 個 12 樓候選；每個候選的推導都已驗證（06 §4.1a (b)）→ X5 排除其他 16 個；K1 ✓、K2 ✓、K3（S1，此刊登自己的事實，根來源是仲介 X，不含 SYSTEM）✓、K4 ✓、K5 ✓ | `CONFIRMED`（SYSTEM_RULES） |
| T-ADDR-02 | 同上，但號碼 mapping 是 TRANSCRIBED_UNVERIFIED | 推導未驗證 → X5 改成軟反駁；A2：0.6 + 0.1 = 0.7；其他：−0.3 + 0.1 → 0 | `MULTIPLE_CANDIDATES`，排名第一是 A2-12F；領先 ≥ 0.30 → 建立覆核任務 |
| T-ADDR-03 | 同樓不同戶：`12F` 精確、45.2 坪、3/2/2 | G2 → 17；X6 排除 T38、T52 → 剩 B1、B2、B3；每個 0.15 + 0.1 + 0.1 = 0.35 | `MULTIPLE_CANDIDATES`；floor = NARROWED(12)；sales_unit、full_address = AMBIGUOUS（對應 C-AS-01） |
| T-ADDR-04 | `中樓層`、45.2 坪；R-ALL 存在（任何非 RETRACTED 狀態） | G2 取 R-ALL 範圍 1–24，扣除 1 樓 SHOP 例外 → 2–24；X6 之後剩 3×23 = 69 > 40（G4） | `INSUFFICIENT_INFO`；floor = UNKNOWN（模糊樓層不能轉成確定樓層） |
| T-ADDR-05 | `10~15樓`、45.2 坪 | 3×6 = 18 個候選 | `MULTIPLE_CANDIDATES`；floor = AMBIGUOUS |
| T-ADDR-06a | 同一快照：標題寫 12F、樓層欄位寫 15F | 兩者根來源相同 → 關係 SAME → 內部不一致，採用欄位值 15 | 不是 CONFLICTING；`flags ∋ INTERNAL_INCONSISTENCY` |
| T-ADDR-06b | 刊登 `12F` ＋ 使用者證據「實際是 15 樓」 | 根來源不同（仲介 vs. 使用者）→ 不是 SAME → 06 §4.4 衝突 | `CONFLICTING`，進入覆核 |
| T-ADDR-07 | 戶別「A2」＋ `12F`；mapping 未核對 | X4、X1 → 剩 1；K2 不成立 | `SINGLE_CANDIDATE`；sales_unit、floor、full_address = NARROWED |
| T-ADDR-08 | 戶別「A4」（名冊沒有）；涵蓋度 PARTIAL | X4 排除全部 → 0 | `NO_CANDIDATE(REGISTRY_INCOMPLETE)`；歸屬不變 |
| T-ADDR-10 | 對 T-ADDR-03 送出 `ABSTAIN` | — | `ABSTAINED` |

### 3.3a 人工確認完整門牌（06 §5.2–5.3；基準 = T-ADDR-07：SINGLE_CANDIDATE A2-12F，mapping 與 R-ALL 都未驗證）
> 取代第一版的 T-ADDR-09（只附照片描述就 CONFIRMED，與「名冊核對前不產生 CONFIRMED」矛盾）與 T-ADDR-11（撤回後固定回到 SINGLE_CANDIDATE）。

| ID | 佐證 | 推導 | 預期 |
|----|------|------|------|
| T-ADDR-09a | `SET_UNIT(79號12樓)`＋現場門牌照片（DIRECT_ADDRESS、ORIGINAL、有 capture 與雜湊）＋帶看紀錄（LISTING_LINKAGE、`AGENT_VIEWING_WITH_LISTING_REF`，看到的刊登編號 = 此刊登） | H1 ✓、H2 ✓、H3 ✓、H4 ✓（根來源是使用者的兩次原始觀測，不含 SYSTEM）、H5 ✓（79號12樓 與 12F 相容）、H6 ✓ | **直接證據充分**：`support = SUFFICIENT`；outcome `CONFIRMED`（HUMAN_REVIEW）；full_address、address_number、floor = CONFIRMED（HUMAN_REVIEW）；**sales_unit = NARROWED**（mapping 未驗證，也沒有 DIRECT_UNIT）；building = UNKNOWN（對應 C-RD-01、C-AS-03） |
| T-ADDR-09b | `SET_UNIT` 只附使用者輸入的地址文字（role = OTHER） | H1 ✗（不是 DIRECT_ADDRESS）、H2 ✗ | **只填地址不充分**：`INSUFFICIENT`，unmet = [H1, H2]；outcome 維持 `SINGLE_CANDIDATE`；任務保持 OPEN；覆核與證據紀錄保留（對應 C-RD-10） |
| T-ADDR-09c | 門牌照片（H1 成立），但沒有任何刊登連結證據 | H2 ✗ | **無法連結刊登**：`INSUFFICIENT`，unmet = [H2]；outcome 維持 `SINGLE_CANDIDATE`（對應 C-SA-01） |
| T-ADDR-09d | 門牌照片＋連結證據，但 `link_method = OTHER`（「感覺很像」） | H2 ✗ | `INSUFFICIENT`，unmet = [H2] |
| T-ADDR-09e | `CONFIRM_CANDIDATE`，只引用系統候選清單與 S8 推論（REVIEW_CITATION → INFERENCE） | 根來源含 SYSTEM → H4 ✗；也沒有原始的直接門牌與連結證據 → H1 ✗、H2 ✗ | **重新包裝推論不能升級**：`INSUFFICIENT`，unmet = [H1, H2, H4] |
| T-ADDR-09f | 照片顯示「79號12樓」，但此刊登的 `floor_exact` 是 15 | H5 ✗（與 X1 事實不相容）；刊登的 15F 事實反駁 → H6 ✗ | `INSUFFICIENT`，unmet = [H5, H6]；outcome `CONFLICTING`（06 §4.4） |
| T-ADDR-09g | 同 T-ADDR-09a，而且 mapping 未驗證 | 人工確認不需要 mapping 已驗證 | **mapping 未驗證仍可確認完整門牌**；但 sales_unit、building 不會因此變成 CONFIRMED |
| T-ADDR-11a | 有兩組充分佐證：R1（照片＋帶看）與 R2（一份同時載明刊登編號與門牌的文件，`DOCUMENT_CITES_LISTING`，兩個定位）；撤回 R1 | R1 建立的證據 RETRACTED → 用剩餘 ACTIVE 證據重新套用 §5.1、§5.2：R2 仍然 H1–H6 成立 | **撤回後仍有其他佐證**：維持 `CONFIRMED`（HUMAN_REVIEW）；full_address 的 `evidence_ids` 改成 R2 的證據；發出 ADDRESS_ASSESSMENT_CHANGED（佐證集合改變） |
| T-ADDR-11b | 只有 R1；撤回 R1 | 沒有充分佐證 → 規則重新推導：T-ADDR-07 的事實 → 剩 1 個、K2 ✗ | `SINGLE_CANDIDATE`（由規則推出，不是固定的降級狀態） |
| T-ADDR-11c | 只撤回 R1 的帶看證據（照片仍然 ACTIVE） | H2 ✗ | R1 的 `support_assessment` 重新判定為 INSUFFICIENT [H2]；outcome 由規則推出 → `SINGLE_CANDIDATE` |

### 3.3b 門牌推導規則（06 §4.1a；05 §5.5）
| ID | 情境 | 推導 | 預期 |
|----|------|------|------|
| T-RULE-01 | 沒有任何推導規則；刊登只寫 `中樓層`；已知該棟總樓高 24 | G2：沒有 floor_exact、沒有區間、沒有規則 → 不生成 | `INSUFFICIENT_INFO`（**不因總樓高推定每層的戶別與門牌**） |
| T-RULE-02 | 號碼 mapping VERIFIED，但 R-ALL 是 TRANSCRIBED_UNVERIFIED；刊登「79號12樓」 | 推導未驗證 → X5 軟反駁 → 同 T-ADDR-02 的分數 | `MULTIPLE_CANDIDATES`；K2 ✗（規則未驗證，不能支持系統確認） |
| T-RULE-03 | R-ALL 與 mapping 都 VERIFIED；刊登「79號1樓」（1 樓是 SHOP 例外） | G2 → 17 個 1 樓候選；X7 排除全部；G3 加入一筆只有門牌的候選（名冊沒有 79號1樓）→ 剩 1；該候選沒有已驗證的推導 → K2 ✗ | `SINGLE_CANDIDATE`，`flags ∋ REGISTRY_GAP`；不會把店面樓層當成住宅候選 |
| T-RULE-04 | 在 T-ADDR-01 CONFIRMED 之後撤回 R-ALL | 新 registry_version → `REGISTRY_UPDATED`（C-EVT-20）→ `registry_items_used` 含 R-ALL 的評估重新計算 → 推導未驗證 → 同 T-ADDR-02 | `MULTIPLE_CANDIDATES`；發出 ADDRESS_ASSESSMENT_CHANGED（trigger = REGISTRY_UPDATED） |
| T-RULE-05 | R-ALL 與另一條 VERIFIED 規則（範圍 B2、10–15 樓、12 樓 `DIFFERENT_NUMBER` 88）同時適用 B2-12F | 兩條規則結果不同 → 推導 CONFLICTING | K2 ✗；`flags ∋ MAPPING_DISPUTE`；建立 `DERIVATION_RULE` 覆核任務 |
| T-RULE-06 | 對 R-ALL 送出 `VERIFY_RULE`（附佐證） | 規則變成 VERIFIED → 新 registry_version → 重新評估 | 範圍內符合 K1–K5 的刊登可以變成 CONFIRMED（SYSTEM_RULES） |
| T-RULE-07 | 規則沒有樓層範圍、或 DIFFERENT_NUMBER 沒有 override | schema 拒絕 | C-DR-02、C-DR-04 |

### 3.3c 坪數與照片格局比對（16；v0.5）
Fixture（虛構，只供測試）：坪型都設為 VERIFIED、車位口徑已知；C3 30.0 坪；D2 36.1 坪（mirror = L）；D5 36.1 坪（mirror = R）；其他坪型與 C3、D2、D5 的距離都超過 1.0 坪。規則 R-3TO14 存在（HYPOTHETICAL，3–14 樓）；C3 在 2F 也存在。

| ID | 情境 | 推導 | 預期 |
|----|------|------|------|
| T-PHOTO-01 | 12F、扣除車位 30.0 坪 | G2 → 17 個 12 樓候選；X6 排除 C3 以外的全部 | `SINGLE_CANDIDATE`（C3-12F）；sales_unit = NARROWED；K2 不成立（推導未驗證），所以不是 CONFIRMED |
| T-PHOTO-02 | 12F、36.1 坪；照片顯示鏡像 R | X6 → 剩 D2、D5；D5：S3 0.15 + S7 0.1 + S9 0.1 = 0.35；D2：0.15 + 0.1 − 0.3（S10）→ 0 | `MULTIPLE_CANDIDATES`，D5 排第一、領先 0.35 ≥ 0.30 → 建立覆核任務；**D2 不會被硬排除** |
| T-PHOTO-03 | 同 T-PHOTO-02，但那張照片也出現在另一個社區的刊登（雜湊相同） | I3 → UNKNOWN；`flags ∋ PHOTO_REUSE`；S9、S10 不計分 | D2、D5 都是 0.25 → `MULTIPLE_CANDIDATES`，沒有領先者 |
| T-PHOTO-04 | 沒有樓層；30.0 坪 | G2 依 R-3TO14（3–14）加上 C3 的 2F → 13 個候選（≤ 40） | `MULTIPLE_CANDIDATES`；sales_unit = NARROWED（C3）；floor = AMBIGUOUS（**照片只辨識到戶別**） |
| T-PHOTO-05 | 對 T-PHOTO-01 送出 `CONFIRM_CANDIDATE`，佐證只有照片格局比對（FEATURE_OBSERVATION） | H1 ✗（不是直接門牌證據）、H2 ✗ | `INSUFFICIENT`；sales_unit 維持 NARROWED（不是 DIRECT_UNIT） |
| T-PHOTO-06 | 階段 2 模型輸出 `model_score = 0.92`，指向 D5 | INFERENCE 證據，權重上限等同 S9 +0.3；`calibrated_probability = null` | 與 T-PHOTO-02 相同的排序；不能硬排除 D2，也不能確認 |
| T-PHOTO-07 | 照片窗景顯示「可俯瞰高鐵站」，人工判斷為高樓層 | S11 → INFERENCE 樓層區間 | `floor_exact = false`；X1、X2 不使用這個區間 |

### 3.4 證據
| ID | 情境 | 預期 |
|----|------|------|
| T-EVD-01 | 同一仲介 X 把刊登複製到 591 與樂居，兩邊都寫 A2 | 規則 I2（asserting_party_key 相同）→ DEPENDENT；獨立根來源群 = 1；自動合併條件 A2 不成立；M3、S2 各只計一次 |
| T-EVD-02 | 新快照把樓層從 12F 改成 15F | 舊樓層證據 SUPERSEDED；重新評估只用 15F |
| T-EVD-03 | 價格證據 15 天沒有再觀測到 | STALE；不再用於價格彙總 |
| T-EVD-04 | 撤回轉錄證據 | 對應 mapping 變成 RETRACTED；依賴的評估重新計算 |
| T-EVD-05 | INFERENCE 沒有 `derived_from`；或 derived_from 只有整數 ID | schema 拒絕（C-EV-01、C-UE-05） |

#### 來源獨立性（05 §5.4，v0.3 新增）
| ID | 情境 | 推導 | 預期 |
|----|------|------|------|
| T-EVD-06 | **同一張照片在兩次覆核中引用**：照片 P1（ORIGINAL，根來源 O1）；覆核 R1、R2 各建立一筆 REVIEW_CITATION（derived_from = EVIDENCE P1） | 三筆證據的根來源都是 {O1} → 兩兩 SAME；review_id 不同不影響 | 獨立根來源群 = **1**；第二次覆核不會讓佐證「變成兩個來源」，也不能滿足 K3 的「兩個獨立事實」（第一版的 `user:{review_id}` 會誤算成 2） |
| T-EVD-07 | **同一刊登改標題後跨平台發布**：仲介 X 在 591 標題「聯上世界三房」，在樂居標題「高鐵青埔景觀三房」 | 標題雜湊不同，但 I2（同一刊登者）→ DEPENDENT | 不算獨立；P7 +0.2；A2 不成立 → **不自動合併**（對應 C-IR-02） |
| T-EVD-08 | **同一文件的圖片與 OCR 文字**：圖片 E_img（ORIGINAL，根來源 O_doc），OCR 文字 E_ocr（OCR，derived_from E_img） | 根來源相同 → SAME | S1 只計一次；兩者合起來**不**滿足 K3 的兩來源路徑（對應 C-EV-02） |
| T-EVD-09 | **來源獨立性未知**：L3 的刊登者不明（key = null），L4 是仲介 Y；沒有關係紀錄，也沒有雜湊線索 | 兩者都是第三方內容，I1 不適用；沒有正面佐證 → UNKNOWN | 視為不獨立：A2 不成立、K3 兩來源路徑不成立；可以建立 LISTING_MATCH 覆核 |
| T-EVD-09c | **不同仲介**：仲介 X 與仲介 Y 各自刊登，key 已知而且不同，沒有衍生關係，也沒有雜湊線索 | 陳述者不同 ≠ 獨立的正面佐證；I1 不適用（雙方都是第三方）→ UNKNOWN | 視為不獨立（D-011） |
| T-EVD-09b | 仲介 X 與仲介 Y 的照片 `content_hash` 相同 | I3 → UNKNOWN（只是重複線索，不直接判定 SAME 或 INDEPENDENT） | 同 T-EVD-09；需要人工覆核來源關係 |
| T-EVD-10 | **兩份確實獨立、可追溯的佐證**：(a) 使用者現場門牌照片（USER_SITE_VISIT、ORIGINAL、有 capture、沒有 derived_from）與仲介 X 的刊登內容；(b) 仲介 X 與仲介 Y 的刊登，使用者另外建立 `origin_relation = INDEPENDENT`，並附正面佐證（例如兩份不同屋主委託的書面資料） | (a) I1 → INDEPENDENT；(b) 依關係紀錄 → INDEPENDENT | 兩種情況的獨立根來源群都是 2；(b) 讓 T-IDN-02 的 A2 成立；對應 C-OR-02 |
| T-EVD-11 | **連鎖撤回**：撤回照片 P1 → R1 的 REVIEW_CITATION → L2 由 R1 衍生的 S8 推論 | 沿 `evidence_derivation` 反查：全部 RETRACTED（UPSTREAM_RETRACTED）；`GET /evidence/{evidence_id}/dependents` 列出這些證據與 L1、L2 的評估 | L1、L2 都重新評估 |
| T-EVD-12 | 人工主張只是重新包裝既有推論 | 同 T-ADDR-09e | 根來源含 SYSTEM → 不能當成決定性證據 |

### 3.5 覆核
| ID | 情境 | 預期 |
|----|------|------|
| T-REV-01 | 拿到任務 v2 之後有新快照（任務變成 v3），再用 v2 送出 | 412 `STALE_VERSION` |
| T-REV-02 | `CONFIRM_CANDIDATE` 沒有佐證，或 `evidence_ids = []` | 422（C-RD-04、C-RD-05） |
| T-REV-03 | `SET_UNIT` 缺少 `address_unit_id`，或值為 null | 422（C-RD-02、C-RD-03） |
| T-REV-04 | 同一任務送出兩次覆核 | 兩筆都保留；舊的是 SUPERSEDED |
| T-REV-05 | 在 `LISTING_MATCH` 任務送出 `SET_UNIT` | 422 `DECISION_NOT_ALLOWED` |
| T-REV-06 | 沒有帶 If-Match | 428 |

### 3.6 同屋辨識（13）
| ID | 情境 | 推導 | 預期 |
|----|------|------|------|
| T-IDN-01 | 同樓不同戶：L1（591）與 L2（樂居）都是 12F、45.2 與 45.1 坪、3/2/2，不同仲介 | 候選交集有 3 個 → P1 不成立；P2 0.2 + P3 0.2 + P4 0.1 = 0.5；A2 不成立 | `REVIEW_REQUIRED`；**不自動合併** |
| T-IDN-02 | 跨仲介重刊：La（仲介 X，BASELINE）與 Lb（仲介 Y，baseline 之後的 CONFIRMED_NEW）各自明寫「79號12樓」；mapping 與 R-ALL 都 VERIFIED | (1) 兩邊都是 CONFIRMED（SYSTEM_RULES），指向同一 dwelling，但來源關係是 UNKNOWN（T-EVD-09c）→ A2 ✗；配對分數 P1 0.4 + P3 0.2 = 0.6 → `REVIEW_REQUIRED`。(2) 使用者建立附佐證的 `origin_relation = INDEPENDENT`（T-EVD-10b）→ 重新評估 → A1–A4 成立 | (1) **不自動合併**，建立 LISTING_MATCH 覆核；(2) `AUTO_MERGE_ELIGIBLE` → AUTO_MERGE；`PROPERTY_MERGED`（不通知）；**只對 Lb** 發 `DWELLING_NEW_LISTING`；La 不會被當成新案 |
| T-IDN-03 | 兩則都是 `floor_exact`，但樓層不同 | F1 | `CONFLICTING`；不合併 |
| T-IDN-04 | 誤合併後拆分：人工合併 L1、L2，L2 得到 S8 繼承；之後拆分 | S8 證據 RETRACTED；L2 重新評估（回到 MULTIPLE）；兩個 property 的 `current_min_price` 重新計算；舊通知內容不變；發出更正通知 | 如左 |
| T-IDN-05 | 撤回合併；之後已經有依賴這次合併的操作 | — | 先撤回後續操作；否則回 409 `DEPENDENT_OPERATIONS` |
| T-IDN-06 | 人工確認 L1 門牌（同一群組有 L2） | L2 只得到 S8（不符合 K3） | L2 不是 CONFIRMED |
| T-IDN-07 | 拆分後沒有新證據 | F7 | 不再建立同一對的任務 |

### 3.7 監控生命週期（07）
| ID | 情境 | 預期 |
|----|------|------|
| T-LIFE-01 | 第一次完整的 BASELINE，有 20 則刊登 | 20 個 `LISTING_FIRST_OBSERVED(context = BASELINE)`；0 個 NEW_CONFIRMED；0 則通知 |
| T-LIFE-02 | baseline 之後，完整輪次出現新刊登，前一輪是同指紋的完整輪次 | `LISTING_NEW_CONFIRMED(basis = COMPLETE_RUN_DIFF)` |
| T-LIFE-03 | PARTIAL 輪次中沒有出現某則刊登 | 未出現次數不變 |
| T-LIFE-04 | SUCCEEDED，但撞到頁數上限（C-FR-01） | `coverage = INCOMPLETE`；未出現次數不變 |
| T-LIFE-05 | 連續 2 個完整輪次缺席 | `LISTING_DELISTED`，`missing_complete_run_ids` 有 2 筆；沒有新快照 |
| T-LIFE-06 | 人工增量匯入 1 筆 | 其他刊登的未出現次數不變 |
| T-LIFE-07 | 下架 → 重新上架 → 下架 → 重新上架 | 週期 1→2→3；冪等鍵 `delisted:X:1`、`relisted:X:2`、`delisted:X:2`、`relisted:X:3` 都不同 |
| T-LIFE-08 | 價格 1,580 萬 → 1,550 萬 | `LISTING_PRICE_CHANGED(direction = DOWN, diff = -300000)` |
| T-LIFE-09 | 篩選條件變更 | `SCOPE_CHANGED` → INCOMPLETE，直到重新 baseline |
| T-LIFE-10 | 在 INCOMPLETE 輪次首次觀測；下一個完整輪次符合條件 | 先是 `UNCONFIRMED`；之後**補發一次** NEW_CONFIRMED（`late_confirmation = true`） |
| T-LIFE-11 | 前一個完整輪次 63 筆，這輪 0 筆 | `SUSPICIOUS_EMPTY` → INCOMPLETE |
| T-LIFE-12 | 人工初始化匯入（`import_mode = INITIAL`）30 筆 | 只有 FIRST_OBSERVED（MANUAL_INITIAL），不通知 |

#### 初始化與同屋新案通知（12 §2.1、§3，v0.3 新增）
| ID | 情境 | 推導 | 預期 |
|----|------|------|------|
| T-LIFE-13 | **初始化含重複刊登**：BASELINE 中有 L1（591）與 L2（樂居）是同一戶；baseline 結束 5 天後才完成合併 | L1、L2 都是 `first_observed_context = BASELINE`、`NOT_NEW`；合併時間在 baseline 之後，但判斷看刊登自己的脈絡 | 只有 `PROPERTY_MERGED`（notify_eligible = false，`initial_context_listing_ids = [L1, L2]`，`new_listing_ids = []`）；**沒有** DWELLING_NEW_LISTING；0 則通知（schema 也禁止，C-EVT-21） |
| T-LIFE-14 | **延遲合併的真正新增刊登**：L20 在 baseline 之後的完整輪次首次觀測（CONFIRMED_NEW，已發 NEW_CONFIRMED）；3 天後與 L11（BASELINE）合併 | 兩個條件都成立 → trigger = MERGE；anchor = L11 | `PROPERTY_MERGED`（不通知）＋ `DWELLING_NEW_LISTING(L20, anchor = L11)`（通知；C-EVT-03） |
| T-LIFE-15 | **人工初始化**：L30 由 MANUAL_INITIAL 匯入，之後與 L11 合併 | L30 是 MANUAL_INITIAL → 不是 CONFIRMED_NEW | FIRST_OBSERVED 的 suppression_reason = `MANUAL_INITIAL_CONTEXT`（C-EVT-25）；合併只有 `PROPERTY_MERGED`；0 則新案通知 |
| T-LIFE-16 | **撤回後重新合併**：接續 T-LIFE-14，撤回操作 41，再以操作 45 合併回同一群組 | 冪等鍵 `dwelling_new_listing:20` 已存在 | `MERGE_REVERTED` 與第二次 `PROPERTY_MERGED` 都保存（不通知）；**不重複**產生 DWELLING_NEW_LISTING 與新案通知；撤回時與重新合併時各有一則更正通知（`notification_kind = CORRECTION`），引用原通知 |
| T-LIFE-20 | **拆分後改併到不同成員的群組**：接續 T-LIFE-14，拆分群組；之後 L20 與 L40（BASELINE，首次觀測比 L11 更早）合併，群組最早刊登（anchor）改變 | 冪等鍵只看 listing_id（`dwelling_new_listing:20`），已存在；不依賴 anchor | 不產生新的 DWELLING_NEW_LISTING；拆分時與改併時各發一則更正通知，引用原通知；原通知的 `rendered` 不變 |
| T-LIFE-17 | **合併時尚未確認是新刊登**：L21 在 INCOMPLETE 輪次首次觀測（UNCONFIRMED），先與 L11 合併；之後補確認 | 合併當下條件不成立；補確認時兩個條件都成立 → trigger = NEW_LISTING_CONFIRMED | 合併時只有 PROPERTY_MERGED；補確認時發 NEW_CONFIRMED（late）＋ DWELLING_NEW_LISTING（各一次） |
| T-LIFE-18 | L22（CONFIRMED_NEW）與 L23 合併，但 L23 的首次觀測比 L22 晚 | 對 L22 而言，群組中沒有更早的其他刊登 → 不成立；對 L23：如果是 CONFIRMED_NEW，anchor = L22 | 不對 L22 發 DWELLING_NEW_LISTING；L23 依自己的脈絡決定 |
| T-LIFE-19 | 初始化結束後真正新增、而且與既有刊登無關的刊登 | 同 T-LIFE-02 | 正常發 NEW_CONFIRMED 並通知 |

### 3.8 事件、通知、API、來源、保存
| ID | 情境 | 預期 |
|----|------|------|
| T-EVT-01 | 同一輪重跑 | 沒有新增事件（idempotency_key 衝突就略過） |
| T-EVT-02 | 所有事件範例符合 schema；反例被拒絕 | contract-cases 的 C-EVT-* |
| T-NTF-01 | 同一事件被處理兩次 | 只有 1 則通知 |
| T-NTF-02 | 拆分之後 | 更正通知（`notification_kind = CORRECTION`）的 `corrects_notification_id` 指向原通知；原通知的 `rendered` 不變（C-NT-01、C-NT-02） |
| T-API-01 | `SRC-MANUAL` 沒有網址也沒有編號（C-LC-01） | 201，`external_id` 以 `man_` 開頭 |
| T-API-02 | 同一個 Idempotency-Key、相同內容重送 | 重播原回應，並帶 `Idempotent-Replayed: true` |
| T-API-03 | 平台來源兩者都缺（C-LC-03、C-LC-04） | 422 |
| T-API-04 | 同一個 key、不同內容 | 422 `IDEMPOTENCY_KEY_REUSED` |
| T-API-05 | Merge Patch（C-PATCH-01～06） | 省略 = 不變；null = 清除；不可為 null 的欄位 → 422 |
| T-API-06 | 已存在的 `(source, external_id)` | 409 `LISTING_EXISTS` |
| T-RUN-01 | 403 或 CAPTCHA | FAILED；自動停用；`SOURCE_HEALTH_CHANGED` |
| T-RUN-02 | 429 帶 Retry-After | 依指示等待；最多重試 3 次 |
| T-RUN-03 | 連續 3 輪 FAILED | `/health` 顯示 degraded |
| T-RUN-04 | 權利是 UNKNOWN 時要啟用自動擷取 | 409 `SOURCE_NOT_ELIGIBLE` |
| T-RET-01 | 刪除原始頁面後重新評估 | 評估結果相同；`raw_available = false` |
| T-RET-02 | 刪除後用新版 parser 重新抽取 | 回報無法重新抽取（不得假裝成功） |
| T-EXP-01 | 匯出 | UTF-8 BOM；只包含 `republish` 允許的欄位 |

## 4. 與第一版測試的差異（依現行規則重新推導）
| 第一版 | 問題 | 現行 |
|--------|------|------|
| T-VERIFY-02：「只有路段＋社區名 → LIKELY（≤ 0.85）」 | 依現行規則是 0.2 + 0.2 = 0.4 | T-MEM-02：UNDETERMINED |
| T-VERIFY-01：「完整門牌命中 → MATCH 1.0」 | 把社區歸屬和門牌混為一談，也忽略 mapping 是否已核對 | 拆成 T-MEM-07（歸屬）與 T-ADDR-01／T-ADDR-02（門牌） |
| T-VERIFY-03：「同一路、門牌號不在白名單 → MISMATCH」 | 違反「名冊缺少紀錄不等於不屬於社區」 | T-MEM-04（PARTIAL → LIKELY_MEMBER）／T-MEM-05（COMPLETE → NON_MEMBER） |
| T-DEDUP-02：「弱鍵不自動合併」，但中鍵會自動合併 | 同樓不同戶會被誤合併 | T-IDN-01 |
| T-DIFF-03：「連續 2 次缺席 → DELISTED；PARTIAL 不累加」 | 沒有定義完整輪次 | T-LIFE-03～05、T-LIFE-09、T-LIFE-11 |
| v0.2 T-ADDR-09：「mapping 未核對，附現場照片描述就 CONFIRMED」 | 與 06「名冊核對前不產生 CONFIRMED」矛盾，也沒有要求刊登連結 | T-ADDR-09a～09g（H1–H6） |
| v0.2 T-ADDR-11：「撤回後回到 SINGLE_CANDIDATE」 | 有其他有效佐證時不應固定降級 | T-ADDR-11a～11c |
| v0.2 T-ADDR-04：「樓層 1–24 是 VERIFIED → 72 個候選」 | 以總樓高推定每層都有相同安排 | T-ADDR-04（依規則範圍 2–24 → 69）、T-RULE-01 |
| v0.2 T-EVD-01、T-IDN-02 以 dependency_group 判斷獨立 | 標題雜湊與 `user:{review_id}` 會誤判 | T-EVD-01、T-EVD-06～12 |
| v0.2 T-IDN-02：合併就發 DWELLING_NEW_LISTING | 初始化資料合併也會被通知 | T-IDN-02、T-LIFE-13～19 |

## 5. 真值標註規則（評估用）
| 項目 | 規則（【建議預設】） |
|------|----------------------|
| 標註單位 | 每則刊登：社區歸屬真值、dwelling 真值（或 UNKNOWN）、完整門牌真值（或 UNKNOWN） |
| 真值依據 | 必須是**獨立於系統評估**的證據：例如現場門牌、已核對的銷控表；每筆都要記錄依據與標註者信心（高、中） |
| 不能當真值 | 系統自己的輸出；只靠刊登文字推論；未核對的轉錄 |
| UNKNOWN | 證據不足時標成 UNKNOWN，不強迫標註 |
| 同屋群組 | 依 dwelling 真值分組；dwelling 不明時，由標註者建立暫時群組 |
| 雙人複核 | 至少抽 20% 給第二位標註者（如果只有使用者一人，就隔週重標）；記錄一致率 |

## 6. 資料切分與指標
- **依實體住宅分組切分**：同一個 dwelling（或暫時群組）的所有刊登，只能出現在同一個集合，避免同一戶的不同刊登跨集合洩漏。
- 切分（【建議預設】）：開發集 60%、校準集 20%、評估集 20%；評估集凍結，只在里程碑結束時使用。
- **校準與最終評估不得使用相同資料**；規則權重只能用開發集調整，門檻與機率校準只能用校準集。

| 指標 | 定義 | 報告方式 |
|------|------|----------|
| 門牌作答正確率 | `CONFIRMED` 中，門牌等於真值的比例 | 分開報告 SYSTEM_RULES 與 HUMAN_REVIEW |
| 門牌覆蓋率 | 真值已知的刊登中，系統作答（CONFIRMED）的比例 | — |
| 棄答率 | `ABSTAINED`、`INSUFFICIENT_INFO`、`NO_CANDIDATE` 的比例 | 分狀態報告 |
| 候選包含率 | MULTIPLE 或 SINGLE 時，真值在候選中的比例 | — |
| 社區歸屬 | MEMBER 的精確率與召回率；NON_MEMBER 的精確率 | — |
| 同屋辨識 | 配對的精確率與召回率 | — |
| 誤合併率 | 自動合併中，實際上不同戶的比例 | 單獨報告，不和召回率合併 |
| 誤拆分率 | 同戶被分在不同 property 的比例 | — |

【建議目標】（**尚未驗證**）：門牌作答正確率 ≥ 95%；誤合併率 ≤ 1%；LIKELY_MEMBER 加上覆核任務量每週 ≤ 30 筆（Q-023）。實際數值要等評估集結果出來才能報告，不能事先宣稱已達成。

## 7. 本階段已執行的檢查
只有文件與契約的離線檢查（見 [15](15-consistency-checklist.md)）。§3 的測試**全部未執行**。
