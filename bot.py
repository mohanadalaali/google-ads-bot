import os
import time
import logging
from typing import List
from dataclasses import dataclass

import requests
from dotenv import load_dotenv

try:
    from google.ads.googleads.client import GoogleAdsClient
    from google.ads.googleads.errors import GoogleAdsException
    GOOGLE_ADS_AVAILABLE = True
except Exception:
    GOOGLE_ADS_AVAILABLE = False


load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@dataclass
class Alert:
    level: str
    title: str
    body: str


def env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return default


TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID")


def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": str(text)[:3500],
    }

    try:
        r = requests.post(url, json=payload, timeout=20)
        if not r.ok:
            logging.error("Telegram error: %s", r.text)
        r.raise_for_status()
    except Exception as e:
        logging.exception("Failed to send Telegram message: %s", e)


def build_google_client():
    if not GOOGLE_ADS_AVAILABLE:
        raise RuntimeError("google-ads library is not installed")

    developer_token = env("GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_DEVELOPER_TOKEN")
    client_id = env("GOOGLE_CLIENT_ID", "GOOGLE_ADS_CLIENT_ID")
    client_secret = env("GOOGLE_CLIENT_SECRET", "GOOGLE_ADS_CLIENT_SECRET")
    refresh_token = env("GOOGLE_REFRESH_TOKEN", "GOOGLE_ADS_REFRESH_TOKEN")
    login_customer_id = env("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "GOOGLE_LOGIN_CUSTOMER_ID")

    missing = []
    if not developer_token:
        missing.append("GOOGLE_ADS_DEVELOPER_TOKEN")
    if not client_id:
        missing.append("GOOGLE_CLIENT_ID")
    if not client_secret:
        missing.append("GOOGLE_CLIENT_SECRET")
    if not refresh_token:
        missing.append("GOOGLE_REFRESH_TOKEN")

    if missing:
        raise RuntimeError("Missing variables: " + ", ".join(missing))

    config = {
        "developer_token": developer_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "use_proto_plus": True,
    }

    if login_customer_id:
        config["login_customer_id"] = login_customer_id.replace("-", "")

    return GoogleAdsClient.load_from_dict(config)


def get_customer_id() -> str:
    customer_id = env("GOOGLE_ADS_CUSTOMER_ID", "GOOGLE_CUSTOMER_ID")
    if not customer_id:
        raise RuntimeError("Missing GOOGLE_ADS_CUSTOMER_ID")
    return customer_id.replace("-", "")


def check_google_ads() -> str:
    client = build_google_client()
    customer_id = get_customer_id()
    ga_service = client.get_service("GoogleAdsService")

    alerts: List[Alert] = []

    query = """
        SELECT
          campaign.id,
          campaign.name,
          campaign.status,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros
        FROM campaign
        WHERE segments.date DURING LAST_7_DAYS
          AND campaign.status != 'REMOVED'
        LIMIT 50
    """

    response = ga_service.search_stream(customer_id=customer_id, query=query)

    total_clicks = 0
    total_impressions = 0
    total_cost = 0.0
    campaigns = 0

    for batch in response:
        for row in batch.results:
            campaigns += 1
            total_clicks += row.metrics.clicks
            total_impressions += row.metrics.impressions
            total_cost += row.metrics.cost_micros / 1_000_000

            if row.campaign.status.name == "PAUSED":
                alerts.append(Alert("⚠️", "حملة متوقفة", row.campaign.name))

    message = (
        "✅ Google Ads Guard يعمل بنجاح\n\n"
        f"الحساب: {customer_id}\n"
        f"عدد الحملات: {campaigns}\n"
        f"الظهور آخر 7 أيام: {total_impressions}\n"
        f"النقرات آخر 7 أيام: {total_clicks}\n"
        f"التكلفة آخر 7 أيام: {total_cost:.2f}\n"
    )

    if alerts:
        message += "\nتنبيهات:\n"
        for a in alerts[:10]:
            message += f"{a.level} {a.title}: {a.body}\n"
    else:
        message += "\n✅ لا توجد مشاكل واضحة حالياً."

    return message


def handle_command(text: str) -> str:
    text = text.strip().lower()

    if text in ["/start", "start"]:
        return (
            "✅ البوت شغال.\n\n"
            "الأوامر:\n"
            "/check - فحص Google Ads\n"
            "/status - حالة البوت"
        )

    if text in ["/status", "status"]:
        return "✅ Telegram شغال. استخدم /check لفحص Google Ads."

    if text in ["/check", "check"]:
        try:
            return check_google_ads()
        except GoogleAdsException as ex:
            logging.exception("Google Ads API error")
            return "❌ Google Ads API Error:\n" + str(ex)[:3000]
        except Exception as ex:
            logging.exception("Check failed")
            return "❌ Bot failed:\n" + str(ex)[:3000]

    return "اكتب /check للفحص أو /status للحالة."


def telegram_loop() -> None:
    send_telegram("✅ Google Ads Guard بدأ التشغيل بنجاح.")
    offset = None

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset

            r = requests.get(url, params=params, timeout=40)
            r.raise_for_status()

            data = r.json()
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message", {})
                chat_id = str(message.get("chat", {}).get("id", ""))

                if chat_id != str(TELEGRAM_CHAT_ID):
                    continue

                text = message.get("text", "")
                if text:
                    reply = handle_command(text)
                    send_telegram(reply)

        except Exception as e:
            logging.exception("Telegram loop error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    telegram_loop()
