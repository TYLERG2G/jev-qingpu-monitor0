# 交接

## 目前狀態（2026-10-18）
- 版本：v0.3.1。**沒有任何實作、爬蟲、部署或外部聯繫。**
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
1. 決定何時連到 GitHub（Private）。本機沒有 `gh`；可以在 github.com/new 建空的 repo，再設定 remote 推送，第一次推送時由 Git Credential Manager 登入。
2. 回覆待確認事項（00 的 P0、P1，以及 Q-027、Q-028）。
3. M0：執行 source-log §5 的 V-01～V-07（需要使用者同意後才能連線查證），並把結果記成 mapping 與推導規則。
4. 確認 MVP 範圍（ADR-001）後才進入實作規劃。

## 注意事項（給下一位協作者）
- JEV 是代稱，不是社區名稱；試點社區是聯上世界；社區規模尚未查證。
- 轉錄對照（A1=77號…D6=109號）**尚未核對**；不得當成完整名冊，也不能作為系統確認的依據。
- 「建議預設」在使用者明確同意前，都不是「已確認」。
- 驗證腳本是暫存的，沒有放進 repo；如果要讓檢查可以重複執行，請先徵求使用者同意，再加入 `tools/`。
