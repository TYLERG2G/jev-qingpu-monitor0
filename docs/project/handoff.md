# 交接

## 目前狀態（2026-10-18）
- **v0.6：功能完整、尚未用真實模型測試**（D-018、D-020、D-113）。見 [17 實作狀態](../17-implementation-status.md)、[18 使用手冊](../18-user-guide.md)。`pytest` 103 passed；社區總覽、591 書籤匯入（D-019）、JEV 決策模型（OpenAI／OpenRouter／TypeSafe，軟計分）、評估工具、通知規則（Email／Telegram）、匯出；地基修正：錯誤處理、schema 遷移、交易、備份、日誌、Host 檢查、自動金鑰。正式資料庫 `data/jev.db`（13 則真實記錄＋1 次書籤匯入）。**決策模型從未呼叫外部**；下一步是使用者同意後用 `jev eval` 測試。尚未 commit（等使用者指示）。
- 版本：v0.5（已 commit 到本機 `main`；尚未連到 GitHub）。依 D-016 調整戶別辨識方法：坪數篩選 + 591 刊登照片格局比對 + 樂居格局圖；規格見 [16](../16-layout-photo-matching.md)。樂居第二次唯讀查詢仍然 403，**改由使用者人工整理**，請填寫 [`leju-layout-intake-template.csv`](../research/leju-layout-intake-template.csv)（每列一個戶別；只填格局特徵與坪數，不要貼上圖片；記錄頁面網址、位置與觀測時間）。
- 使用者已提供 2 張樂居截圖（門牌示意圖、「…9.11.13F平面圖」）；觀察結果記在 source-log §3 與 M0 報告 §7a。樂居門牌圖與 591 疑似同源，**不算獨立佐證**。
- 樂居平面圖已分析：[leju-layout-analysis.md](../research/leju-layout-analysis.md)。D 棟 5 戶的排列與門牌示意圖逐格吻合（推論方向一致）；各棟是鏡像配對；坪數可以直接分出 C3、D3、C5 等唯一戶別，其他戶別要靠照片判斷前排或後排、左右鏡像。需要清晰版本的平面圖來確認戶別編號。
- 19:09 收到的「cad_architectural_floorplan_7_9_11_13f」圖判斷為 AI 重繪圖（標示重複、A3/A5 左右相反、文字亂碼），**不採用**；已在 16 §6 加上規則：AI 增強圖片不能當成來源事實。
- **下一階段（D-017）進行中**：官方實價登錄 115S3 已下載並篩選（1 筆，與 591 一致）；坪數參考表 [unit-area-reference.csv](../research/unit-area-reference.csv)；13 則刊登的候選試算 [poc-candidate-trials.md](../research/poc-candidate-trials.md)：12 則縮小到 1 戶或 1 組鏡像配對，4 則揭露樓層的縮小到 2 個完整門牌候選；D2／D5 型 7 則 591 刊登推論只有約 2 戶。
- v0.4：M0 資料可行性查證完成。
- **M0 結果**：見 [M0 可行性報告](../research/m0-feasibility-report.md)、[門牌對照 CSV](../research/lianshang-world-address-mapping.csv)、[房源抽樣分析](../research/listing-sample-analysis.md)。重點：17 組戶別→號原圖已核對、全部一致，但**沒有獨立佐證**；沒有逐層名冊（只有 2F 平面圖）；所有推導規則都是 HYPOTHETICAL 或 TRANSCRIBED_UNVERIFIED；591 刊登只能辨識到社區層級，信義刊登可以縮小到 2–4 個門牌候選；**沒有找到任何授權的自動刊登來源**。建議進入「資料補齊 + 離線概念驗證」。
- M0 依 D-015 只做唯讀查詢：沒有爬蟲、沒有大量下載、沒有登入、沒有繞過 403、沒有聯繫平台。591 平面圖 4 張只存在工作階段暫存區，沒有放進 repo。
- 先前（v0.3.1）：**沒有任何實作、爬蟲、部署或外部聯繫。**
- v0.3 修正第二輪審閱的 4 項問題（D-106～D-109，【建議預設】）；v0.3.1 依使用者指示（D-011～D-014）修正 04、獨立性規則與新案去重。
- 已執行離線檢查（見 [15](../15-consistency-checklist.md)），0 個未解決問題。業務行為測試（10 §3）全部只是規劃，尚未執行。
- 版本控制：已建立**本機** git repo（`main` 分支）。**尚未連到 GitHub**；使用者決定之後再連，可見性用 Private（D-014）。
- 狀態：等待使用者審閱。

