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
    python -m app.seeds.run            (from project root)
    uv run python -m app.seeds.run     (if using uv)
"""

import asyncio
import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models.permission_model import Permission
from app.models.role_model import Role
from app.models.user_model import User
from app.seeds.seed_data import (
    DEFAULT_SUPERUSER,
    PERMISSIONS,
    ROLE_PERMISSION_MAP,
    ROLES,
)

# ── Colour helpers ────────────────────────────────────────────────────────────


def green(s):
    return f"\033[92m{s}\033[0m"


def yellow(s):
    return f"\033[93m{s}\033[0m"


def cyan(s):
    return f"\033[96m{s}\033[0m"


def bold(s):
    return f"\033[1m{s}\033[0m"


# ── Step functions ────────────────────────────────────────────────────────────


async def seed_permissions(db: AsyncSession) -> dict[str, Permission]:
    """
    Upsert all permissions from PERMISSIONS into the database.
    Returns a name → Permission ORM object mapping for role wiring.
    """
    print(f"\n{bold('[ 1/3 ] Permissions')}")
    perm_map: dict[str, Permission] = {}
    created = updated = 0
    for name, (category, description) in PERMISSIONS.items():
        result = await db.execute(select(Permission).filter(Permission.name == name))
        existing = result.scalars().first()
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
            await db.flush()
            perm_map[name] = perm
            print(f"  {green('+')} {name:<35} (created)")
            created += 1
    await db.commit()
    print(
        f"  → {green(f'{created} created')}, {yellow(f'{updated} updated')}, "
        f"{len(PERMISSIONS) - created - updated} unchanged"
    )
    return perm_map


async def seed_roles(db: AsyncSession, perm_map: dict[str, Permission]) -> None:
    """Upsert all roles and wire them to their permissions."""
    print(f"\n{bold('[ 2/3 ] Roles & permission assignments')}")
    roles_created = links_added = 0
    for role_name, (display_name, description, is_system) in ROLES.items():
        # ✅ Eagerly load permissions to avoid MissingGreenlet
        result = await db.execute(
            select(Role)
            .options(selectinload(Role.permissions))
            .filter(Role.name == role_name)
        )
        role = result.scalars().first()
        if not role:
            role = Role(
                name=role_name,
                display_name=display_name,
                description=description,
                is_system_role=is_system,
            )
            db.add(role)
            await db.flush()
            # ✅ Re-fetch with selectinload after flush for new roles
            result = await db.execute(
                select(Role)
                .options(selectinload(Role.permissions))
                .filter(Role.name == role_name)
            )
            role = result.scalars().first()
            print(f"  {green('+')} {role_name}")
            roles_created += 1
        else:
            print(f"  {cyan('·')} {role_name} (exists)")
        # Safe to access role.permissions — eagerly loaded in both branches
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
    await db.commit()
    print(
        f"  → {green(f'{roles_created} roles created')}, "
        f"{green(f'{links_added} permission links added')}"
    )


async def seed_superuser(db: AsyncSession) -> None:
    """
    Create the default superuser if none exists, and ensure they always
    have the 'admin' role assigned.
    """
    print(f"\n{bold('[ 3/3 ] Default superuser')}")
    # ✅ Eagerly load permissions on admin_role
    role_result = await db.execute(
        select(Role)
        .options(selectinload(Role.permissions))
        .filter(Role.name == "admin")
    )
    admin_role = role_result.scalars().first()
    if not admin_role:
        print(f"  {yellow('!')} 'admin' role not found — run seeder after migrations")
        return
    # ✅ Eagerly load roles on existing superuser
    user_result = await db.execute(
        select(User).options(selectinload(User.roles)).filter(User.is_superuser == True)
    )
    existing_super = user_result.scalars().first()
    if existing_super:
        # Backfill: assign admin role if missing
        current_role_names = {r.name for r in existing_super.roles}  # ✅ safe
        if "admin" not in current_role_names:
            existing_super.roles.append(admin_role)
            await db.commit()
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
    user.roles = [admin_role]
    db.add(user)
    await db.commit()
    print(f"  {green('+')} Created superuser  : {user.email}")
    print(
        f"  {green('+')} Role assigned       : admin ({len(admin_role.permissions)} permissions)"
    )
    print(f"  {yellow('⚠')}  Default password   : {DEFAULT_SUPERUSER['password']}")
    print(f"  {yellow('⚠')}  Change immediately via PATCH /api/v1/auth/password")


# ── Main ──────────────────────────────────────────────────────────────────────


async def run_seed() -> None:
    print(bold("\n══════════════════════════════════════════"))
    print(bold("  Afya Guard — Database Seeder"))
    print(bold("══════════════════════════════════════════"))
    async with AsyncSessionLocal() as db:
        try:
            perm_map = await seed_permissions(db)
            await seed_roles(db, perm_map)
            await seed_superuser(db)
            print(f"\n{green(bold('✓ Seeding complete.'))}\n")
        except Exception as e:
            await db.rollback()
            print(f"\n\033[91m✗ Seeding failed: {e}\033[0m\n")
            raise


if __name__ == "__main__":
    asyncio.run(run_seed())
