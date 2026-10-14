"""Email alerts over SMTP (Gmail App Password, Outlook, SendGrid SMTP, etc.)."""
from __future__ import annotations

import html
import logging
import smtplib
import ssl
from email.message import EmailMessage

from .models import Alert, clip

log = logging.getLogger(__name__)

STAGE_LABEL = {
    "rumour": "Approach / rumour confirmed",
    "possible_offer": "Possible offer",
    "definitive_agreement": "Definitive deal / firm offer",
    "tender_offer": "Tender offer launched",
    "revised_offer": "Revised offer",
    "competing_offer": "Competing bid",
}
MARKET_FLAG = {"US": "🇺🇸", "UK": "🇬🇧", "EU": "🇪🇺", "IN": "🇮🇳"}


def subject_for(alert: Alert) -> str:
    d = alert.deal
    flag = MARKET_FLAG.get(alert.item.market, "")
    stage = STAGE_LABEL.get(d.stage, d.stage.replace("_", " ").title())
    tgt = d.target_name or alert.item.meta.get("company") or alert.item.title
    tick = f" ({d.target_ticker})" if d.target_ticker and len(d.target_ticker) < 25 else ""
    acq = f" ← {d.acquirer_name}" if d.acquirer_name and d.acquirer_name != "Not yet named" else ""
    price = f" @ {d.currency} {d.price_per_share}".replace("  ", " ") if d.price_per_share else ""
    unverified = " [unverified]" if d.method == "keywords" else ""
    return clip(f"{flag} M&A: {stage} – {tgt}{tick}{acq}{price}{unverified}".strip(), 180)


def _row(label: str, value: str) -> str:
    if not value:
        return ""
    return (f"<tr><td style='padding:4px 12px 4px 0;color:#666;white-space:nowrap;vertical-align:top'>{label}</td>"
            f"<td style='padding:4px 0'>{html.escape(value)}</td></tr>")


def body_for(alert: Alert) -> tuple[str, str]:
    d, it = alert.deal, alert.item
    price = " ".join(x for x in [d.currency, d.price_per_share] if x)
    rows = "".join([
        _row("Target", " ".join(x for x in [d.target_name, f"({d.target_ticker})" if d.target_ticker else ""] if x)),
        _row("Exchange / country", ", ".join(x for x in [d.target_exchange, d.target_country] if x)),
        _row("Acquirer", d.acquirer_name),
        _row("Stage", STAGE_LABEL.get(d.stage, d.stage)),
        _row("Price per share", price),
        _row("Premium", d.premium),
        _row("Consideration", d.consideration),
        _row("Deal value", d.deal_value),
        _row("Public target check", d.listing_check),
        _row("Confidence", f"{d.confidence} ({d.method})"),
        _row("Source", f"{it.source} – {it.title}"),
        _row("Published", it.published),
    ])
    links = [f"<a href='{html.escape(it.url)}'>Open source document</a>"]
    links += [f"<a href='{html.escape(u)}'>{html.escape(t)}</a>" for t, u in alert.extra_links]
    html_body = f"""<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;font-size:14px;color:#111;max-width:640px">
<p style="font-size:15px;margin:0 0 12px">{html.escape(d.summary or it.title)}</p>
<table style="border-collapse:collapse;font-size:14px">{rows}</table>
<p style="margin-top:16px">{' · '.join(links)}</p>
<p style="color:#999;font-size:12px;margin-top:24px">Sent by mna-watch. AI-extracted fields can be wrong – always check the source.</p>
</div>"""
    text_body = "\n".join([
        d.summary or it.title, "",
        f"Target: {d.target_name} {d.target_ticker}".strip(),
        f"Acquirer: {d.acquirer_name}",
        f"Stage: {STAGE_LABEL.get(d.stage, d.stage)}",
        f"Price: {price}" if price else "",
        f"Premium: {d.premium}" if d.premium else "",
        f"Public target check: {d.listing_check}" if d.listing_check else "",
        f"Confidence: {d.confidence} ({d.method})",
        f"Source: {it.url}",
    ])
    return text_body, html_body


def send(settings, alerts: list[Alert]) -> int:
    if not alerts:
        return 0
    if settings.dry_run or not settings.email_ready():
        for a in alerts:
            log.info("[%s] %s | %s", "DRY RUN" if settings.dry_run else "EMAIL NOT CONFIGURED", subject_for(a), a.item.url)
        return 0
    sent = 0
    ctx = ssl.create_default_context()
    try:
        if settings.smtp_port == 465:
            server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=ctx, timeout=30)
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
            server.starttls(context=ctx)
        with server:
            server.login(settings.smtp_user, settings.smtp_password)
            for a in alerts:
                msg = EmailMessage()
                msg["Subject"] = subject_for(a)
                msg["From"] = settings.email_from or settings.smtp_user
                msg["To"] = ", ".join(settings.email_to)
                text_body, html_body = body_for(a)
                msg.set_content(text_body)
                msg.add_alternative(html_body, subtype="html")
                server.send_message(msg)
                sent += 1
    except Exception as e:
        log.error("Email sending failed after %d message(s): %s", sent, e)
    return sent


def send_test(settings) -> None:
    from .models import Deal, Item
    item = Item(source="test", uid="test", title="Test alert from mna-watch", url="https://example.com", market="UK")
    deal = Deal(is_mna=True, target_is_public=True, target_name="Example Target plc", target_ticker="EXT",
                acquirer_name="Example Bidco Ltd", stage="definitive_agreement", currency="GBP",
                price_per_share="2.50", premium="35%", consideration="cash",
                summary="This is a test email. If you can read it, email alerts are working.", confidence="high")
    n = send(settings, [Alert(item=item, deal=deal)])
    print(f"Sent {n} test email(s) to {', '.join(settings.email_to) or '(EMAIL_TO not set)'}")
