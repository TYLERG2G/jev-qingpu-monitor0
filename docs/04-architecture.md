# 04 系統架構

> 本文件定義 JEV 系統的系統情境、技術分層、資料管線流程與儲存架構。
> 所有技術選型都是【建議預設】（部署與環境見 Q-006、ADR-002）。本階段**只寫規劃文件**：不實作應用、不執行爬蟲、不部署，也不聯繫外部平台（【已確認】）。
> 名稱以 [05 資料模型](05-data-model.md)、[06 門牌判定](06-address-verification.md)、[12 事件契約](12-event-contract.md) 與 [`api/openapi.yaml`](../api/openapi.yaml) 為準；本文件只畫架構，不另外定義欄位或事件。
> v0.3 修訂：保留外部改寫版的圖表版型，並把內容改成與契約一致（見 handoff）。

---

## 1. 系統情境與架構圖

說明 JEV 系統的邊界、外部資料來源、權利檢查閘門、本機操作介面、儲存系統，以及下游通知管道之間的關係。

```mermaid
flowchart TB
  %% 外部實體與資料源
  subgraph ExtSources["外部資料來源（【待查證】／依權利啟用）"]
    p591["591<br/>（優先來源・【待查證】）"]
    leju["樂居<br/>（優先來源・【待查證】）"]
    sec_sources["次要候選來源<br/>（信義、永慶、樂屋網等・【待查證】）"]
    gov_data["政府開放資料<br/>（桃園市門牌開放資料、實價登錄・【待查證】）"]
  end

  subgraph ExtNotify["外部通知目標（Q-005，擇一）"]
    tg_bot["Telegram Bot<br/>（【建議預設】）"]
    smtp_mail["SMTP 郵件<br/>（【建議預設】）"]
  end

  subgraph Actors["使用者端（單一使用者、個人非商業・【建議預設】Q-021）"]
    user_person(["使用者"])
  end

  %% JEV 系統邊界
  subgraph JEVSystem["JEV 系統邊界（試點社區：桃園市大園區青埔「聯上世界」；社區規模【待查證】）"]
    subgraph BoundaryGate["權利閘門與禮貌存取控制"]
      rights_gate["來源權利檢查閘門<br/>（03 §5：依條款與授權放行）"]
      rate_limiter["禮貌存取管制<br/>（單一連線、間隔 ≥ 10 秒、403/CAPTCHA 立即停用）"]
    end

    subgraph UI_Interfaces["操作與服務介面（MVP 不含 Web UI，Q-012）"]
      cli_tool["CLI<br/>（PowerShell 7 / Python）"]
      rest_api["本機 REST API<br/>（127.0.0.1，OpenAPI 3.1）"]
    end

    subgraph CoreEngine["核心推斷與生命週期引擎"]
      fetcher["Fetcher"]
      parser["Parser Adapters"]
      normalizer["Normalizer<br/>（樓層區間、面積口徑、地址）"]
      evid_builder["Evidence Builder<br/>（根來源、具型別衍生參照、來源關係）"]
      reg_service["Registry Service<br/>（名冊版本、mapping、門牌推導規則、涵蓋度）"]
      mem_assessor["Membership Assessor<br/>（社區歸屬，與門牌分開）"]
      addr_resolver["Address Resolver<br/>（候選、排除、逐欄位確認、棄答）"]
      idn_resolver["Identity Resolver<br/>（ADR-004：配對、合併、拆分、撤回）"]
      life_engine["Lifecycle Engine<br/>（R1–R8 完整輪次、上架週期、四種「新」）"]
      rev_service["Review Service<br/>（佐證充分性 H1–H6、版本檢查、撤回）"]
      evt_writer["Event Writer<br/>（14 種事件、冪等、一律保存）"]
      notifier["Notifier<br/>（notify_eligible 過濾、規則比對、更正通知）"]
    end

    subgraph LocalStorage["本機儲存（SQLite WAL・【建議預設】）"]
      sqlite_db[("SQLite<br/>（SQLAlchemy 2 / Alembic）")]
      raw_store[("原始內容儲存<br/>（依來源權利保存或不保存）")]
    end
  end

  %% 外部存取資料流
  p591 -. "只在通過權利檢查時" .-> rate_limiter
  leju -. "只在通過權利檢查時" .-> rate_limiter
  sec_sources -. "次要候選" .-> rate_limiter
  rate_limiter --> rights_gate --> fetcher
  gov_data -. "人工下載後匯入" .-> cli_tool

  %% 使用者操作流
  user_person -- "人工記錄刊登 / 匯入 CSV / 執行排程" --> cli_tool
  user_person -- "本機 API：查詢、覆核" --> rest_api
  cli_tool --> CoreEngine
  rest_api --> CoreEngine

  %% 內部核心流動
  fetcher --> parser --> normalizer --> evid_builder
  evid_builder --> mem_assessor
  reg_service --> mem_assessor
  mem_assessor --> addr_resolver
  reg_service --> addr_resolver
  addr_resolver --> idn_resolver
  idn_resolver --> life_engine
  life_engine --> evt_writer
  rev_service --> evid_builder
  evt_writer --> notifier

  %% 儲存互動
  CoreEngine <--> sqlite_db
  fetcher --> raw_store

  %% 外部通知流
  notifier --> tg_bot --> user_person
  notifier --> smtp_mail --> user_person
```

