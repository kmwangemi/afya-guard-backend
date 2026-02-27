"""
SHA Fraud Detection — Database Seeder

Idempotent seed script: safe to run multiple times.
  - Permissions are created if they don't exist, updated if they do.
  - Roles are created if they don't exist.
  - Role→permission links are reconciled (adds new, leaves existing).
  - Default superuser is created only if no superuser exists.
  - Superuser always gets the 'admin' role assigned (backfilled if missing).
  - Existing data is never deleted.

Run:
    python -m app.db.seeds.run            (from project root)
    uv run python -m app.db.seeds.run     (if using uv)
"""

import os
import sys

# Allow running directly from project root
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
)

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.models import Permission, Role, User
from app.seeds.seed_data import (
    DEFAULT_SUPERUSER,
    PERMISSIONS,
    ROLE_PERMISSION_MAP,
    ROLES,
)

# ── Colour helpers (terminal output) ─────────────────────────────────────────


def green(s):
    return f"\033[92m{s}\033[0m"


def yellow(s):
    return f"\033[93m{s}\033[0m"


def cyan(s):
    return f"\033[96m{s}\033[0m"


def bold(s):
    return f"\033[1m{s}\033[0m"


# ── Step functions ────────────────────────────────────────────────────────────


def seed_permissions(db: Session) -> dict[str, Permission]:
    """
    Upsert all permissions from PERMISSIONS into the database.
    Returns a name → Permission ORM object mapping for role wiring.
    """
    print(f"\n{bold('[ 1/3 ] Permissions')}")
    perm_map: dict[str, Permission] = {}
    created = updated = 0

    for name, (category, description) in PERMISSIONS.items():
        existing = db.query(Permission).filter(Permission.name == name).first()

        if existing:
            changed = False
            if existing.description != description:
                existing.description = description
                changed = True
            if existing.category != category:
                existing.category = category
                changed = True
            perm_map[name] = existing
            if changed:
                print(f"  {yellow('~')} {name:<35} (updated)")
                updated += 1
            else:
                print(f"  {cyan('·')} {name:<35} (already exists)")
        else:
            perm = Permission(name=name, description=description, category=category)
            db.add(perm)
            db.flush()
            perm_map[name] = perm
            print(f"  {green('+')} {name:<35} (created)")
            created += 1

    db.commit()
    print(
        f"  → {green(f'{created} created')}, {yellow(f'{updated} updated')}, "
        f"{len(PERMISSIONS) - created - updated} unchanged"
    )
    return perm_map


def seed_roles(db: Session, perm_map: dict[str, Permission]) -> None:
    """
    Upsert all roles and wire them to their permissions.
    """
    print(f"\n{bold('[ 2/3 ] Roles & permission assignments')}")
    roles_created = links_added = 0

    for role_name, (display_name, description, is_system) in ROLES.items():
        role = db.query(Role).filter(Role.name == role_name).first()

        if not role:
            role = Role(
                name=role_name,
                display_name=display_name,
                description=description,
                is_system_role=is_system,
            )
            db.add(role)
            db.flush()
            print(f"  {green('+')} {role_name}")
            roles_created += 1
        else:
            print(f"  {cyan('·')} {role_name} (exists)")

        # Reconcile permissions — add any new ones, never remove
        expected_perm_names: list[str] = ROLE_PERMISSION_MAP.get(role_name, [])
        current_perm_names: set[str] = {p.name for p in role.permissions}

        for perm_name in expected_perm_names:
            if perm_name not in perm_map:
                print(f"    {yellow('!')} Unknown permission '{perm_name}' — skipped")
                continue
            if perm_name not in current_perm_names:
                role.permissions.append(perm_map[perm_name])
                print(f"    {green('+')} linked → {perm_name}")
                links_added += 1
            else:
                print(f"    {cyan('·')} linked · {perm_name}")

    db.commit()
    print(
        f"  → {green(f'{roles_created} roles created')}, "
        f"{green(f'{links_added} permission links added')}"
    )


def seed_superuser(db: Session) -> None:
    """
    Create the default superuser if none exists, and ensure they always
    have the 'admin' role assigned.

    Why assign a role to a superuser?
      is_superuser=True bypasses all permission checks — so the admin can
      always act on everything. But without a role, the UI shows an empty
      roles list, which looks broken and confuses operators. Assigning the
      'admin' role makes the account self-documenting and consistent.
    """
    print(f"\n{bold('[ 3/3 ] Default superuser')}")

    admin_role = db.query(Role).filter(Role.name == "admin").first()
    if not admin_role:
        print(f"  {yellow('!')} 'admin' role not found — run seeder after migrations")
        return

    existing_super = db.query(User).filter(User.is_superuser == True).first()

    if existing_super:
        # Backfill: assign admin role if missing (fixes accounts created before this fix)
        current_role_names = {r.name for r in existing_super.roles}
        if "admin" not in current_role_names:
            existing_super.roles.append(admin_role)
            db.commit()
            print(f"  {yellow('~')} {existing_super.email} — backfilled 'admin' role")
        else:
            print(f"  {cyan('·')} {existing_super.email} — already has 'admin' role")
        return

    user = User(
        email=DEFAULT_SUPERUSER["email"],
        full_name=DEFAULT_SUPERUSER["full_name"],
        hashed_password=hash_password(DEFAULT_SUPERUSER["password"]),
        is_superuser=True,
        is_active=True,
        must_change_password=True,
    )
    user.roles = [admin_role]  # ← assign admin role on creation
    db.add(user)
    db.commit()

    print(f"  {green('+')} Created superuser  : {user.email}")
    print(
        f"  {green('+')} Role assigned       : admin ({len(admin_role.permissions)} permissions)"
    )
    print(f"  {yellow('⚠')}  Default password   : {DEFAULT_SUPERUSER['password']}")
    print(f"  {yellow('⚠')}  Change immediately via PATCH /api/v1/auth/password")


# ── Main ──────────────────────────────────────────────────────────────────────


def run_seed(db: Session) -> None:
    print(bold("\n══════════════════════════════════════════"))
    print(bold("  Afya Guard — Database Seeder"))
    print(bold("══════════════════════════════════════════"))

    perm_map = seed_permissions(db)
    seed_roles(db, perm_map)
    seed_superuser(db)

    print(f"\n{green(bold('✓ Seeding complete.'))}\n")


if __name__ == "__main__":
    db: Session = SessionLocal()
    try:
        run_seed(db)
    except Exception as e:
        db.rollback()
        print(f"\n\033[91m✗ Seeding failed: {e}\033[0m\n")
        raise
    finally:
        db.close()
