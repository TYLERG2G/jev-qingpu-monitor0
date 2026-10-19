"""設定：讀取專案根目錄的 .env（標準函式庫，不依賴 python-dotenv）。環境變數優先於 .env。

決策模型（D-020）預設關閉：JEV_DECISION_PROVIDER=none，而且文字、圖片外送都要另外允許。
"""

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
PROVIDERS = ("none", "openai", "openrouter", "typesafe")
TRUE = {"1", "true", "yes", "on"}


def parse_env(text):
    """解析 .env：KEY=VALUE，# 開頭是註解；值可以用單或雙引號包住。"""
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        out[key] = value
    return out


def load_env(path=ENV_FILE, environ=None):
    """把 .env 的值放進環境變數（已存在的環境變數不覆蓋）。回傳 .env 中出現的鍵。"""
    environ = os.environ if environ is None else environ
    p = Path(path)
    if not p.exists():
        return []
    values = parse_env(p.read_text(encoding="utf-8-sig"))
    for k, v in values.items():
        if k not in environ:
            environ[k] = v
    return sorted(values)


def mask(secret):
    if not secret:
        return "（未設定）"
    return secret[:4] + "…" + secret[-2:] if len(secret) > 10 else "（已設定）"


@dataclass(frozen=True)
class DecisionProvider:
    name: str
    base_url: str
    api_key: str
    model: str
    wire_format: str  # systemone（Jev 格式）或 decisions（OpenAI 格式）
    supports_images: bool
    extra_headers: tuple = ()
    endpoint_override: str = ""

    @property
    def endpoint(self):
        if self.endpoint_override:
            return self.endpoint_override
        return self.base_url.rstrip("/") + ("/systemone" if self.wire_format == "systemone" else "/decisions")


@dataclass(frozen=True)
class DecisionSettings:
    provider: DecisionProvider | None
    allow_text: bool
    allow_images: bool
    timeout_sec: float
    problems: tuple
    monthly_budget_usd: float | None = 1.0

    @property
    def enabled(self):
        return self.provider is not None and not self.problems and (self.allow_text or self.allow_images)


def decision_settings(environ=None):
    """依環境變數組出決策模型設定；有問題時 provider 照樣回傳，但 problems 不為空、enabled = False。"""
    e = os.environ if environ is None else environ
    get = lambda k, d="": (e.get(k) or d).strip()
    name = get("JEV_DECISION_PROVIDER", "none").lower()
    allow_text = get("JEV_DECISION_ALLOW_TEXT", "false").lower() in TRUE
    allow_images = get("JEV_DECISION_ALLOW_IMAGES", "false").lower() in TRUE
    problems = []
    try:
        timeout = float(get("JEV_DECISION_TIMEOUT_SEC", "30"))
    except ValueError:
        timeout, problems = 30.0, problems + ["JEV_DECISION_TIMEOUT_SEC 不是數字"]
    raw_budget = get("JEV_DECISION_MONTHLY_BUDGET_USD", "1")
    try:
        budget = None if raw_budget.lower() in ("", "none", "unlimited") else float(raw_budget)
    except ValueError:
        budget, problems = 1.0, problems + ["JEV_DECISION_MONTHLY_BUDGET_USD 不是數字"]
    if name not in PROVIDERS:
        return DecisionSettings(None, allow_text, allow_images, timeout,
                                tuple(problems + [f"JEV_DECISION_PROVIDER 只能是 {'、'.join(PROVIDERS)}，目前是 {name!r}"]), budget)
    if name == "none":
        return DecisionSettings(None, allow_text, allow_images, timeout, tuple(problems), budget)
    if name == "openai":
        p = DecisionProvider("openai", get("OPENAI_BASE_URL", "https://api.openai.com/v1"), get("OPENAI_API_KEY"),
                             get("OPENAI_DECISION_MODEL", "gpt-6-luna"), "decisions", True)
        key_var = "OPENAI_API_KEY"
    elif name == "openrouter":
        wire = get("OPENROUTER_WIRE_FORMAT", "systemone").lower()
        if wire not in ("systemone", "decisions"):
            problems.append("OPENROUTER_WIRE_FORMAT 只能是 systemone 或 decisions")
            wire = "systemone"
        headers = (("X-Title", get("OPENROUTER_APP_TITLE", "JEV")),)
        # OpenRouter 文件：System One 格式在 /api/v1/systemone；Decisions 格式在 /api/alpha/decisions（alpha 路徑，不在 v1 底下）
        override = get("OPENROUTER_DECISIONS_URL", "https://openrouter.ai/api/alpha/decisions") if wire == "decisions" else ""
        p = DecisionProvider("openrouter", get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"), get("OPENROUTER_API_KEY"),
                             get("OPENROUTER_DECISION_MODEL"), wire, True, headers, override)  # Jev 1.14 經 OpenRouter 可收圖片（早期功能）
        key_var = "OPENROUTER_API_KEY"
        if not p.model:
            problems.append("OPENROUTER_DECISION_MODEL 未設定")
    else:
        p = DecisionProvider("typesafe", get("TYPESAFE_BASE_URL", "https://api.typesafe.ai/v1"), get("TYPESAFE_API_KEY"),
                             get("TYPESAFE_MODEL", "jev-latest"), "systemone", False)
        key_var = "TYPESAFE_API_KEY"
    if not p.api_key:
        problems.append(f"{key_var} 未設定")
    if not p.endpoint.startswith("https://"):
        problems.append(f"{p.name} 的端點必須是 https://")
    if allow_images and not p.supports_images:
        problems.append(f"{p.name} 不支援圖片（TypeSafe Jev 只收文字）；請把 JEV_DECISION_ALLOW_IMAGES 設為 false 或換 provider")
    return DecisionSettings(p, allow_text, allow_images, timeout, tuple(problems), budget)


