// 591 刊登頁 → JEV 記錄。只讀使用者畫面上已經顯示的內容（一次一頁），不連網、不讀照片。
// 只取房屋欄位；不擷取仲介姓名、電話等個人資料。
// 輸入：{href, title, text, ld: [JSON-LD 物件], links: [{href, text}]}
function parse591(page) {
  const warn = [];
  const text = (page.text || "").replace(/ /g, " ");
  const num = s => (s == null || s === "" ? null : Number(String(s).replace(/,/g, "")));
  const first = (re, i = 1) => { const m = text.match(re); return m ? m[i] : null; };

  const idm = (page.href || "").match(/detail\/\d+\/(\d+)\.html/);
  const nodes = [];
  for (const j of page.ld || []) for (const g of [].concat(j["@graph"] || j)) nodes.push(g);
  const prod = nodes.find(g => [].concat(g["@type"] || []).includes("Product")) || {};
  if (!prod.offers) warn.push("找不到結構化資料（JSON-LD），總價與坪數改由文字判讀");

  const rec = {
    source_code: "SRC-591",
    external_id: idm ? idm[1] : (prod.sku || "").replace(/^S/, "") || null,
    url: idm ? `https://sale.591.com.tw/home/house/detail/2/${idm[1]}.html` : (prod.url || null),
    observed_at: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
    title: (prod.name || page.title || "").replace(/\s*-\s*591售屋網\s*$/, "").trim() || null,
  };

  // 總價（元）：JSON-LD offers.price；否則「總價…2,188萬」
  rec.total_price = num(prod.offers && prod.offers.price);
  if (!rec.total_price) { const w = first(/總價[^0-9]{0,12}([\d,]+(?:\.\d+)?)\s*萬/); rec.total_price = w ? Math.round(num(w) * 10000) : null; }

  // 坪數：JSON-LD 會四捨五入；文字中如果有同一個數字的更多位數就採用
  const ldPing = num(prod.floorSize && prod.floorSize.value);
  const pings = [...text.matchAll(/(?:權狀坪數|坪數|面積)\s*[:：]?\s*([\d.]+)\s*坪/g)].map(m => num(m[1]));
  const precise = (vals, ref) => vals.filter(p => ref == null || Math.abs(p - ref) < 0.01).sort((a, b) => String(b).length - String(a).length)[0];
  rec.building_ping = precise(pings, ldPing) ?? ldPing ?? null;
  rec.main_ping = num(first(/主建物\s*[:：]?\s*([\d.]+)\s*坪/));
  const parks = [...text.matchAll(/(?:車位\s*[:：]?|含車位)\s*([\d.]+)\s*坪/g)].map(m => num(m[1]));
  rec.parking_ping = parks.length ? precise(parks, parks[0]) : null;
  rec.parking_included = /含車位價格|已含售金|總價（含車位）|坪數[^。\n]{0,8}含車位/.test(text) ? "INCLUDED"
    : /不含車位/.test(text) ? "EXCLUDED" : "INCLUDED";  // 591 的標示方式預設含車位（D-016）

  // 樓層：「9F/14F」「9樓／14樓」「9樓/共14樓」
  const fm = text.match(/(?:樓層\s*[:：]?\s*)?(B?\d+(?:~\d+)?|頂樓)\s*(?:F|樓)\s*[/／]\s*(?:共)?\s*(\d+)\s*(?:F|樓)/);
  rec.floor_text = fm ? `${fm[1]}樓/${fm[2]}樓` : null;
  if (!rec.floor_text) warn.push("樓層未揭露或找不到：門牌只能縮小到戶別");

  const lay = text.match(/(\d+)\s*房\s*(\d+)\s*廳\s*(\d+)\s*衛/);
  rec.rooms = lay ? num(lay[1]) : num(prod.numberOfRooms);
  rec.halls = lay ? num(lay[2]) : null;
  rec.baths = lay ? num(lay[3]) : null;
  rec.age_years = num(first(/屋齡\s*[:：]?\s*([\d.]+)\s*年/));
  rec.orientation = (first(/(?:朝向|座向|落地窗)\s*[:：]?\s*((?:坐)?[東西南北]{1,2}(?:朝[東西南北]{1,2})?)/) || null);

  const addr = prod.address || {};
  rec.address_text = addr.streetAddress || first(/(桃園市大園區[^\s，,|]{2,20}?(?:路|街)(?:[一二三四五六七八九十]段)?)/) || null;
  // 社區：以 591 自己的結構化描述「位於○○，」為準；頁面上還有「熱門社區」等推薦連結，不能用
  const loc = (prod.description || "").match(/位於([^，,。]{2,20})[，,。]/);
  rec.community_text = loc ? loc[1].trim() : (first(/(?:^|\n)\s*社區\s*[:：]\s*([^\s，,|｜]{2,12})/) || null);
  const cl = (page.links || []).find(l => !l.recommended && /market\.591\.com\.tw\/(\d+)/.test(l.href) && rec.community_text
                                          && (l.text || "").trim() === rec.community_text);
  rec.platform_community_ref = cl ? "591:" + cl.href.match(/market\.591\.com\.tw\/(\d+)/)[1] : null;
  if (!rec.platform_community_ref) warn.push("找不到 591 社區連結：請確認這則刊登掛在哪個 591 社區（聯上世界是 591:9488）");

  rec.lister_type = /（\s*仲介|仲介，收取服務費|經紀業/.test(text) ? "AGENT" : /屋主/.test(text) ? "OWNER" : "UNKNOWN";
  rec.lister_company = first(/經紀業(?:名稱)?\s*[:：]\s*([^\s|｜]{2,30}?(?:有限公司|股份有限公司|公司))/);
  if (!rec.total_price) warn.push("找不到總價");
  // 照片網址（JSON-LD 的 image）：只記網址；要請 JEV 判斷時，由使用者勾選後才下載
  const photos = [].concat(prod.image || []).map(x => (typeof x === "string" ? x : x && x.url)).filter(u => /^https:\/\/img\d*\.591\.com\.tw\//.test(u || "")).slice(0, 20);
  return {record: rec, warnings: warn, photos};
}
if (typeof module !== "undefined") module.exports = {parse591};
