import os
import smtplib
import logging
from email.mime.text import MIMEText
from dataclasses import dataclass
from typing import List, Dict, Any

import requests
from dotenv import load_dotenv
try:
    from google.ads.googleads.client import GoogleAdsClient
    from google.ads.googleads.errors import GoogleAdsException
    GOOGLE_ADS_ENABLED = True
except:
    GOOGLE_ADS_ENABLED = False

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

@dataclass
class Alert:
    level: str
    title: str
    body: str
    risk: int


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def build_google_ads_client() -> GoogleAdsClient:
    config = {
        "developer_token": env("GOOGLE_ADS_DEVELOPER_TOKEN"),
        "client_id": env("GOOGLE_ADS_CLIENT_ID"),
        "client_secret": env("GOOGLE_ADS_CLIENT_SECRET"),
        "refresh_token": env("GOOGLE_ADS_REFRESH_TOKEN"),
        "use_proto_plus": True,
    }
    login_customer_id = env("GOOGLE_ADS_LOGIN_CUSTOMER_ID")
    if login_customer_id:
        config["login_customer_id"] = login_customer_id
    return GoogleAdsClient.load_from_dict(config)


def send_telegram(text: str) -> None:
    token = env("TELEGRAM_BOT_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logging.warning("Telegram credentials missing; skipping Telegram alert")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=20)
    response.raise_for_status()


def send_email(subject: str, body: str) -> None:
    host = env("SMTP_HOST")
    user = env("SMTP_USER")
    password = env("SMTP_PASSWORD")
    to_addr = env("EMAIL_TO")
    if not host or not user or not password or not to_addr:
        return
    port = int(env("SMTP_PORT", "587"))
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    with smtplib.SMTP(host, port, timeout=20) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(msg)


def notify(alerts: List[Alert], total_risk: int) -> None:
    if not alerts:
        send_telegram("✅ Google Ads Guard: لا توجد مشاكل خطرة حالياً.")
        return
    lines = [f"🛡️ <b>Google Ads Guard Alert</b>", f"Risk Score: <b>{total_risk}</b>", ""]
    for a in alerts[:20]:
        lines.append(f"{a.level} <b>{a.title}</b> (+{a.risk})")
        lines.append(a.body[:900])
        lines.append("")
    text = "\n".join(lines)
    send_telegram(text)
    send_email("Google Ads Guard Alert", text.replace("<b>", "").replace("</b>", ""))


def load_risky_keywords() -> List[str]:
    try:
        with open("risky_keywords.txt", "r", encoding="utf-8") as f:
            return [x.strip() for x in f if x.strip()]
    except FileNotFoundError:
        return []


def check_ads(client: GoogleAdsClient, customer_id: str) -> List[Alert]:
    ga_service = client.get_service("GoogleAdsService")
    alerts: List[Alert] = []
    risky_keywords = load_risky_keywords()

    query = """
        SELECT
          campaign.id,
          campaign.name,
          campaign.status,
          ad_group.id,
          ad_group.name,
          ad_group_ad.ad.id,
          ad_group_ad.status,
          ad_group_ad.policy_summary.approval_status,
          ad_group_ad.ad.responsive_search_ad.headlines,
          ad_group_ad.ad.responsive_search_ad.descriptions
        FROM ad_group_ad
        WHERE ad_group_ad.status != 'REMOVED'
        LIMIT 500
    """
    response = ga_service.search_stream(customer_id=customer_id, query=query)

    disapproved_count = 0
    limited_count = 0

    for batch in response:
        for row in batch.results:
            approval = row.ad_group_ad.policy_summary.approval_status.name
            campaign_name = row.campaign.name
            ad_id = row.ad_group_ad.ad.id

            if approval == "DISAPPROVED":
                disapproved_count += 1
                alerts.append(Alert(
                    "🚨", "إعلان مرفوض",
                    f"Campaign: {campaign_name}\nAd ID: {ad_id}\nStatus: {approval}",
                    50
                ))
            elif approval == "APPROVED_LIMITED":
                limited_count += 1
                alerts.append(Alert(
                    "⚠️", "إعلان محدود",
                    f"Campaign: {campaign_name}\nAd ID: {ad_id}\nStatus: {approval}",
                    20
                ))

            ad = row.ad_group_ad.ad
            texts = []
            if ad.responsive_search_ad:
                texts += [h.text for h in ad.responsive_search_ad.headlines]
                texts += [d.text for d in ad.responsive_search_ad.descriptions]
            full_text = " ".join(texts)
            for kw in risky_keywords:
                if kw and kw in full_text:
                    alerts.append(Alert(
                        "🟠", "كلمة خطرة في الإعلان",
                        f"Keyword: {kw}\nCampaign: {campaign_name}\nAd ID: {ad_id}\nText: {full_text[:300]}",
                        10
                    ))
                    break

    if disapproved_count > int(env("DISAPPROVED_LIMIT", "2")):
        alerts.append(Alert("🛑", "عدد إعلانات مرفوضة عالي", f"Disapproved ads: {disapproved_count}", 40))
    if limited_count > int(env("APPROVED_LIMITED_LIMIT", "5")):
        alerts.append(Alert("⚠️", "عدد إعلانات محدودة عالي", f"Limited ads: {limited_count}", 25))
    return alerts


def check_campaign_costs(client: GoogleAdsClient, customer_id: str) -> List[Alert]:
    ga_service = client.get_service("GoogleAdsService")
    alerts: List[Alert] = []
    query = """
        SELECT
          campaign.id,
          campaign.name,
          campaign.status,
          metrics.cost_micros,
          segments.date
        FROM campaign
        WHERE segments.date DURING LAST_7_DAYS
          AND campaign.status != 'REMOVED'
        ORDER BY segments.date DESC
    """
    data: Dict[int, Dict[str, Any]] = {}
    response = ga_service.search_stream(customer_id=customer_id, query=query)
    for batch in response:
        for row in batch.results:
            cid = row.campaign.id
            data.setdefault(cid, {"name": row.campaign.name, "costs": []})
            data[cid]["costs"].append(row.metrics.cost_micros / 1_000_000)

    threshold = float(env("DAILY_COST_SPIKE_PERCENT", "60"))
    for cid, info in data.items():
        costs = info["costs"]
        if len(costs) < 3:
            continue
        today = costs[0]
        avg_prev = sum(costs[1:]) / max(len(costs[1:]), 1)
        if avg_prev > 0 and today > avg_prev * (1 + threshold / 100):
            alerts.append(Alert(
                "📈", "ارتفاع تكلفة مفاجئ",
                f"Campaign: {info['name']}\nToday cost: {today:.2f}\nPrevious avg: {avg_prev:.2f}",
                20
            ))
    return alerts


def pause_enabled_campaigns(client: GoogleAdsClient, customer_id: str) -> None:
    # Safety action: pauses enabled campaigns when total risk crosses threshold.
    # Keep AUTO_PAUSE=false until you test alerts for several days.
    ga_service = client.get_service("GoogleAdsService")
    campaign_service = client.get_service("CampaignService")
    query = """
        SELECT campaign.resource_name, campaign.name, campaign.status
        FROM campaign
        WHERE campaign.status = 'ENABLED'
        LIMIT 50
    """
    operations = []
    response = ga_service.search_stream(customer_id=customer_id, query=query)
    for batch in response:
        for row in batch.results:
            op = client.get_type("CampaignOperation")
            campaign = op.update
            campaign.resource_name = row.campaign.resource_name
            campaign.status = client.enums.CampaignStatusEnum.PAUSED
            client.copy_from(op.update_mask, client.get_type("FieldMask")(paths=["status"]))
            operations.append(op)
    if operations:
        campaign_service.mutate_campaigns(customer_id=customer_id, operations=operations)
        send_telegram(f"🛑 AUTO_PAUSE: تم إيقاف {len(operations)} حملة بسبب ارتفاع Risk Score.")


def main() -> None:
    customer_id = env("GOOGLE_ADS_CUSTOMER_ID")

    if not customer_id:
        notify(
            [Alert("⚠️", "Google Ads غير مربوط بعد",
                   "أضف GOOGLE_ADS_CUSTOMER_ID وباقي مفاتيح Google Ads API داخل Railway Variables.",
                   10)],
            10
        )
        return

    if not GOOGLE_ADS_ENABLED:
        notify(
            [Alert("⚠️", "Google Ads Library غير مثبتة",
                   "تأكد أن google-ads موجودة داخل requirements.txt.",
                   10)],
            10
        )
        return

    client = build_google_ads_client()
    alerts: List[Alert] = []
    alerts += check_ads(client, customer_id)
    alerts += check_campaign_costs(client, customer_id)

    total_risk = sum(a.risk for a in alerts)
    notify(alerts, total_risk)

    if env("AUTO_PAUSE", "false").lower() == "true" and total_risk >= int(env("RISK_THRESHOLD", "100")):
        pause_enabled_campaigns(client, customer_id)


if __name__ == "__main__":
    try:
        main()
    except GoogleAdsException as ex:
        error = f"Google Ads API Error: {ex.failure}"
        logging.exception(error)
        send_telegram("❌ " + error[:3500])
    except Exception as ex:
        logging.exception("Bot failed")
        send_telegram("❌ Bot failed: " + str(ex)[:3500])
