"""Module de gestion des licences d'utilisation (Monoposte / Multiposte Reseau, empreinte materielle)

Revision ID: 3c4d5e6f7a8b
Revises: 2b8f91c4d5e6
Create Date: 2026-11-26 20:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "3c4d5e6f7a8b"
down_revision = "2b8f91c4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())

    if "issued_licenses" not in existing_tables:
        op.create_table(
            "issued_licenses",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("license_serial", sa.String(length=48), nullable=False),
            sa.Column("license_type", sa.String(length=20), nullable=False),
            sa.Column("beneficiary_name", sa.String(length=200), nullable=False),
            sa.Column("machine_fingerprint", sa.String(length=80), nullable=False),
            sa.Column("issued_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.Date(), nullable=True),
            sa.Column("notes", sa.String(length=255), nullable=True),
            sa.Column("license_token", sa.Text(), nullable=False),
            sa.Column("is_revoked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("issued_by_id", sa.Integer(), nullable=True),
            sa.CheckConstraint(
                "license_type IN ('MONOPOSTE', 'MULTIPOSTE')",
                name="ck_issued_licenses_type",
            ),
            sa.ForeignKeyConstraint(["issued_by_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("license_serial"),
        )
        op.create_index("ix_issued_licenses_license_serial", "issued_licenses", ["license_serial"], unique=True)
        op.create_index("ix_issued_licenses_license_type", "issued_licenses", ["license_type"], unique=False)
        op.create_index(
            "ix_issued_licenses_machine_fingerprint", "issued_licenses", ["machine_fingerprint"], unique=False
        )
        op.create_index("ix_issued_licenses_issued_at", "issued_licenses", ["issued_at"], unique=False)


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "issued_licenses" in set(insp.get_table_names()):
        op.drop_table("issued_licenses")
