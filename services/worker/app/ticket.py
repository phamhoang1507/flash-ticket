import hashlib
import hmac
import io
import json

import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def create_ticket_pdf(
    booking_id: str,
    event_id: str,
    quantity: int,
    signing_secret: str,
) -> bytes:
    payload = {
        "booking_id": booking_id,
        "event_id": event_id,
        "quantity": quantity,
    }

    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    signature = hmac.new(
        signing_secret.encode(),
        canonical,
        hashlib.sha256,
    ).hexdigest()

    qr_payload = json.dumps(
        {**payload, "signature": signature},
        sort_keys=True,
        separators=(",", ":"),
    )

    qr_image = qrcode.make(qr_payload).convert("RGB")
    output = io.BytesIO()

    document = canvas.Canvas(
        output,
        pagesize=A4,
        invariant=1,
        pageCompression=1,
    )

    document.setTitle("Flash-Ticket")
    document.setFont("Helvetica-Bold", 24)
    document.drawString(48, 780, "FLASH-TICKET")

    document.setFont("Helvetica", 11)
    document.drawString(48, 730, f"Booking: {booking_id}")
    document.drawString(48, 705, f"Event: {event_id}")
    document.drawString(48, 680, f"Quantity: {quantity}")

    document.drawImage(
        ImageReader(qr_image),
        48,
        450,
        width=180,
        height=180,
    )

    document.drawString(
        48,
        420,
        "Keep this ticket private.",
    )

    document.showPage()
    document.save()

    return output.getvalue()