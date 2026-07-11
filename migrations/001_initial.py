"""Peewee migration -- 001_initial.py

Single initial schema for the multi-bot runtime.

Greenfield project — no historical migrations to replay. Every user-data
table carries a NOT NULL ``bot_id`` FK to ``bots(id)`` from the start.
The 18 historical migrations that this replaces are gone for good.
"""
from contextlib import suppress

import peewee as pw
from peewee_migrate import Migrator

with suppress(ImportError):
    import playhouse.postgres_ext as pw_pext  # noqa: F401


CONFIG_SCHEMA_VERSION = 1


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    """Create the entire post-multi-bot schema."""

    # ------------------------------------------------------------------
    # Admin auth (no FKs to other tables)
    # ------------------------------------------------------------------
    @migrator.create_model
    class AdminUser(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        email = pw.CharField(max_length=255, unique=True)
        password_hash = pw.CharField(max_length=255)
        is_active = pw.BooleanField(default=True)
        created_on = pw.DateTimeField()
        updated_on = pw.DateTimeField()

        class Meta:
            table_name = "admin_user"

    @migrator.create_model
    class AdminInvite(pw.Model):
        token = pw.CharField(max_length=255, primary_key=True)
        email = pw.CharField(max_length=255)
        invited_by = pw.CharField(max_length=255)
        created_on = pw.DateTimeField()
        expires_on = pw.DateTimeField()
        used_on = pw.DateTimeField(null=True)

        class Meta:
            table_name = "admin_invite"

    # ------------------------------------------------------------------
    # Bot model (root of the multi-tenant tree)
    # ------------------------------------------------------------------
    @migrator.create_model
    class Bot(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        slug = pw.CharField(max_length=255, unique=True)
        name = pw.CharField(max_length=255)
        is_active = pw.BooleanField(default=True)
        is_deleted = pw.BooleanField(default=False)
        config_json = pw.TextField(null=True)
        config_schema_version = pw.IntegerField(default=CONFIG_SCHEMA_VERSION)
        # Bumped on every config write — workers compare the cached
        # version against the current row to detect cross-worker
        # invalidations (Phase 3.5).
        config_version = pw.IntegerField(default=0)
        api_key_encrypted = pw.TextField(null=True)
        embed_model_id = pw.CharField(max_length=255, null=True)
        current_owner_count = pw.IntegerField(default=0)
        created_at = pw.DateTimeField()
        updated_at = pw.DateTimeField()
        deleted_at = pw.DateTimeField(null=True)

        class Meta:
            table_name = "bots"

    @migrator.create_model
    class AdminBotMembership(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        admin = pw.ForeignKeyField(AdminUser, field="id", on_delete="CASCADE", backref="memberships")
        bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="memberships")
        role = pw.CharField(max_length=32)  # owner / admin / viewer
        created_at = pw.DateTimeField()
        updated_at = pw.DateTimeField()

        class Meta:
            table_name = "admin_bot_membership"
            indexes = (
                (("admin", "bot"), True),  # unique (admin_id, bot_id)
            )

    @migrator.create_model
    class BotChannel(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="channels")
        type = pw.CharField(max_length=64)  # web_widget / google_chat / slack / teams / ...
        external_id = pw.CharField(max_length=255, null=True)
        credentials_encrypted = pw.TextField(null=True)
        config_json = pw.TextField(null=True)
        is_active = pw.BooleanField(default=True)
        created_at = pw.DateTimeField()

        class Meta:
            table_name = "bot_channel"
            indexes = (
                (("type", "external_id"), True),  # unique (type, external_id)
                (("bot",), False),
            )

    # ------------------------------------------------------------------
    # End-user data — every row carries bot_id NOT NULL.
    # ------------------------------------------------------------------
    @migrator.create_model
    class EMLYUser(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="emly_users")
        first_name = pw.CharField(max_length=255, null=True)
        last_name = pw.CharField(max_length=255, null=True)
        email = pw.CharField(max_length=255, null=True)
        phone = pw.CharField(max_length=255, null=True)
        ip = pw.CharField(max_length=255)
        browser = pw.CharField(max_length=255)
        timestamp = pw.BigIntegerField()
        country = pw.CharField(max_length=255, null=True)
        city = pw.CharField(max_length=255, null=True)
        region = pw.TextField(null=True)
        latitude = pw.FloatField(null=True)
        longitude = pw.FloatField(null=True)
        created_on = pw.DateTimeField(null=True)
        updated_on = pw.DateTimeField(null=True)
        meta = pw.TextField(null=True)

        class Meta:
            table_name = "emly_user"
            indexes = ((("bot",), False),)

    @migrator.create_model
    class EMLYMessage(pw.Model):
        id = pw.AutoField()
        bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="messages")
        user_id = pw.CharField(max_length=255)
        session_id = pw.CharField(max_length=255)
        message = pw.TextField()
        role = pw.CharField(max_length=255)
        created_on = pw.DateTimeField()
        updated_on = pw.DateTimeField()
        not_useful = pw.BooleanField(default=False)
        expanded_query = pw.TextField(null=True)
        page = pw.TextField(null=True)
        topic = pw.CharField(max_length=255, null=True)

        class Meta:
            table_name = "emly_messages"
            indexes = (
                (("bot", "user_id", "session_id"), False),
                (("bot", "created_on"), False),
            )

    @migrator.create_model
    class EMLYUserAction(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="user_actions")
        user = pw.ForeignKeyField(EMLYUser, field="id", on_delete="CASCADE", backref="actions")
        session_id = pw.CharField(max_length=255, null=True)
        message = pw.ForeignKeyField(EMLYMessage, field="id", on_delete="CASCADE", null=True, backref="actions")
        action_name = pw.CharField(max_length=255, null=True)
        action_value = pw.CharField(max_length=255, null=True)
        action_payload = pw.TextField(null=True)
        created_on = pw.DateTimeField()
        updated_on = pw.DateTimeField()

        class Meta:
            table_name = "emly_user_actions"
            indexes = ((("bot",), False),)

    @migrator.create_model
    class EMLYFile(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="files")
        user = pw.ForeignKeyField(EMLYUser, field="id", on_delete="CASCADE", null=True, backref="files")
        file_name = pw.CharField(max_length=255, null=True)
        file_type = pw.CharField(max_length=255, null=True)
        file_size = pw.IntegerField(null=True)
        size_bytes = pw.BigIntegerField(null=True)
        mime_type = pw.CharField(max_length=255, null=True)
        sha256 = pw.CharField(max_length=64, null=True)
        embedding_status = pw.CharField(max_length=32, default="pending")
        error_message = pw.TextField(null=True)
        created_on = pw.DateTimeField()
        updated_on = pw.DateTimeField()

        class Meta:
            table_name = "emly_files"
            indexes = (
                (("bot",), False),
                (("bot", "embedding_status"), False),
            )

    @migrator.create_model
    class EMLYDocs(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="docs")
        name = pw.CharField(max_length=255)
        content_hash = pw.TextField(null=True)
        created_on = pw.DateTimeField(null=True)
        updated_on = pw.DateTimeField(null=True)

        class Meta:
            table_name = "emly_docs"
            indexes = (
                (("bot", "name"), True),  # unique within a bot
            )

    @migrator.create_model
    class BotImpressions(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="impressions")
        impression_type = pw.CharField(max_length=255)
        created_on = pw.DateTimeField()
        updated_on = pw.DateTimeField()

        class Meta:
            table_name = "bot_impressions"
            indexes = (
                (("bot", "created_on"), False),
            )

    @migrator.create_model
    class OtpAuth(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="otp_auths")
        user_id = pw.CharField(max_length=255)
        otp_type = pw.CharField(max_length=255)
        otp = pw.CharField(max_length=255)
        expires_in = pw.BigIntegerField()
        authorized = pw.BooleanField()
        created_on = pw.DateTimeField()
        updated_on = pw.DateTimeField()

        class Meta:
            table_name = "otp_auth"
            indexes = (
                (("bot", "user_id"), True),  # one OTP per (bot, user)
            )

    # ------------------------------------------------------------------
    # Cross-channel identity & idempotency.
    # ------------------------------------------------------------------
    @migrator.create_model
    class EMLYUserChannelIdentity(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="channel_identities")
        channel = pw.ForeignKeyField(BotChannel, field="id", on_delete="CASCADE", backref="identities")
        external_id = pw.CharField(max_length=255)
        emly_user = pw.ForeignKeyField(EMLYUser, field="id", on_delete="CASCADE", backref="channel_identities")
        verified = pw.BooleanField(default=False)
        created_at = pw.DateTimeField()

        class Meta:
            table_name = "emly_user_channel_identity"
            indexes = (
                (("channel", "external_id"), True),  # unique within a channel
            )

    @migrator.create_model
    class WebhookEventDedupe(pw.Model):
        id = pw.AutoField()
        channel_type = pw.CharField(max_length=64)
        event_id = pw.CharField(max_length=255)
        received_at = pw.DateTimeField()

        class Meta:
            table_name = "webhook_event_dedupe"
            indexes = (
                (("channel_type", "event_id"), True),
                (("received_at",), False),  # for TTL cleanup
            )


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    """Drop everything. Greenfield rollback = nuclear."""
    for table in (
        "webhook_event_dedupe",
        "emly_user_channel_identity",
        "otp_auth",
        "bot_impressions",
        "emly_docs",
        "emly_files",
        "emly_user_actions",
        "emly_messages",
        "emly_user",
        "bot_channel",
        "admin_bot_membership",
        "bots",
        "admin_invite",
        "admin_user",
    ):
        migrator.remove_model(table)
