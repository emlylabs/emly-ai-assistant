"""Peewee migration -- 001_baseline.py

Single consolidated baseline. Replaces the original 001-011 chain.

Greenfield project — no production data. Any dev DB that ran the old
chain must be wiped (SQLite: ``rm $DATA_DIR/db.sqlite``; Postgres:
``DROP DATABASE … ; CREATE DATABASE …``) so peewee_migrate's
``migratehistory`` table starts fresh.
"""
import peewee as pw
from peewee_migrate import Migrator


CONFIG_SCHEMA_VERSION = 1


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    # ------------------------------------------------------------------
    # Admin auth
    # ------------------------------------------------------------------
    @migrator.create_model
    class AdminUser(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        email = pw.CharField(max_length=255, unique=True)
        password_hash = pw.CharField(max_length=255)
        is_active = pw.BooleanField(default=True)
        created_at = pw.DateTimeField()
        updated_at = pw.DateTimeField()
        issuer = pw.CharField(max_length=512, null=True)
        subject = pw.CharField(max_length=255, null=True)
        email_verified = pw.BooleanField(default=False)
        is_superadmin = pw.BooleanField(default=False)
        last_login_at = pw.DateTimeField(null=True)
        name = pw.CharField(max_length=255, null=True)

        class Meta:
            table_name = "admin_user"
            indexes = ((("issuer", "subject"), True),)

    @migrator.create_model
    class PendingAdmin(pw.Model):
        id = pw.CharField(max_length=64, primary_key=True)
        email = pw.CharField(max_length=255, unique=True)
        invited_by = pw.CharField(max_length=64, null=True)
        is_superadmin = pw.BooleanField(default=False)
        bot_assignments = pw.TextField(null=True)
        created_at = pw.DateTimeField()
        expires_at = pw.DateTimeField(null=True)
        consumed_at = pw.DateTimeField(null=True)
        consumed_by = pw.CharField(max_length=64, null=True)

        class Meta:
            table_name = "pending_admin"

    # ------------------------------------------------------------------
    # Bot tree
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
        config_version = pw.IntegerField(default=0)
        api_key_encrypted = pw.TextField(null=True)
        embed_model_id = pw.CharField(max_length=255, null=True)
        current_owner_count = pw.IntegerField(default=0)
        widget_key_version = pw.IntegerField(default=1)
        widget_key_rotated_at = pw.DateTimeField(null=True)
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
        role = pw.CharField(max_length=32)
        created_at = pw.DateTimeField()
        updated_at = pw.DateTimeField()

        class Meta:
            table_name = "admin_bot_membership"
            indexes = ((("admin", "bot"), True),)

    @migrator.create_model
    class BotChannel(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="channels")
        type = pw.CharField(max_length=64)
        external_id = pw.CharField(max_length=255, null=True)
        credentials_encrypted = pw.TextField(null=True)
        config_json = pw.TextField(null=True)
        is_active = pw.BooleanField(default=True)
        secrets_rotated_at = pw.DateTimeField(null=True)
        display_name = pw.CharField(max_length=255, null=True)
        config_version = pw.IntegerField(default=0)
        created_at = pw.DateTimeField()

        class Meta:
            table_name = "bot_channel"
            indexes = ((("type", "external_id"), True),)

    # ------------------------------------------------------------------
    # End-user data (composite-PK emly_user; child tables hold plain
    # ``user_id`` columns — FKs are enforced via composite ADD CONSTRAINT
    # on Postgres after model creation. SQLite doesn't enforce FKs in
    # this codebase so the column is enough.)
    # ------------------------------------------------------------------
    @migrator.create_model
    class EMLYUser(pw.Model):
        id = pw.CharField(max_length=255)
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
        created_at = pw.DateTimeField(null=True)
        updated_at = pw.DateTimeField(null=True)
        meta = pw.TextField(null=True)

        class Meta:
            table_name = "emly_user"
            primary_key = pw.CompositeKey("bot", "id")

    @migrator.create_model
    class EMLYMessage(pw.Model):
        id = pw.AutoField()
        bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="messages")
        user_id = pw.CharField(max_length=255)
        session_id = pw.CharField(max_length=255)
        message = pw.TextField()
        role = pw.CharField(max_length=255)
        created_at = pw.DateTimeField()
        updated_at = pw.DateTimeField()
        not_useful = pw.BooleanField(default=False)
        expanded_query = pw.TextField(null=True)
        page = pw.TextField(null=True)
        topic = pw.CharField(max_length=255, null=True)
        # Telemetry / channel / deflection / CSAT (migration 007).
        channel_id = pw.CharField(max_length=64, null=True)
        model_used = pw.CharField(max_length=128, null=True)
        prompt_tokens = pw.IntegerField(null=True)
        completion_tokens = pw.IntegerField(null=True)
        response_time_ms = pw.IntegerField(null=True)
        is_deflected = pw.BooleanField(null=True)
        deflection_method = pw.CharField(max_length=32, null=True)
        rating = pw.IntegerField(null=True)
        rated_at = pw.DateTimeField(null=True)
        citations = pw.TextField(null=True)

        class Meta:
            table_name = "emly_messages"
            indexes = (
                (("bot", "user_id", "session_id"), False),
                (("bot", "created_at"), False),
                (("bot", "channel_id"), False),
                (("bot", "rating"), False),
            )

    @migrator.create_model
    class EMLYUserAction(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="user_actions")
        user_id = pw.CharField(max_length=255)
        session_id = pw.CharField(max_length=255, null=True)
        message = pw.ForeignKeyField(EMLYMessage, field="id", on_delete="CASCADE", null=True, backref="actions")
        action_name = pw.CharField(max_length=255, null=True)
        action_value = pw.CharField(max_length=255, null=True)
        action_payload = pw.TextField(null=True)
        created_at = pw.DateTimeField()
        updated_at = pw.DateTimeField()

        class Meta:
            table_name = "emly_user_actions"
            indexes = ((("bot", "user_id"), False),)

    @migrator.create_model
    class EMLYFile(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="files")
        user_id = pw.CharField(max_length=255, null=True)
        file_name = pw.CharField(max_length=255, null=True)
        file_type = pw.CharField(max_length=255, null=True)
        file_size = pw.IntegerField(null=True)
        size_bytes = pw.BigIntegerField(null=True)
        mime_type = pw.CharField(max_length=255, null=True)
        sha256 = pw.CharField(max_length=64, null=True)
        embedding_status = pw.CharField(max_length=32, default="pending")
        error_message = pw.TextField(null=True)
        document_type = pw.CharField(max_length=64, default="document")
        created_at = pw.DateTimeField()
        updated_at = pw.DateTimeField()

        class Meta:
            table_name = "emly_files"
            indexes = ((("bot", "embedding_status"), False),)

    @migrator.create_model
    class EMLYDocs(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="docs")
        name = pw.CharField(max_length=255)
        content_hash = pw.TextField(null=True)
        created_at = pw.DateTimeField(null=True)
        updated_at = pw.DateTimeField(null=True)

        class Meta:
            table_name = "emly_docs"
            indexes = ((("bot", "name"), True),)

    @migrator.create_model
    class BotImpressions(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="impressions")
        impression_type = pw.CharField(max_length=255)
        created_at = pw.DateTimeField()
        updated_at = pw.DateTimeField()

        class Meta:
            table_name = "bot_impressions"
            indexes = ((("bot", "created_at"), False),)

    @migrator.create_model
    class OtpAuth(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="otp_auths")
        user_id = pw.CharField(max_length=255)
        otp_type = pw.CharField(max_length=255)
        otp = pw.CharField(max_length=255)
        expires_in = pw.BigIntegerField()
        authorized = pw.BooleanField()
        created_at = pw.DateTimeField()
        updated_at = pw.DateTimeField()

        class Meta:
            table_name = "otp_auth"
            indexes = ((("bot", "user_id"), True),)

    @migrator.create_model
    class EMLYUserChannelIdentity(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot = pw.ForeignKeyField(Bot, field="id", on_delete="CASCADE", backref="channel_identities")
        channel = pw.ForeignKeyField(BotChannel, field="id", on_delete="CASCADE", backref="identities")
        external_id = pw.CharField(max_length=255)
        emly_user_id = pw.CharField(max_length=255)
        verified = pw.BooleanField(default=False)
        created_at = pw.DateTimeField()

        class Meta:
            table_name = "emly_user_channel_identity"
            indexes = ((("channel", "external_id"), True),)

    @migrator.create_model
    class WebhookEventDedupe(pw.Model):
        id = pw.AutoField()
        channel_type = pw.CharField(max_length=64)
        event_id = pw.CharField(max_length=255)
        received_at = pw.DateTimeField()
        status = pw.CharField(max_length=32, default="done")
        processing_expires_at = pw.DateTimeField(null=True)
        attempts = pw.IntegerField(default=1)

        class Meta:
            table_name = "webhook_event_dedupe"
            indexes = (
                (("channel_type", "event_id"), True),
                (("received_at",), False),
            )

    @migrator.create_model
    class AdminAuditLog(pw.Model):
        id = pw.CharField(max_length=64, primary_key=True)
        admin_id = pw.CharField(max_length=64, null=True)
        bot_id = pw.CharField(max_length=64, null=True)
        action = pw.CharField(max_length=128)
        target_type = pw.CharField(max_length=64, null=True)
        target_id = pw.CharField(max_length=255, null=True)
        payload = pw.TextField(null=True)
        ip = pw.CharField(max_length=64, null=True)
        ua = pw.CharField(max_length=512, null=True)
        success = pw.BooleanField(default=True)
        created_at = pw.DateTimeField()

        class Meta:
            table_name = "admin_audit_log"
            indexes = (
                (("admin_id", "created_at"), False),
                (("bot_id", "created_at"), False),
                (("action", "created_at"), False),
            )

    # ------------------------------------------------------------------
    # Embedded OIDC issuer credentials (one row per local admin).
    # admin_id is a plain CharField so this table doesn't pin admin_user's
    # column shape; integrity is enforced in app code.
    # ------------------------------------------------------------------
    @migrator.create_model
    class LocalCredential(pw.Model):
        admin_id = pw.CharField(max_length=64, primary_key=True)
        password_hash = pw.CharField(max_length=512)
        password_set_at = pw.DateTimeField()
        must_change = pw.BooleanField(default=False)
        failed_attempts = pw.IntegerField(default=0)
        locked_until = pw.DateTimeField(null=True)

        class Meta:
            table_name = "local_credential"

    # ------------------------------------------------------------------
    # Crawler state
    # ------------------------------------------------------------------
    @migrator.create_model
    class CrawlJob(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot_id = pw.CharField(max_length=255)
        seed_url = pw.TextField()
        options_json = pw.TextField()
        status = pw.CharField(max_length=32, default="running")
        pages_total = pw.IntegerField(default=0)
        pages_done = pw.IntegerField(default=0)
        pages_skipped = pw.IntegerField(default=0)
        pages_failed = pw.IntegerField(default=0)
        document_type = pw.CharField(max_length=64, default="web_page")
        created_by_admin_id = pw.CharField(max_length=255, null=True)
        created_at = pw.DateTimeField()
        updated_at = pw.DateTimeField()
        completed_at = pw.DateTimeField(null=True)
        error_message = pw.TextField(null=True)

        class Meta:
            table_name = "crawl_jobs"
            indexes = (
                (("bot_id", "status"), False),
                (("bot_id", "created_at"), False),
            )

    @migrator.create_model
    class CrawlJobPage(pw.Model):
        id = pw.AutoField()
        job_id = pw.CharField(max_length=255)
        url = pw.TextField()
        depth = pw.IntegerField(default=0)
        state = pw.CharField(max_length=32, default="queued")
        reason = pw.TextField(null=True)
        file_id = pw.CharField(max_length=255, null=True)
        created_at = pw.DateTimeField()
        updated_at = pw.DateTimeField()

        class Meta:
            table_name = "crawl_job_pages"
            indexes = (
                (("job_id", "state"), False),
                (("job_id", "url"), True),
            )

    # ------------------------------------------------------------------
    # Per-session aggregate (with takeover columns from 010)
    # ------------------------------------------------------------------
    @migrator.create_model
    class EMLYSession(pw.Model):
        id = pw.CharField(max_length=255, primary_key=True)
        bot_id = pw.CharField(max_length=64)
        user_id = pw.CharField(max_length=255)
        channel_id = pw.CharField(max_length=64, null=True)
        started_at = pw.DateTimeField()
        last_activity_at = pw.DateTimeField()
        ended_at = pw.DateTimeField(null=True)
        turn_count = pw.IntegerField(default=0)
        is_resolved = pw.BooleanField(null=True)
        resolved_at = pw.DateTimeField(null=True)
        resolved_by = pw.CharField(max_length=64, null=True)
        sentiment_score = pw.FloatField(null=True)
        sentiment_label = pw.CharField(max_length=32, null=True)
        intent = pw.CharField(max_length=128, null=True)
        intent_confidence = pw.FloatField(null=True)
        enrichment_at = pw.DateTimeField(null=True)
        taken_over_by = pw.CharField(max_length=64, null=True)
        taken_over_at = pw.DateTimeField(null=True)
        taken_over_until = pw.DateTimeField(null=True)

        class Meta:
            table_name = "emly_session"
            indexes = (
                (("bot_id", "started_at"), False),
                (("bot_id", "last_activity_at"), False),
                (("bot_id", "channel_id"), False),
                (("bot_id", "is_resolved"), False),
            )

    # Multi-tenant integrity (every child row's bot_id/user_id pair must
    # reference a real emly_user row) is enforced in application code via
    # the bot_id security boundary in agents/rag_manager.py and the
    # per-bot routing in routes/admin_bot_*.py — no DB-level composite FK.


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    for table in (
        "emly_session",
        "crawl_job_pages",
        "crawl_jobs",
        "local_credential",
        "admin_audit_log",
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
        "pending_admin",
        "bots",
        "admin_user",
    ):
        migrator.remove_model(table)