---

## 2. 技術分層架構圖

依分層原則，定義表現層、應用協調層、領域核心層、資料持久化層與基礎設施層的技術選型與呼叫邊界（全部是【建議預設】）。

```mermaid
flowchart TB
  subgraph PresentationLayer["1. 表現層與介面"]
    cli_app["CLI Entrypoint<br/>（Python Typer / Click）"]
    api_server["REST API Server<br/>（FastAPI，只綁 127.0.0.1）"]
    openapi_spec["OpenAPI 3.1 契約<br/>（api/openapi.yaml；v0.6：60 個操作、153 個 schema）"]
    api_mw["API 中介層<br/>（Idempotency-Key、If-Match、Merge Patch）"]
  end

  subgraph AppLayer["2. 應用與協調層"]
    pipeline_orchestrator["Pipeline Orchestrator<br/>（輪次排程與手動觸發）"]
    import_service["Manual Import Service<br/>（INITIAL / INCREMENTAL）"]
    review_service["Review Workflow Service<br/>（覆核任務、佐證充分性、撤回與重新評估）"]
    notify_dispatcher["Notification Dispatcher<br/>（只處理 notify_eligible = true；更正通知依 notify_corrections）"]
    export_service["Export Service<br/>（物件與價格歷史 CSV，受 republish 權利限制）"]
  end

  subgraph DomainLayer["3. 領域核心層"]
    subgraph IngestDomain["資料攝取與轉換"]
      fetcher_core["Fetcher & Rights Gate<br/>（權利閘門、退避、限速）"]
      parser_adapters["Parser Adapters<br/>（591 / 樂居 / 次要來源）"]
      normalizer_core["Normalizer<br/>（坪數、樓層區間、地址正規化）"]
    end

    subgraph InferenceDomain["推斷、關聯與治理"]
      evidence_engine["Evidence Engine<br/>（事實／推論／人工主張、根來源、來源關係、連鎖撤回）"]
      registry_domain["Registry Service<br/>（棟別、坪型、戶別、住宅、門牌、推導規則、涵蓋度）"]
      membership_domain["Membership Assessor<br/>（社區歸屬，與門牌分開）"]
      address_resolver["Address Resolver<br/>（候選、排除、排序、逐欄位確認、棄答）"]
      identity_resolver["Identity Resolver<br/>（ADR-004：配對、合併閘門、衝突防護）"]
    end

    subgraph LifecycleDomain["生命週期與事件契約"]
      lifecycle_engine["Lifecycle Engine<br/>（R1–R8、未出現計數、四種「新」）"]
      event_writer["Event Writer<br/>（14 種事件、一律保存、Outbox 模式）"]
    end
  end

  subgraph PersistenceLayer["4. 資料持久化層"]
    orm_core["SQLAlchemy 2<br/>（ORM、交易控制）"]
    migration_tool["Alembic<br/>（結構遷移）"]
    sqlite_db[("SQLite 3<br/>（WAL、單一檔案）")]
    raw_storage[("原始內容儲存<br/>（依來源權利保存）")]
  end

  subgraph InfraLayer["5. 基礎設施層"]
    os_sched["Windows Task Scheduler<br/>（定時觸發 CLI）"]
    python_env["Python 3.11+<br/>（Anaconda）"]
    config_ruleset["規則集與設定<br/>（ruleset 表 + config.toml，見 09 §5）"]
    notify_gateways["通知閘道<br/>（SMTP、Telegram Bot，Q-005）"]
    audit_logger["稽核與日誌<br/>（不含密鑰與個資）"]
  end

  cli_app --> pipeline_orchestrator
  cli_app --> import_service
  api_server --> api_mw
  api_mw --> pipeline_orchestrator
  api_mw --> import_service
  api_mw --> review_service
  api_mw --> export_service
  openapi_spec -. "定義契約" .-> api_server

  pipeline_orchestrator --> IngestDomain
  pipeline_orchestrator --> InferenceDomain
  pipeline_orchestrator --> LifecycleDomain
  import_service --> normalizer_core
  review_service --> evidence_engine
  review_service --> identity_resolver
  LifecycleDomain --> notify_dispatcher

  IngestDomain --> InferenceDomain
  InferenceDomain --> LifecycleDomain

  AppLayer --> orm_core
  DomainLayer --> orm_core
  orm_core --> sqlite_db
  migration_tool --> sqlite_db
  fetcher_core --> raw_storage

  os_sched --> cli_app
  notify_dispatcher --> notify_gateways
  DomainLayer -. "讀取規則版本" .-> config_ruleset
  AppLayer -. "記錄稽核軌跡" .-> audit_logger
```