## 讀文件的順序
README → [background](background.md) → [00](../00-open-questions.md) → [01](../01-requirements.md) → [source-log](source-log-lianshang-world.md) → [06](../06-address-verification.md) → [13](../13-identity-resolution.md) → [05](../05-data-model.md) → [07](../07-monitoring-pipeline.md) → [12](../12-event-contract.md) → [08](../08-api-contract.md)

## 04 架構文件的處理紀錄
v0.3 修訂期間，`docs/04-architecture.md` 被外部整份改寫。v0.3.1 依使用者授權（D-013）保留它的圖表版型，並改成與契約一致：

| 原內容 | 處理 |
|--------|------|
| 「聯上世界 4 棟 242 戶」 | 移除；改成「社區規模【待查證】」 |
| `dependency_group` 獨立性 | 改成根來源、具型別衍生參照、來源關係（05 §5.4） |
| `LISTING_NON_MEMBER`、`LISTING_MERGED`、`FIELD_CHANGED` | 改成 `COMMUNITY_MEMBERSHIP_CHANGED`、`PROPERTY_MERGED`、`LISTING_FIELDS_CHANGED` |
| 狀態 `MEMBER / CANDIDATE`、`CANDIDATE / ABSTAIN` | 改成 05 的 enum（`LIKELY_MEMBER`、`SINGLE_CANDIDATE` 等） |
| 合併後發 `DWELLING_NEW_LISTING（通知）` | 合併只發 `PROPERTY_MERGED`（不通知）；新案另外判定 |
| ERD 表名（`sources`、`runs`、`address_records` 等） | 改成 05 的表名 |
| 「13 種事件」「51 操作 / 125 Schema」 | 改成 14 種事件、60 個操作、151 個 schema |
| 「Web 介面」 | 移除；註明 MVP 不做 Web UI（Q-012） |
| 「YAML 設定」 | 改成 `config.toml`（09 §5） |
| 「好房」、「桃園門牌 ODS」 | 改成 03 列出的次要來源與「桃園市門牌開放資料」 |

## 已知限制
- 規則 I1 只適用於「使用者本人原始觀測 vs. 第三方內容」；兩家仲介之間一律 UNKNOWN，需要人工附佐證判定 INDEPENDENT。這會讓自動合併在實務上很少發生，覆核量增加（Q-023）。
- 新案通知每則刊登最多一次；如果第一次併入的群組是錯的，使用者之後收到的是更正通知，不是第二則新案通知（ADR-007）。
- 佐證充分性（H1–H6）、來源關係判定、連鎖撤回、decision 與 task_type 是否相符、If-Match、冪等、`floor_min ≤ floor_max`、規則衝突，都是伺服器行為，schema 無法驗證；要等實作後用業務測試驗證。

## 下一步（使用者審閱後）
1. 審閱 M0 報告；決定是否 commit v0.4。
2. 資料補齊（M0 報告 §8）：使用者用一般瀏覽器確認樂居銷控表；決定是否下載一期官方實價登錄與桃園門牌 CSV（Q-031）；設法取得戶別→號的獨立佐證（例如現場門牌）。
3. 回覆新的待確認事項：Q-029（地圖定位欄與「該層戶數」規則）、Q-030（是否洽詢授權）、Q-031、Q-032，以及既有的 P0、P1。
4. 決定何時連到 GitHub（Private）。本機沒有 `gh`；可以在 github.com/new 建空的 repo，再設定 remote 推送（本次依指示沒有設定 remote）。
5. 確認 MVP 範圍（ADR-001）後才進入實作規劃。

## 注意事項（給下一位協作者）
- JEV 是代稱，不是社區名稱；試點社區是聯上世界；社區規模尚未查證。
- 轉錄對照（A1=77號…D6=109號）**尚未核對**；不得當成完整名冊，也不能作為系統確認的依據。
- 「建議預設」在使用者明確同意前，都不是「已確認」。
- 驗證腳本是暫存的，沒有放進 repo；如果要讓檢查可以重複執行，請先徵求使用者同意，再加入 `tools/`。
