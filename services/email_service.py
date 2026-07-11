import smtplib
import ssl
import logging


class EmailSender:
    def __init__(self, smtp_server, port, username, password):
        self.smtp_server = smtp_server
        self.port = port
        self.username = username
        self.password = password

    def send_email(self, msg):
        try:
            if self.port not in [465, 587]:
                logging.error("Use 465 or 587 as port value")
                raise Exception("Use 465 or 587 as port value")

            if self.port == 465:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.smtp_server, self.port, context=context) as server:
                    server.login(self.username, self.password)
                    server.send_message(msg)
            else:  # port == 587
                with smtplib.SMTP(self.smtp_server, self.port) as server:
                    server.starttls()
                    server.login(self.username, self.password)
                    server.send_message(msg)

            logging.info("Email successfully sent")
        except Exception as e:
            logging.error(f"Error occurred while sending the email: {e}")
            raise
