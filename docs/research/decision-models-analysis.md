# 決策模型（Jev 類）分析與 JEV 套用推導

> 2026-10-18。依使用者要求查證 TypeSafe Jev、OpenAI Decisions API 與開放權重的決策模型。只做唯讀查詢，**沒有呼叫任何模型 API、沒有下載模型**。
> 可信度標記：**[官方]** 廠商自己的文件；**[廠商宣稱]** 廠商公布、未經獨立驗證的數字；**[二手]** 媒體或第三方整理；【待查證】需要原文或實測。

## 1. 這類模型是什麼
「決策模型」（TypeSafe 稱為 System One）不生成文字。應用程式給一段**狀態**（文字，部分模型也收圖片）和一組**有型別的問題**，模型一次回傳每題的答案與機率：

| 題型 | Jev 名稱 | OpenAI 名稱 | 回傳 |
|------|----------|-------------|------|
| 是非 | `noul` | `predicate` | 為真的機率（0–1） |
| 選擇 | `choice` | `choice` | 選項＋各選項機率＋信心 |
| 等級 | `score` | `score` | 期望等級＋各等級機率＋信心 |

Jev 的 API 格式（以 POST 呼叫 `/v1/systemone`，`model`／`state`／`questions`）已成為事實標準：Cloudflare Clef 與 H2O-Lightning 的服務程式都相容這個格式 [官方：docs.typesafe.ai；H2O 模型卡]。

## 2. 候選模型
| 項目 | TypeSafe Jev | OpenAI Decisions | Cloudflare Clef／Clef-flash | H2O-Lightning-4B |
|------|--------------|------------------|-----------------------------|------------------|
| 發布 | 2026-09-15（1.14 預覽：10-14） | DevDay 9-29 預覽；10-06 公開測試版 [二手] | 2026-10-01 [二手] | 2026-10 |
| 權重 | 封閉（只有 API） | 封閉（`gpt-6-luna`） | **開放**，Apache 2.0（訓練資料未公開）[二手] | **開放**，Apache 2.0 [官方模型卡] |
| 輸入 | **只有文字** [官方] | 文字＋圖片（內嵌 base64，每次最多 128 張）[二手] | 文字、JSON、圖片（每次 4 張）[二手] | 文字＋圖片（每次 4 張）[官方模型卡] |
| 上下文 | 每題 32k、每次 64k；長上下文模式會變慢、變不準 [官方] | 【待查證】 | 64k（Clef）[二手] | 40,960 [官方模型卡] |
| 價格（每百萬輸入 token） | US$0.042，輸出免費 [官方] | US$0.10 [二手] | Clef 0.24、Clef-flash 0.09 [二手] | 自架免費；託管版依平台 |
| 取得 | 候補名單；或 Cloudflare Workers AI `typesafe/jev` | OpenAI **API 帳號**（與 ChatGPT 訂閱分開）| Cloudflare 帳號；或自架 | 自架（Hugging Face）；或 Featherless 等託管 |
| 本機需求 | — | — | Clef-flash ≥ 41 GB、Clef ≥ 85 GB 顯示記憶體 [二手] | BF16 約 9.3 GB 權重＋快取；有 GGUF 與 4-bit 版（約 3 GB）[官方模型卡] |
| 語言 | 【待查證】 | 【待查證】 | 【待查證】 | **微調資料 > 99% 英文**，中文未評估 [官方模型卡] |
| 資料政策 | 不用客戶資料訓練；企業版可零保存 [官方] | 【待查證】 | 【待查證】 | 自架則資料不出本機 |

**ChatGPT Plus 不能用**：OpenAI 官方說明，Plus 不含 API 使用量，API 要另外開帳號、另外計費 [官方：help.openai.com]。

