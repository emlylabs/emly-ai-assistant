"""OTP issuance and verification for widget auth flows."""
from __future__ import annotations

import logging
import random
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import (
    EMAIL_FROM,
    EMAIL_OTP_TEMPLATE,
    EMAIL_PASSWORD,
    EMAIL_PORT,
    EMAIL_SERVER,
    EMAIL_USER,
)
from models.otp_auth import OtpAuthorization
from services.bot_config import get_config_for_bot
from services.email_service import EmailSender
from services.scheduler import scheduler

log = logging.getLogger(__name__)


def generate_otp() -> str:
    return str(random.randint(100000, 999999))


def _otp_expires_in(bot_id: str) -> int:
    try:
        cfg = get_config_for_bot(bot_id).model_dump(mode="json")
    except LookupError:
        return 5
    return int(cfg.get("otp", {}).get("expires_in", 5))


def create_otp(payload_data: dict, bot_id: str) -> dict:
    """Issue (or refresh) an OTP for a user under ``bot_id``."""
    expires_in = _otp_expires_in(bot_id)
    otp = OtpAuthorization.get_otp(bot_id=bot_id, user_id=payload_data["user_id"])
    if otp:
        otp = OtpAuthorization.update_otp(
            bot_id=bot_id,
            user_id=payload_data["user_id"],
            otp=generate_otp(),
            expires_in=expires_in,
        )
    else:
        otp = OtpAuthorization.insert_otp(
            bot_id=bot_id,
            user_id=payload_data["user_id"],
            otp_type=payload_data["otp_type"],
            otp=generate_otp(),
            expires_in=expires_in,
            authorized=False,
        )
    payload_data["otp"] = otp["otp"]
    scheduler.add_job(send_otp_in_email, args=[payload_data])
    return otp


def authorize_otp(payload_data: dict, bot_id: str) -> dict:
    otp = OtpAuthorization.validate_otp(
        bot_id=bot_id, user_id=payload_data["user_id"], otp=payload_data["otp"]
    )
    if otp:
        return {"code": 200, "message": "OTP authorized successfully"}
    return {"code": 401, "message": "Invalid OTP"}


def send_otp_in_email(payload_data: dict) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Emly Labs Verification Code"
    msg["From"] = EMAIL_FROM
    msg["To"] = payload_data["user_id"]

    content = EMAIL_OTP_TEMPLATE
    content = content.replace("{otp_code}", payload_data["otp"])
    content = content.replace("{email}", payload_data["user_id"])
    first_name, _, _ = payload_data["user_id"].partition("@")
    content = content.replace("{first_name}", first_name)

    msg.attach(MIMEText(content, "html"))
    EmailSender(
        smtp_server=EMAIL_SERVER,
        port=EMAIL_PORT,
        username=EMAIL_USER,
        password=EMAIL_PASSWORD,
    ).send_email(msg)