---

## 3. 資料處理與推斷管線流程圖

單一監控輪次從資料輸入、權利檢查、正規化、證據生成、社區歸屬、門牌推斷、同屋配對，到生命週期事件與通知的流程。判定細節以 06、07、12、13 為準。

```mermaid
flowchart TD
  start_run(["啟動輪次<br/>（排程 / 手動觸發 / 人工匯入）"]) --> check_run_type{"輪次類型"}

  check_run_type -- "SCHEDULED / BASELINE / MANUAL_TRIGGER" --> rights_chk{"來源權利檢查<br/>（03 §5 放行？）"}
  rights_chk -- "未通過" --> halt_rights["fetch_run = SKIPPED<br/>（維持關閉，記錄原因）"]
  rights_chk -- "通過" --> fetch_list["Fetcher：抓取列表頁<br/>（間隔 ≥ 10 秒、單一連線）"]

  fetch_list --> parse_list["Parser Adapter：解析列表"]
  parse_list --> chk_complete{"完整輪次條件 R1–R8"}
  chk_complete -- "未達標" --> mark_incomplete["coverage = INCOMPLETE<br/>（記錄原因；不累加未出現次數）"]
  chk_complete -- "全部達標" --> mark_complete["coverage = COMPLETE"]
  mark_complete --> delist_chk["沒看到的刊登：未出現次數 + 1<br/>（連續 2 個完整輪次 → LISTING_DELISTED，不需新快照）"]

  check_run_type -- "MANUAL_IMPORT" --> mark_manual["coverage = NOT_APPLICABLE<br/>（不影響其他刊登的計數與下架）"]

  mark_incomplete --> proc_items
  mark_complete --> proc_items
  mark_manual --> proc_items

  subgraph IngestionPipeline["刊登處理管線（逐筆）"]
    proc_items["取出一筆刊登"] --> normalize["Normalizer<br/>（樓層區間、面積口徑、地址）"]
    normalize --> build_evidence["Evidence Builder<br/>（內容雜湊、根來源、具型別衍生參照）"]

    build_evidence --> assess_member{"社區歸屬判定（06 §3）"}
    assess_member -- "NON_MEMBER" --> log_non_member["記錄評估結果<br/>（COMMUNITY_MEMBERSHIP_CHANGED，不通知）"]
    assess_member -- "UNDETERMINED / CONFLICTING" --> mem_review["address_status = NOT_EVALUATED<br/>（CONFLICTING 建立覆核任務）"]
    assess_member -- "MEMBER / LIKELY_MEMBER" --> resolve_addr["Address Resolver<br/>（候選、排除、排序；LIKELY_MEMBER 另建覆核）"]

    resolve_addr --> chk_addr_conf{"K1–K5 成立？（06 §5.1）"}
    chk_addr_conf -- "成立" --> addr_confirmed["CONFIRMED（SYSTEM_RULES）<br/>（逐欄位狀態與支持證據）"]
    chk_addr_conf -- "不成立" --> addr_pending["SINGLE / MULTIPLE_CANDIDATES、INSUFFICIENT_INFO、<br/>NO_CANDIDATE、CONFLICTING（符合分流條件時建立覆核）"]

    addr_confirmed --> resolve_identity["Identity Resolver<br/>（配對候選與評估）"]
    addr_pending --> resolve_identity

    resolve_identity --> chk_merge{"A1–A4 成立？（13 §5）"}
    chk_merge -- "成立" --> auto_merge["AUTO_MERGE<br/>（PROPERTY_MERGED：只記錄同屋關係，不通知）"]
    chk_merge -- "衝突 / 證據不足 / 來源 UNKNOWN" --> split_or_task["維持各自的 property<br/>（REVIEW_REQUIRED 時建立 LISTING_MATCH 覆核）"]

    auto_merge --> chk_dw_new{"加入的刊登是 CONFIRMED_NEW，<br/>而且群組有更早首次觀測的刊登？"}
    chk_dw_new -- "是，且此刊登未曾發過" --> evt_dw_new["DWELLING_NEW_LISTING（通知；每則刊登最多一次）"]
    chk_dw_new -- "否" --> lifecycle_check
    evt_dw_new --> lifecycle_check
    split_or_task --> lifecycle_check["Lifecycle Engine：生命週期演進"]

    lifecycle_check --> eval_new{"首次觀測與新刊登判定（07 §3）"}
    eval_new -- "首次觀測" --> evt_obs["LISTING_FIRST_OBSERVED<br/>（保存；不通知）"]
    eval_new -- "確認新刊登" --> evt_new["LISTING_NEW_CONFIRMED（通知）"]
    eval_new -- "原刊登重新上架" --> evt_relist["LISTING_RELISTED（通知）"]
    evt_new -. "如果已在同屋群組中" .-> chk_dw_new

    lifecycle_check --> chk_diff{"價格或欄位變更？"}
    chk_diff -- "有變更" --> evt_price["LISTING_PRICE_CHANGED / LISTING_FIELDS_CHANGED"]
    chk_diff -- "沒有變更" --> cycle_done["不新增快照"]
  end

  subgraph EventAndNotification["事件與通知"]
    evt_obs --> write_event["Event Writer<br/>（idempotency_key；事件一律保存）"]
    evt_new --> write_event
    evt_relist --> write_event
    evt_dw_new --> write_event
    evt_price --> write_event
    auto_merge --> write_event
    delist_chk --> write_event

    write_event --> notifier_disp["Notifier<br/>（只處理 notify_eligible = true；拆分、撤回只發更正通知）"]
    notifier_disp --> send_out["Telegram / SMTP<br/>（寫入 notification，保存內容快照）"]
  end
```