## 3. 評測（都不是 JEV 的題目）
| 評測 | 結果 | 性質 |
|------|------|------|
| Artificial Analysis Decision Index v1.0（10-13） | Claude Haiku 5.5（Low）61、**Jev 1.13 58**、GPT-6 Luna（Low）55、GLiDE 54、Microsoft-Decision-1 54 | 第三方；四項英文評測（知識與幻覺、推理選擇題、工具呼叫、有害內容分類）[二手轉引] |
| 同上，校準分析 | 決策模型在知識、推理類題目普遍**過度自信**，在分類類題目較準 | 第三方 |
| JevBench 開放權重榜 | H2O-Lightning-4B v1.1 72.5（第 1）、Jev 1.13.0 71.5（參考列，不排名） | 單一第三方網站；分數由 H2O 模型卡轉述 |
| BANKING77 | Clef 94.20 vs. Jev 79.74（macro-F1） | **Cloudflare 自己測的** |
| Jev 延遲 | 70–500 ms；Clef-flash 中位數 38.8 ms vs. Jev 524.1 ms | 廠商宣稱 |

**結論**：沒有任何評測涵蓋繁體中文房地產文字，也沒有涵蓋「從室內照片判斷格局方向」。誰最強**只能用 JEV 自己的題目實測**。

## 4. 已知弱點（直接影響 JEV 的設計）
1. **數字、計數、日期不可靠**：TypeSafe 自己的 jaggedness 頁（Jev 1.13，9-17）列出計數、算術、日期處理不可靠 [二手轉引官方]；建議數字邏輯留在程式碼裡。
2. **多步推理差**：LessWrong 的評測中，Jev 在多步推理題約為 GPT-3.5 水準 [二手]。
3. **高信心的錯誤**：格式保證不等於答案正確；校準要用自己的資料驗證 [多個來源]。
4. **提示注入與對抗文字**：HiddenLayer（2026-10）指出 Jev 容易被 state 裡的指令和 TokenBreak 式改寫誤導，有些手法對一般 LLM 無效、對 Jev 有效；TypeSafe 未回應 [二手：InfoWorld]。刊登標題與說明文字是仲介寫的，屬於**不可信輸入**。
5. **與無關內容的混雜**：state 裡無關內容越多，準確度越低 [二手轉引官方]。

## 5. 推導：JEV 的哪些判斷適合交給決策模型
從 JEV 現有的判斷逐一檢查（依 §4 的弱點）：

| JEV 的判斷 | 目前做法 | 適合決策模型？ | 理由 |
|------------|----------|----------------|------|
| 坪數比對、車位扣除、樓層區間 | 規則（G5、S3） | **不適合** | 算術與數字比對是已知弱點（§4-1） |
| 門牌確認（H1–H6、K1–K5）、硬排除（X 規則） | 規則 | **不適合** | 需要可追溯的證據；模型高信心錯誤（§4-3）不能當證據 |
| **鏡像戶辨識**（D2／D5、C1／C2、A1／A5）：從刊登照片判斷格局方向 | 人工比對照片（S9／S10，D-016 第一階段） | **最適合**，也是最大瓶頸 | 坪數相同、只能看照片；屬於「看圖選一個」的選擇題。需要支援圖片的模型（OpenAI、Clef、H2O；**Jev 不行**） |
| 社區歸屬的文字線索（「近聯上世界」、文案提到的社區） | 規則（M3、N4，固定字詞） | 適合當輔助 | 語意判斷是強項；但仲介文字是不可信輸入（§4-4），只能加減分 |
| 同屋辨識的文字線索（兩則說明文字是否描述同一戶） | 規則（坪數、樓層、房數） | 適合當輔助 | 語意比對；不能自動合併（D-104 不變） |
| 書籤讀不到欄位時，從說明文字整理欄位 | 規則比對（parse591.js） | 可以，但數字欄位要再用規則驗證 | §4-1 |

**放進現有架構的方式**（不推翻規格；10 的 T-PHOTO-06 早已預留）：
- 模型的答案存成 `INFERENCE` 證據，記錄模型名稱、版本、問題、各選項機率。
- 只能**加減分**（例如鏡像判斷的權重上限等同 S9 +0.3），**不能硬排除、不能讓門牌變成 CONFIRMED**。
- `calibrated_probability` 一律維持空白，直到用 JEV 自己的真值資料驗證過校準（§6），才填入。
- 刊登文字、照片一律視為不可信輸入；問題的選項由 JEV 固定，不讓模型自由選動作（呼應 §4-4 分析師的建議）。

