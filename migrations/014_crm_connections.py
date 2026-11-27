"""Peewee migration -- 014_crm_connections.py

One Zoho or HubSpot connection per bot. Secrets are stored encrypted by
the application; this migration only creates the columns.
"""
import peewee as pw
from peewee_migrate import Migrator


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    @migrator.create_model
    class CrmConnection(pw.Model):
        id = pw.CharField(max_length=64, primary_key=True)
        bot_id = pw.CharField(max_length=255)
        provider = pw.CharField(max_length=32)
        status = pw.CharField(max_length=32, default="pending")
        client_id = pw.CharField(max_length=512)
        client_secret_encrypted = pw.TextField(null=True)
        data_center = pw.CharField(max_length=16, null=True)
        accounts_server = pw.CharField(max_length=255, null=True)
        api_domain = pw.CharField(max_length=255, null=True)
        hub_id = pw.CharField(max_length=64, null=True)
        scopes = pw.TextField(null=True)
        access_token_encrypted = pw.TextField(null=True)
        refresh_token_encrypted = pw.TextField(null=True)
        token_issued_at = pw.DateTimeField(null=True)
        expires_in = pw.IntegerField(null=True)
        expires_at = pw.DateTimeField(null=True)
        last_error = pw.TextField(null=True)
        created_at = pw.DateTimeField()
        updated_at = pw.DateTimeField()

        class Meta:
            table_name = "crm_connection"
            indexes = ((("bot_id", "provider"), True),)


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    migrator.remove_model("crm_connection")