def report(environ=None, env_keys=None):
    """`python -m jev config` 的輸出（金鑰遮蔽）。"""
    e = os.environ if environ is None else environ
    s = decision_settings(e)
    lines = [f".env：{'已讀取（' + str(len(env_keys)) + ' 個鍵）' if env_keys else '沒有（使用環境變數與預設值；範本見 .env.example）'}",
             f"本機 API 金鑰：{'自訂 ' + mask(e.get('JEV_API_KEY')) if e.get('JEV_API_KEY') else '預設 dev-local-key（只限 127.0.0.1）'}",
             f"資料庫：{e.get('JEV_DB') or 'data/jev.db'}",
             f"通知：Email {'SMTP ' + e['JEV_SMTP_HOST'] if e.get('JEV_SMTP_HOST') else '未設定（寫入 outbox）'}；"
             f"Telegram {'已設定' if e.get('JEV_TELEGRAM_BOT_TOKEN') and e.get('JEV_TELEGRAM_CHAT_ID') else '未設定（寫入 outbox）'}",
             "", "決策模型："]
    for name, key in (("openai", "OPENAI_API_KEY"), ("openrouter", "OPENROUTER_API_KEY"), ("typesafe", "TYPESAFE_API_KEY")):
        lines.append(f"  {name:<10} 金鑰 {mask(e.get(key))}")
    if s.provider is None:
        lines.append("  使用中：none（不呼叫任何外部模型）")
    else:
        p = s.provider
        lines.append(f"  使用中：{p.name}｜模型 {p.model or '（未設定）'}｜格式 {p.wire_format}｜端點 {p.endpoint}｜圖片 {'可' if p.supports_images else '不可'}")
    lines.append(f"  允許外送：文字 {'是' if s.allow_text else '否'}、圖片 {'是' if s.allow_images else '否'}｜逾時 {s.timeout_sec:g} 秒"
                 f"｜每月上限 {'不限' if s.monthly_budget_usd is None else f'US${s.monthly_budget_usd:g}'}")
    for prob in s.problems:
        lines.append(f"  ⚠ {prob}")
    lines.append("  狀態：" + ("已啟用" if s.enabled else "未啟用（不會送出任何資料）"))
    return "\n".join(lines)
