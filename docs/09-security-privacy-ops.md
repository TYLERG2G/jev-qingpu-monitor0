# 09 資安、個資與維運

> 全部是【建議預設】，除非另外註明。

## 1. 威脅與對策
| 威脅 | 對策 |
|------|------|
| API 被其他裝置存取 | 只綁 127.0.0.1（Q-014）；用 `X-API-Key` 驗證，金鑰至少 32 bytes 隨機值 |
| 密鑰外洩 | 放在 `.env`，並加入 `.gitignore`；不寫進日誌 |
| 原始內容含個資或受著作權保護 | 依 `source.rights` 決定要不要保存（03 §2）；摘錄先遮罩；權利未知時不保存原始內容 |
| 授權介面的憑證（Q-024） | 只存在使用者本機的密鑰庫或 `.env`；不共用帳號；不保存密碼明文 |
| 依賴套件漏洞 | 鎖定版本；定期執行 `pip-audit` |
| 被平台視為濫用 | 03 §5 的啟用條件與 07 §7 的錯誤處理 |

## 2. 個資最小化
- 不蒐集刊登者姓名、電話、LINE ID、Email（Q-015）。
- 使用者提供的 USER_OBSERVATION（例如現場照片）：只存描述、雜湊與觀測時間；要不要保存檔案，【待確認】。
- 使用目的：【建議預設】個人非商業（Q-021）；如果改成商業使用，要重新評估權利與法規。

## 3. 保存期限與可追溯性
| 資料 | 保存 | 刪除後的影響 |
|------|------|--------------|
| 原始頁面、圖片（`source_document.raw_ref`） | 依來源權利；權利允許時最多 90 天（Q-007） | 無法重新抽取；評估仍然可以重現（05 §6） |
| 結構化快照、證據值、摘錄、雜湊 | 永久 | — |
| 評估、覆核、合併操作 | 永久（稽核需要） | — |
| 事件、通知內容快照 | 永久 | — |
| `idempotency_record` | 24 小時 | 過期後，相同 key 視為新請求 |

## 4. 日誌、備份、監控
- `app.log` 30 天輪替，不含密鑰與個資。
- 每天用 SQLite `.backup` 備份，保留 14 份；異地複本的位置【待確認】。
- `/health` 與 `SOURCE_HEALTH_CHANGED`：來源連續失敗、被自動停用、通知失敗率 > 50%、磁碟剩餘 < 1 GB 時告警。

## 5. 設定範例（草案）
```toml
# config.toml（草案；全部是建議預設）
[project]
codename = "JEV"                 # 專案代稱，不是社區名稱

[community]
code = "lianshang-world"
name = "聯上世界"                # 已確認的試點社區
city = "桃園市"
district = "大園區"
area_name = "青埔"
timezone = "Asia/Taipei"

[scope]
deal_types = ["SALE"]            # 建議預設，Q-020
single_user = true               # 建議預設，Q-021

[schedule]
runs = ["09:00", "21:00"]        # Q-004

[lifecycle]
delist_after_missing_complete_runs = 2
relink_window_days = 180

[rules]                          # 規則分數，不是機率；尚未校準（Q-010）
membership_member_threshold = 0.80
membership_likely_threshold = 0.50
address_max_enumerated_candidates = 40
area_tolerance_ping = 1.0
match_review_threshold = 0.50

[evidence]
price_stale_after_days = 14

[retention]
raw_max_days = 90                # 受來源權利限制，Q-007
idempotency_ttl_hours = 24

[api]
host = "127.0.0.1"
port = 8787
```
