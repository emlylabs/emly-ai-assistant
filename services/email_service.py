import logging
import smtplib
import ssl

from config import EMAIL_SERVER

log = logging.getLogger(__name__)


def email_enabled() -> bool:
    """Email is off unless an SMTP relay is configured via EMAIL_SERVER."""
    return bool(EMAIL_SERVER)


def log_email_status() -> None:
    """One boot-time line so operators can see whether email is on."""
    if email_enabled():
        log.info("Email enabled via SMTP relay %s", EMAIL_SERVER)
    else:
        log.warning(
            "Email disabled: EMAIL_SERVER is not set. OTP codes, invites and "
            "form alerts will not be emailed until an SMTP relay is configured."
        )


class EmailSender:
    def __init__(self, smtp_server, port, username, password):
        self.smtp_server = smtp_server
        self.port = port
        self.username = username
        self.password = password

    def send_email(self, msg) -> bool:
        """Send ``msg``. Returns False (without raising) when no SMTP server
        is configured, so callers degrade instead of crashing."""
        if not self.smtp_server:
            log.warning("Email disabled (EMAIL_SERVER not set); skipping send")
            return False
        try:
            if self.port not in [465, 587]:
                log.error("Use 465 or 587 as port value")
                raise Exception("Use 465 or 587 as port value")

            # Both paths verify the server certificate and hostname.
            context = ssl.create_default_context()
            if self.port == 465:
                with smtplib.SMTP_SSL(self.smtp_server, self.port, context=context) as server:
                    server.login(self.username, self.password)
                    server.send_message(msg)
            else:  # port == 587
                with smtplib.SMTP(self.smtp_server, self.port) as server:
                    server.starttls(context=context)
                    server.login(self.username, self.password)
                    server.send_message(msg)

            log.info("Email successfully sent")
            return True
        except Exception as e:
            log.error(f"Error occurred while sending the email: {e}")
            raise
