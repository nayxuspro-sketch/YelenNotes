"""Module centralise de gestion des roles, permissions granulaires (RBAC) et perimetre multi-etablissements

Revision ID: 2b8f91c4d5e6
Revises: 1604003a9e33
Create Date: 2026-11-25 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2b8f91c4d5e6"
down_revision = "1604003a9e33"
branch_labels = None
depends_on = None


def _has_column(insp, table_name: str, column_name: str) -> bool:
    return any(col["name"] == column_name for col in insp.get_columns(table_name))


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())

    if "establishments" not in existing_tables:
        op.create_table(
            "establishments",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=50), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("ceb", sa.String(length=150), nullable=True),
            sa.Column("locality", sa.String(length=120), nullable=True),
            sa.Column("address", sa.String(length=255), nullable=True),
            sa.Column("phone", sa.String(length=60), nullable=True),
            sa.Column("email", sa.String(length=120), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code"),
        )
        op.create_index("ix_establishments_code", "establishments", ["code"], unique=True)

    if "rbac_roles" not in existing_tables:
        op.create_table(
            "rbac_roles",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=50), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("is_global_scope", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code"),
            sa.UniqueConstraint("name"),
        )
        op.create_index("ix_rbac_roles_code", "rbac_roles", ["code"], unique=True)

    if "rbac_permissions" not in existing_tables:
        op.create_table(
            "rbac_permissions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=100), nullable=False),
            sa.Column("module", sa.String(length=60), nullable=False),
            sa.Column("module_label", sa.String(length=100), nullable=False),
            sa.Column("resource", sa.String(length=80), nullable=False),
            sa.Column("action", sa.String(length=30), nullable=False),
            sa.Column("description", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.CheckConstraint(
                "action IN ('VIEW', 'CREATE', 'UPDATE', 'DELETE', 'VALIDATE', 'EXPORT', 'PRINT', 'MANAGE')",
                name="ck_rbac_permission_action",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code"),
        )
        op.create_index("ix_rbac_permissions_code", "rbac_permissions", ["code"], unique=True)
        op.create_index("ix_rbac_permissions_module", "rbac_permissions", ["module"], unique=False)

    if "rbac_role_permissions" not in existing_tables:
        op.create_table(
            "rbac_role_permissions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("role_id", sa.Integer(), nullable=False),
            sa.Column("permission_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["permission_id"], ["rbac_permissions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["role_id"], ["rbac_roles.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("role_id", "permission_id", name="uq_rbac_role_permission"),
        )
        op.create_index("ix_rbac_role_permissions_role_id", "rbac_role_permissions", ["role_id"], unique=False)
        op.create_index("ix_rbac_role_permissions_permission_id", "rbac_role_permissions", ["permission_id"], unique=False)

    if "rbac_user_roles" not in existing_tables:
        op.create_table(
            "rbac_user_roles",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("role_id", sa.Integer(), nullable=False),
            sa.Column("assigned_by_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["assigned_by_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["role_id"], ["rbac_roles.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "role_id", name="uq_rbac_user_role"),
        )
        op.create_index("ix_rbac_user_roles_user_id", "rbac_user_roles", ["user_id"], unique=False)
        op.create_index("ix_rbac_user_roles_role_id", "rbac_user_roles", ["role_id"], unique=False)

    if "rbac_user_permissions" not in existing_tables:
        op.create_table(
            "rbac_user_permissions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("permission_id", sa.Integer(), nullable=False),
            sa.Column("effect", sa.String(length=10), nullable=False),
            sa.Column("reason", sa.String(length=255), nullable=False),
            sa.Column("granted_by_id", sa.Integer(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.CheckConstraint("effect IN ('ALLOW', 'DENY')", name="ck_rbac_override_effect"),
            sa.ForeignKeyConstraint(["granted_by_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["permission_id"], ["rbac_permissions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "permission_id", name="uq_rbac_user_permission"),
        )
        op.create_index("ix_rbac_user_permissions_user_id", "rbac_user_permissions", ["user_id"], unique=False)
        op.create_index("ix_rbac_user_permissions_permission_id", "rbac_user_permissions", ["permission_id"], unique=False)

    if "user_establishment_scopes" not in existing_tables:
        op.create_table(
            "user_establishment_scopes",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("establishment_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["establishment_id"], ["establishments.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "establishment_id", name="uq_user_establishment_scope"),
        )
        op.create_index("ix_user_establishment_scopes_user_id", "user_establishment_scopes", ["user_id"], unique=False)
        op.create_index("ix_user_establishment_scopes_establishment_id", "user_establishment_scopes", ["establishment_id"], unique=False)

    if "parent_student_links" not in existing_tables:
        op.create_table(
            "parent_student_links",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("student_id", sa.Integer(), nullable=False),
            sa.Column("relationship_label", sa.String(length=60), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "student_id", name="uq_parent_student_link"),
        )
        op.create_index("ix_parent_student_links_user_id", "parent_student_links", ["user_id"], unique=False)
        op.create_index("ix_parent_student_links_student_id", "parent_student_links", ["student_id"], unique=False)

    if "student_absence_records" not in existing_tables:
        op.create_table(
            "student_absence_records",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("establishment_id", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("enrollment_id", sa.Integer(), nullable=False),
            sa.Column("event_date", sa.Date(), nullable=False),
            sa.Column("event_type", sa.String(length=30), nullable=False),
            sa.Column("justified", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("notes", sa.String(length=255), nullable=True),
            sa.Column("recorded_by_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["enrollment_id"], ["class_enrollments.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["establishment_id"], ["establishments.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["recorded_by_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_student_absence_records_establishment_id", "student_absence_records", ["establishment_id"], unique=False)
        op.create_index("ix_student_absence_records_enrollment_id", "student_absence_records", ["enrollment_id"], unique=False)
        op.create_index("ix_student_absence_records_event_date", "student_absence_records", ["event_date"], unique=False)

    if "accounting_entries" not in existing_tables:
        op.create_table(
            "accounting_entries",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("establishment_id", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("entry_date", sa.Date(), nullable=False),
            sa.Column("entry_type", sa.String(length=30), nullable=False),
            sa.Column("reference", sa.String(length=60), nullable=False),
            sa.Column("label", sa.String(length=200), nullable=False),
            sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column("is_validated", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("recorded_by_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["establishment_id"], ["establishments.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["recorded_by_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_accounting_entries_establishment_id", "accounting_entries", ["establishment_id"], unique=False)
        op.create_index("ix_accounting_entries_entry_date", "accounting_entries", ["entry_date"], unique=False)

    # Colonnes additionnelles SQLite-safe (sans recréation des tables existantes)
    if not _has_column(insp, "users", "primary_role_code"):
        op.add_column("users", sa.Column("primary_role_code", sa.String(length=50), nullable=True))
        op.create_index("ix_users_primary_role_code", "users", ["primary_role_code"], unique=False)
    if not _has_column(insp, "users", "establishment_id"):
        op.add_column("users", sa.Column("establishment_id", sa.Integer(), nullable=True, server_default="1"))
        op.create_index("ix_users_establishment_id", "users", ["establishment_id"], unique=False)
    if not _has_column(insp, "users", "suspended_until"):
        op.add_column("users", sa.Column("suspended_until", sa.DateTime(), nullable=True))
    if not _has_column(insp, "users", "suspension_reason"):
        op.add_column("users", sa.Column("suspension_reason", sa.String(length=255), nullable=True))

    if not _has_column(insp, "school_years", "establishment_id"):
        op.add_column("school_years", sa.Column("establishment_id", sa.Integer(), nullable=True, server_default="1"))
        op.create_index("ix_school_years_establishment_id", "school_years", ["establishment_id"], unique=False)

    if not _has_column(insp, "academic_classes", "establishment_id"):
        op.add_column("academic_classes", sa.Column("establishment_id", sa.Integer(), nullable=True, server_default="1"))
        op.create_index("ix_academic_classes_establishment_id", "academic_classes", ["establishment_id"], unique=False)

    if not _has_column(insp, "students", "establishment_id"):
        op.add_column("students", sa.Column("establishment_id", sa.Integer(), nullable=True, server_default="1"))
        op.create_index("ix_students_establishment_id", "students", ["establishment_id"], unique=False)

    if not _has_column(insp, "audit_logs", "establishment_id"):
        op.add_column("audit_logs", sa.Column("establishment_id", sa.Integer(), nullable=True, server_default="1"))
        op.create_index("ix_audit_logs_establishment_id", "audit_logs", ["establishment_id"], unique=False)
    if not _has_column(insp, "audit_logs", "module"):
        op.add_column("audit_logs", sa.Column("module", sa.String(length=60), nullable=True, server_default="system"))
        op.create_index("ix_audit_logs_module", "audit_logs", ["module"], unique=False)
    if not _has_column(insp, "audit_logs", "status"):
        op.add_column("audit_logs", sa.Column("status", sa.String(length=20), nullable=True, server_default="SUCCESS"))
        op.create_index("ix_audit_logs_status", "audit_logs", ["status"], unique=False)


def downgrade():
    op.drop_table("accounting_entries")
    op.drop_table("student_absence_records")
    op.drop_table("parent_student_links")
    op.drop_table("user_establishment_scopes")
    op.drop_table("rbac_user_permissions")
    op.drop_table("rbac_user_roles")
    op.drop_table("rbac_role_permissions")
    op.drop_table("rbac_permissions")
    op.drop_table("rbac_roles")
    op.drop_table("establishments")
