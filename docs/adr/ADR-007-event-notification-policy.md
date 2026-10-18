# ADR-007 事件保存與通知分開；初始化資料不當成新案

- 狀態：**提議中**（初始化不當成新案通知是使用者需求【已確認】D-010；具體設計是【建議預設】）
- 日期：2026-10-18（v0.3）

## 背景
v0.2 中，初始化資料不會發 `LISTING_NEW_CONFIRMED`；但兩則初始化資料（舊刊登）如果之後才合併，會發 `DWELLING_NEW_LISTING` 並通知，與「初始化不通知」矛盾。冪等鍵也包含 `operation_id`，所以撤回後再合併會重複通知。

## 決策（提議）
1. 事件信封加上 `notify_eligible` 與 `suppression_reason`。**事件一律保存**；只有 `notify_eligible = true` 的事件才交給 Notifier（12 §2.1）。
2. 區分兩種語意：
   - `PROPERTY_MERGED`：系統發現新的同屋關係，只保存，不通知。
   - `DWELLING_NEW_LISTING`：同一住宅「真正新增」的刊登。條件是加入的刊登本身是 `CONFIRMED_NEW`（不是 BASELINE、MANUAL_INITIAL），而且群組中有更早首次觀測的其他刊登；兩者都成立的那一刻才發出。
3. 是否屬於初始化資料，看刊登自己的 `first_observed_context` 與 `new_listing_status`（也帶入事件 payload），**不看合併的時間**。schema 也禁止 `joining_listing_context` 是初始化值。
4. 冪等鍵是 `dwelling_new_listing:{listing_id}`：每則刊登一生最多一次。不使用 operation_id，也**不依賴可變的群組 anchor**（v0.3.1 修訂：v0.3 曾用 anchor，拆分後以不同成員重組時 anchor 會改變，可能重發）。
5. 之後的拆分、撤回、再合併（不論回到同一群組或改併到其他群組），一律以引用原通知的更正通知（`notification_kind = CORRECTION`）處理，不再當成新案。

## 後果
- 好處：初始化期間沒有誤報；稽核紀錄完整；冪等規則不依賴操作編號。
- 壞處：同一則真正新增的刊登，可能先後收到「新刊登」與「同一住宅新刊登」兩則通知（由通知規則決定是否都訂閱）。
- 取捨：如果某則刊登第一次被併入的群組是錯的，之後改併到正確的群組，使用者收到的是更正通知，而不是第二則新案通知；新案通知的內容快照仍然保留原本的群組，供追溯。
