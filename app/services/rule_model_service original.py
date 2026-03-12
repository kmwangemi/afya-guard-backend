"""
SHA Fraud Detection — Rule Service & Model Version Service

Rule Service:   CRUD + toggle for configurable fraud rules.
Model Service:  Register, deploy, and track ML model versions.
"""

import uuid
from datetime import UTC, datetime
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums_model import AuditAction
from app.models.fraud_rule_model import FraudRule
from app.models.model_version_model import ModelVersion
from app.models.user_model import User
from app.schemas.admin_schema import (
    FraudRuleCreate,
    FraudRuleResponse,
    FraudRuleUpdate,
    ModelDeployResponse,
    ModelVersionCreate,
    ModelVersionResponse,
    RuleToggleResponse,
)
from app.services.audit_service import AuditService

# ══════════════════════════════════════════════════════════════════════════════
# RULE SERVICE
# ══════════════════════════════════════════════════════════════════════════════


class RuleService:

    @staticmethod
    async def create_rule(
        db: AsyncSession,
        data: FraudRuleCreate,
        created_by: User,
    ) -> FraudRuleResponse:
        result = await db.execute(
            select(FraudRule).filter(FraudRule.rule_name == data.rule_name)
        )
        if result.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Rule '{data.rule_name}' already exists",
            )
        rule = FraudRule(**data.model_dump(), created_by=created_by.id)
        db.add(rule)
        await db.commit()
        await db.refresh(rule)
        await AuditService.log(
            db,
            AuditAction.RULE_CREATED,
            user_id=created_by.id,
            entity_type="FraudRule",
            entity_id=rule.id,
            metadata={"rule_name": rule.rule_name, "weight": float(rule.weight)},
        )
        return FraudRuleResponse.model_validate(rule)

    @staticmethod
    async def list_rules(
        db: AsyncSession,
        active_only: bool = False,
        category: Optional[str] = None,
    ) -> list[FraudRuleResponse]:
        query = select(FraudRule)
        if active_only:
            query = query.filter(FraudRule.is_active == True)
        if category:
            query = query.filter(FraudRule.category == category)
        result = await db.execute(query.order_by(FraudRule.weight.desc()))
        return [FraudRuleResponse.model_validate(r) for r in result.scalars().all()]

    @staticmethod
    async def get_rule(
        db: AsyncSession,
        rule_id: uuid.UUID,
    ) -> FraudRuleResponse:
        result = await db.execute(select(FraudRule).filter(FraudRule.id == rule_id))
        rule = result.scalars().first()
        if not rule:
            raise HTTPException(status_code=404, detail="Rule not found")
        return FraudRuleResponse.model_validate(rule)

    @staticmethod
    async def update_rule(
        db: AsyncSession,
        rule_id: uuid.UUID,
        data: FraudRuleUpdate,
        updated_by: User,
    ) -> FraudRuleResponse:
        result = await db.execute(select(FraudRule).filter(FraudRule.id == rule_id))
        rule = result.scalars().first()
        if not rule:
            raise HTTPException(status_code=404, detail="Rule not found")
        for field, value in data.model_dump(exclude_none=True).items():
            setattr(rule, field, value)
        await db.commit()
        await db.refresh(rule)
        await AuditService.log(
            db,
            AuditAction.RULE_UPDATED,
            user_id=updated_by.id,
            entity_type="FraudRule",
            entity_id=rule.id,
            metadata=data.model_dump(exclude_none=True),
        )
        return FraudRuleResponse.model_validate(rule)

    @staticmethod
    async def toggle_rule(
        db: AsyncSession,
        rule_id: uuid.UUID,
        toggled_by: User,
    ) -> RuleToggleResponse:
        result = await db.execute(select(FraudRule).filter(FraudRule.id == rule_id))
        rule = result.scalars().first()
        if not rule:
            raise HTTPException(status_code=404, detail="Rule not found")
        rule.is_active = not rule.is_active
        await db.commit()
        await AuditService.log(
            db,
            AuditAction.RULE_TOGGLED,
            user_id=toggled_by.id,
            entity_type="FraudRule",
            entity_id=rule.id,
            metadata={"is_active": rule.is_active},
        )
        state = "activated" if rule.is_active else "deactivated"
        return RuleToggleResponse(
            rule_name=rule.rule_name,
            is_active=rule.is_active,
            message=f"Rule '{rule.rule_name}' has been {state}",
        )


# ══════════════════════════════════════════════════════════════════════════════
# MODEL VERSION SERVICE
# ══════════════════════════════════════════════════════════════════════════════


class ModelService:

    @staticmethod
    async def register_model(
        db: AsyncSession,
        data: ModelVersionCreate,
        registered_by: User,
    ) -> ModelVersionResponse:
        result = await db.execute(
            select(ModelVersion).filter(ModelVersion.version_name == data.version_name)
        )
        if result.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Model version '{data.version_name}' already registered",
            )
        model = ModelVersion(**data.model_dump())
        db.add(model)
        await db.commit()
        await db.refresh(model)
        await AuditService.log(
            db,
            AuditAction.MODEL_REGISTERED,
            user_id=registered_by.id,
            entity_type="ModelVersion",
            entity_id=model.id,
            metadata={
                "version_name": model.version_name,
                "model_type": model.model_type,
            },
        )
        return ModelVersionResponse.model_validate(model)

    @staticmethod
    async def list_models(db: AsyncSession) -> list[ModelVersionResponse]:
        result = await db.execute(
            select(ModelVersion).order_by(ModelVersion.created_at.desc())
        )
        return [ModelVersionResponse.model_validate(m) for m in result.scalars().all()]

    @staticmethod
    async def get_model(
        db: AsyncSession,
        model_id: uuid.UUID,
    ) -> ModelVersionResponse:
        result = await db.execute(
            select(ModelVersion).filter(ModelVersion.id == model_id)
        )
        model = result.scalars().first()
        if not model:
            raise HTTPException(status_code=404, detail="Model version not found")
        return ModelVersionResponse.model_validate(model)

    @staticmethod
    async def deploy_model(
        db: AsyncSession,
        model_id: uuid.UUID,
        deployed_by: User,
    ) -> ModelDeployResponse:
        result = await db.execute(
            select(ModelVersion).filter(ModelVersion.id == model_id)
        )
        target = result.scalars().first()
        if not target:
            raise HTTPException(status_code=404, detail="Model version not found")
        # Deactivate all other deployed models
        await db.execute(
            update(ModelVersion)
            .where(ModelVersion.id != model_id, ModelVersion.is_deployed == True)
            .values(is_deployed=False)
        )
        # Deploy target
        target.is_deployed = True
        target.deployed_at = datetime.now(UTC)
        target.deployed_by = deployed_by.id
        await db.commit()
        await AuditService.log(
            db,
            AuditAction.MODEL_DEPLOYED,
            user_id=deployed_by.id,
            entity_type="ModelVersion",
            entity_id=target.id,
            metadata={"version_name": target.version_name},
        )
        return ModelDeployResponse(
            version_name=target.version_name,
            is_deployed=True,
            deployed_at=target.deployed_at,
            message=f"Model '{target.version_name}' is now active for scoring",
        )