---

## 4. 領域實體與儲存架構圖

只畫主要實體之間的關係；表名、欄位與型別以 [05 資料模型](05-data-model.md) 為準。

```mermaid
erDiagram
  source ||--o{ fetch_run : runs
  source ||--o{ listing : publishes
  fetch_run ||--o{ listing_snapshot : produces
  listing ||--o{ listing_snapshot : observed_as
  listing ||--o{ property_membership : member_of
  property ||--o{ property_membership : history
  property }o--o| dwelling : resolved_to
  sales_unit ||--o{ dwelling : floors
  registry_version ||--o{ address_derivation_rule : contains
  dwelling }o--o| address_unit : derived_address
  listing ||--o{ candidate : evaluates
  candidate }o--o| dwelling : points_to
  listing ||--o{ evidence : grounds
  listing_snapshot ||--o{ evidence : sources
  source_origin ||--o{ evidence : roots
  listing ||--o{ assessment : assessed_by
  assessment ||--o{ review_task : opens
  review_task ||--o{ review : has
  listing ||--o{ listing_event : about
  fetch_run ||--o{ listing_event : causes
  listing_event ||--o{ notification : notified_by
```

---

## 5. 元件職責與邊界

| 元件 | 職責 | 不做的事 |
|------|------|----------|
| **Fetcher** | 權利閘門（[03 §5](03-data-sources-and-compliance.md)）、單一連線限速、退避、蒐集輪次完整度資訊（R1–R8） | 不登入、不繞過 CAPTCHA、不輪換 IP、未獲授權不擷取 |
| **Parser adapters** | 每個來源一個解析器，產出快照欄位與定位資訊 | 不做業務判斷、不臆測缺漏欄位 |
| **Normalizer** | 樓層轉成區間、面積口徑換算、地址正規化（[02](02-domain-and-glossary.md)） | 不把模糊樓層（例如「低樓層」）轉成確定樓層 |
| **Evidence Builder** | 建立證據（種類、出處版本、定位、內容雜湊、根來源、具型別衍生參照）、判定來源關係（[05 §5.4](05-data-model.md)）、處理取代、過期與連鎖撤回 | 不把內容雜湊或 review_id 當成獨立來源；沒有正面佐證時不判定為獨立 |
| **Registry Service** | 名冊版本、mapping 狀態、門牌推導規則（[05 §5.5](05-data-model.md)）、涵蓋度 | 不把【先前轉錄・待核對】的資料當成已查證；不依總樓高推定樓層；不宣稱社區規模（【待查證】） |
| **Membership Assessor** | 判定刊登是否屬於試點社區（[06 §3](06-address-verification.md)） | 社區判定與門牌判定分開；名冊缺漏不等於不屬於社區 |
| **Address Resolver** | 門牌候選、排除、排序、逐欄位確認、棄答（[06 §4–5](06-address-verification.md)） | 證據不足時不給出單一確定門牌 |
| **Identity Resolver** | 跨刊登配對、依 ADR-004 判定自動合併、衝突偵測、拆分與撤回（[13](13-identity-resolution.md)） | 來源關係不是 INDEPENDENT（包括 UNKNOWN），或有衝突時不自動合併 |
| **Lifecycle Engine** | 完整輪次檢定（R1–R8）、未出現計數、上架週期、四種「新」（[07](07-monitoring-pipeline.md)） | 人工增量匯入不影響其他刊登；非 COMPLETE 輪次不累加未出現次數 |
| **Review Service** | 覆核任務、If-Match 版本檢查、佐證充分性判定（H1–H6）、把覆核轉成證據、撤回後重新評估 | 不把確認動作本身當成真值 |
| **Event Writer** | 依 [12](12-event-contract.md) 寫入 14 種事件；以 `idempotency_key` 確保冪等；事件一律保存 | 不發出契約以外的事件、不改寫歷史事件 |
| **Notifier** | 只處理 `notify_eligible = true` 的事件；比對規則、保存內容快照、重試；依 `notify_corrections` 發更正通知 | 不修改已發送的通知；合併本身不觸發新案通知 |