## 6. 關鍵前提：沒有真值就無法比較模型
目前 **0 則刊登有確認的門牌**，所以無法知道哪個模型答對。實測前要先有一組真值題目：
1. **格局圖題（不需外部資料，現在就能做）**：樂居與 591 的平面圖上已標示戶別，可以裁成「這張圖是 D2 還是 D5」這類已知答案的題目，測模型的鏡像辨識。圖片只放暫存區，不放進 repo。
2. **現場確認**：你看屋時確認的門牌（H1–H6），每一則都會變成一題真值。
3. **人工標註的照片題**：你比對過照片、有把握的刊登。

## 7. 你的電腦能不能在本機跑
- 顯示卡：AMD Radeon 內建顯示（2 GB）；記憶體 13.8 GB；沒有 NVIDIA GPU。
- Clef／Clef-flash：**不行**（需要 41 GB 以上顯示記憶體）。
- H2O-Lightning-4B：模型卡只在 GPU 上測過。4-bit GGUF 約 3 GB，CPU 上**可能**跑得動但會慢；決策機率要靠模型卡的服務程式取 logits，CPU／GGUF 下能不能正常運作【待查證】，要實際下載（約 3–9 GB）試了才知道。另外它的中文能力未評估。

## 8. 建議的實測順序（需要你決定的事標 ★）
1. **先做真值題目**（§6-1，格局圖題）：本機即可，不送任何資料出去。
2. ★ **OpenAI Decisions**：門檻最低，你建 API 帳號、儲值少量金額即可（與 Plus 分開計費）；支援圖片，可以直接測鏡像辨識。JEV 的資料量很小（十幾則刊登），估計每月花費在 1 美元以下【待實測】。
3. ★ **H2O-Lightning-4B 本機試跑**：資料不出本機，但要下載數 GB、建立獨立的 conda 環境；速度與中文能力未知。
4. ★ **Jev**：要排候補名單或開 Cloudflare 帳號；只支援文字，無法做照片題，只能測文字類判斷。
5. 同一組題目跑完後，比較答對率和**校準**（說 80% 的時候是否真的約 80% 答對），再決定要不要接進 JEV，以及接哪一個。

JEV 的程式可以用同一個介面接這幾個模型：Jev、Clef、H2O 都相容 `/v1/systemone` 格式，OpenAI 用 `/v1/decisions`，差異很小。

## 9. 來源
- TypeSafe 官方文件：[Quick start](https://docs.typesafe.ai/introduction/quickstart)、[Models](https://docs.typesafe.ai/models)
- [Let's Data Science：TypeSafe AI Launches Jev](https://letsdatascience.com/news/typesafe-ai-launches-jev-decision-model-889a38c0)
- [eesel：TypeSafe Jev review](https://www.eesel.ai/blog/typesafe-jev-review)（作者有競品）
- [InfoWorld：HiddenLayer 對 Jev 的資安發現](https://www.infoworld.com/article/4236103/typesafes-jev-even-more-susceptible-to-attack-than-llms-says-security-vendor.html)
- [Artificial Analysis Decision Index v1.0](https://artificialanalysis.ai/articles/artificial-analysis-decision-index)
- [JevBench（Benchmark Heaven）](https://benchmarkheaven.com/jev-models)
- [H2O-Lightning-4B 模型卡](https://huggingface.co/h2oai/h2o-lightning-4b)、[H2O 新聞稿](https://www.financialcontent.com/article/bizwire-2026-10-12-h2o-lightning-4b-ranks-first-on-jevbench-open-weights-leaderboard)
- [Crypto Briefing：Cloudflare Clef](https://cryptobriefing.com/cloudflare-clef-decision-models-workers-ai/)、[Let's Data Science：Clef open-weight](https://letsdatascience.com/news/cloudflare-releases-open-weight-clef-decision-models-c52886d6)
- [Let's Data Science：OpenAI Decisions API 公開測試版](https://letsdatascience.com/news/openai-opens-decisions-api-public-beta-b69a1e40)
- [OpenAI：What is ChatGPT Plus?](https://help.openai.com/en/articles/6950777-what-is-chatgpt-plus%3F)
- [SmartScope：Jev jaggedness](https://smartscope.blog/en/blog/jev-jaggedness-safety-patterns-2026/)、[LessWrong：Benchmarking Jev against no-CoT LLMs](https://www.lesswrong.com/posts/QW2kPrAyZQvRtBXHG/benchmarking-jev-against-no-cot-llms)
