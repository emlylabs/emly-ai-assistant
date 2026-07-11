import logging
from datetime import datetime, timezone
from typing import List, Optional

from peewee import BooleanField, CharField, DateTimeField, DoesNotExist, Model
from playhouse.shortcuts import model_to_dict
from pydantic import BaseModel, EmailStr

from db.db import DB

log = logging.getLogger(__name__)


class AdminUser(Model):
    id = CharField(max_length=255, primary_key=True)
    email = CharField(max_length=255, unique=True)
    # ``password_hash`` is a legacy column carried for one more migration
    # cycle to keep the SQLite schema stable across the rewrite (SQLite's
    # ALTER TABLE DROP COLUMN is finicky enough that we leave it for a
    # dedicated cleanup pass). All inserts default to "" — nothing reads it.
    password_hash = CharField(max_length=255, default="")
    is_active = BooleanField(default=True)
    # Phase 3 columns (auth-rewrite). ``issuer`` + ``subject`` are the OIDC link
    # populated on first login; ``email_verified`` mirrors the IdP claim;
    # ``is_superadmin`` is the global bypass for ``services/authz``.
    issuer = CharField(max_length=512, null=True)
    subject = CharField(max_length=255, null=True)
    email_verified = BooleanField(default=False)
    is_superadmin = BooleanField(default=False)
    last_login_at = DateTimeField(null=True)
    name = CharField(max_length=255, null=True)
    created_on = DateTimeField(default=datetime.utcnow)
    updated_on = DateTimeField(default=datetime.utcnow)

    class Meta:
        database = DB
        table_name = "admin_user"
        indexes = ((("issuer", "subject"), True),)


class AdminUserModel(BaseModel):
    id: str
    email: EmailStr
    is_active: bool
    issuer: Optional[str] = None
    subject: Optional[str] = None
    email_verified: bool = False
    is_superadmin: bool = False
    last_login_at: Optional[datetime] = None
    name: Optional[str] = None
    created_on: datetime
    updated_on: datetime


class AdminUsersTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([AdminUser])

    def insert(
        self,
        id: str,
        email: str,
        password_hash: str = "",
        is_active: bool = True,
        *,
        issuer: Optional[str] = None,
        subject: Optional[str] = None,
        email_verified: bool = False,
        is_superadmin: bool = False,
        name: Optional[str] = None,
    ) -> Optional[AdminUserModel]:
        now = datetime.utcnow()
        AdminUser.create(
            id=id,
            email=email.lower(),
            password_hash=password_hash,
            is_active=is_active,
            issuer=issuer,
            subject=subject,
            email_verified=email_verified,
            is_superadmin=is_superadmin,
            name=name,
            created_on=now,
            updated_on=now,
        )
        return self.get_by_id(id)

    def get_by_id(self, id: str) -> Optional[AdminUserModel]:
        try:
            user = AdminUser.get(AdminUser.id == id)
            return AdminUserModel(**model_to_dict(user))
        except DoesNotExist:
            return None

    def get_by_email(self, email: str) -> Optional[AdminUser]:
        try:
            return AdminUser.get(AdminUser.email == email.lower())
        except DoesNotExist:
            return None

    def get_by_issuer_subject(self, issuer: str, subject: str) -> Optional[AdminUser]:
        try:
            return AdminUser.get(
                (AdminUser.issuer == issuer) & (AdminUser.subject == subject)
            )
        except DoesNotExist:
            return None

    def link_to_idp(
        self,
        id: str,
        *,
        issuer: str,
        subject: str,
        email_verified: bool,
        name: Optional[str] = None,
    ) -> bool:
        """Set the OIDC link on an admin row that was provisioned without it (e.g. via pending_admins activation)."""
        rows = (
            AdminUser.update(
                issuer=issuer,
                subject=subject,
                email_verified=email_verified,
                name=name,
                updated_on=datetime.utcnow(),
            )
            .where(AdminUser.id == id)
            .execute()
        )
        return rows > 0

    def touch_login(self, id: str, *, email: Optional[str] = None, name: Optional[str] = None) -> bool:
        """Record a successful login. Refreshes ``last_login_at`` and optionally syncs email/name from the IdP."""
        updates = {"last_login_at": datetime.now(timezone.utc), "updated_on": datetime.utcnow()}
        if email is not None:
            updates["email"] = email.lower()
        if name is not None:
            updates["name"] = name
        rows = AdminUser.update(**updates).where(AdminUser.id == id).execute()
        return rows > 0

    def set_superadmin(self, id: str, value: bool) -> bool:
        rows = AdminUser.update(is_superadmin=value, updated_on=datetime.utcnow()).where(AdminUser.id == id).execute()
        return rows > 0

    def list(self) -> List[AdminUserModel]:
        return [AdminUserModel(**model_to_dict(u)) for u in AdminUser.select().order_by(AdminUser.created_on)]

    def count(self) -> int:
        return AdminUser.select().count()

    def count_active(self) -> int:
        return AdminUser.select().where(AdminUser.is_active == True).count()  # noqa: E712

    def count_superadmins_active(self) -> int:
        return AdminUser.select().where(
            (AdminUser.is_active == True) & (AdminUser.is_superadmin == True)  # noqa: E712
        ).count()

    def update_password(self, id: str, password_hash: str) -> bool:
        rows = AdminUser.update(password_hash=password_hash, updated_on=datetime.utcnow()).where(AdminUser.id == id).execute()
        return rows > 0

    def set_active(self, id: str, is_active: bool) -> bool:
        rows = AdminUser.update(is_active=is_active, updated_on=datetime.utcnow()).where(AdminUser.id == id).execute()
        return rows > 0


AdminUsers = AdminUsersTable(DB)