---

## 6. 技術選型與依據（【建議預設】）

| 項目 | 建議 | 理由 | 相關 |
|------|------|------|------|
| **語言** | Python 3.11+（Anaconda） | 使用者環境已經有；解析與資料處理生態成熟 | — |
| **API** | FastAPI + Pydantic v2 | 可以產生 OpenAPI 3.1，並和 `api/openapi.yaml` 比對 | 08 |
| **儲存** | SQLite 3（WAL） | 單機、零管理、備份就是複製檔案 | ADR-002、Q-006 |
| **ORM 與遷移** | SQLAlchemy 2 + Alembic | 日後需要時可以遷移到 PostgreSQL | ADR-002、Q-021 |
| **規則版本化** | `ruleset` 表 + `config.toml` | 相同規則版本、名冊與證據 → 相同結果（NFR-09） | 01、09 §5 |
| **排程** | Windows Task Scheduler 觸發 CLI | 不需要常駐程序 | Q-006 |
| **通知** | Telegram Bot 或 SMTP 擇一 | 個人使用門檻低 | Q-005 |
| **介面** | CLI + 本機 REST API；MVP 不做 Web UI | — | Q-012 |

---

## 7. 擴充與演進路徑（M2 以後；皆需另立需求）

1. **多社區**：資料表已保留 `community_id`；名冊版本、規則參數與統計都以社區區分。
2. **多使用者與雲端部署（Q-021、Q-006）**：加上帳號與權限機制，資料庫遷移到 PostgreSQL；部署方式另行決定。
3. **租賃（Q-020）**：`deal_type = RENT`，另外定義租金口徑（押金、管理費）與月租變動事件。
4. **Web UI（Q-012）**：MVP 不包含；如需要，於 M2 另立需求。
5. **行情比對**：實價登錄查證完成後（V-05），作為 M2 的獨立功能；估價不在本專案範圍（01 §3 非目標）。
