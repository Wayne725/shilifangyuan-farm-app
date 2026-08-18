from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import require_admin
from ..config import Settings, get_settings
from ..database import get_session
from ..identity_numbers import next_identity_number
from ..integrations.common import IntegrationError
from ..integrations.pii_crypto import pii_cipher_from_settings
from ..models import (
    AdminAudit,
    PickupLocation,
    Supplier,
    SupplierAccreditation,
    SupplierAccreditationStatus,
    SupplierDocument,
    User,
    new_id,
)
from ..schemas import (
    PickupLocationCreate,
    PickupLocationRead,
    PickupLocationUpdate,
    SupplierAccreditationCreate,
    SupplierAccreditationRead,
    SupplierCreate,
    SupplierDocumentRead,
    SupplierRead,
    SupplierUpdate,
)


operations_router = APIRouter(tags=["operations"])


def _cipher(settings: Settings):
    try:
        return pii_cipher_from_settings(settings)
    except IntegrationError as exc:
        raise HTTPException(
            status_code=503,
            detail="私密資料加密設定尚未完成",
        ) from exc


def _supplier_aad(supplier_id: str) -> str:
    return f"supplier:{supplier_id}"


def _encrypt_optional(cipher, value: Optional[str], aad: str) -> Optional[str]:
    if value is None or value == "":
        return None
    return cipher.encrypt_text(value, associated_data=aad)


def _decrypt_optional(cipher, value: Optional[str], aad: str) -> Optional[str]:
    if value is None:
        return None
    return cipher.decrypt_text(value, associated_data=aad)


def _supplier_read(supplier: Supplier, settings: Settings) -> SupplierRead:
    cipher = _cipher(settings)
    aad = _supplier_aad(supplier.id)
    accreditations = sorted(
        supplier.accreditations,
        key=lambda item: (item.reviewed_on, item.created_at),
        reverse=True,
    )
    return SupplierRead(
        id=supplier.id,
        supplier_number=supplier.supplier_number,
        business_name=supplier.business_name,
        tax_id=supplier.tax_id,
        responsible_person=cipher.decrypt_text(
            supplier.responsible_person_encrypted,
            associated_data=aad,
        ),
        contact_person=cipher.decrypt_text(
            supplier.contact_person_encrypted,
            associated_data=aad,
        ),
        phone=cipher.decrypt_text(supplier.phone_encrypted, associated_data=aad),
        email=cipher.decrypt_text(supplier.email_encrypted, associated_data=aad),
        line_id=_decrypt_optional(cipher, supplier.line_id_encrypted, aad),
        settlement_terms=supplier.settlement_terms,
        bank_account=cipher.decrypt_text(
            supplier.bank_account_encrypted,
            associated_data=aad,
        ),
        accredited_on=supplier.accredited_on,
        is_active=supplier.is_active,
        created_at=supplier.created_at,
        updated_at=supplier.updated_at,
        accreditations=[
            SupplierAccreditationRead.model_validate(item)
            for item in accreditations
        ],
        documents=[
            SupplierDocumentRead.model_validate(item)
            for item in supplier.documents
            if item.deleted_at is None
        ],
    )


def _supplier_query():
    return select(Supplier).options(
        selectinload(Supplier.accreditations),
        selectinload(Supplier.documents),
    )


@operations_router.get(
    "/v1/pickup-locations",
    response_model=list[PickupLocationRead],
)
async def list_pickup_locations(
    session: AsyncSession = Depends(get_session),
) -> list[PickupLocation]:
    return list(
        await session.scalars(
            select(PickupLocation)
            .where(PickupLocation.is_active.is_(True))
            .order_by(PickupLocation.sort_order, PickupLocation.name)
        )
    )


@operations_router.get(
    "/v1/admin/pickup-locations",
    response_model=list[PickupLocationRead],
)
async def list_admin_pickup_locations(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[PickupLocation]:
    return list(
        await session.scalars(
            select(PickupLocation).order_by(
                PickupLocation.sort_order,
                PickupLocation.name,
            )
        )
    )


@operations_router.post(
    "/v1/admin/pickup-locations",
    response_model=PickupLocationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_pickup_location(
    body: PickupLocationCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> PickupLocation:
    location = PickupLocation(**body.model_dump())
    session.add(location)
    await session.flush()
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="pickup_location.created",
            aggregate_type="pickup_location",
            aggregate_id=location.id,
            data={"code": location.code, "name": location.name},
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="領取地點代碼或名稱已存在",
        ) from exc
    return location


@operations_router.patch(
    "/v1/admin/pickup-locations/{location_id}",
    response_model=PickupLocationRead,
)
async def update_pickup_location(
    location_id: str,
    body: PickupLocationUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> PickupLocation:
    location = await session.scalar(
        select(PickupLocation)
        .where(PickupLocation.id == location_id)
        .with_for_update()
    )
    if location is None:
        raise HTTPException(status_code=404, detail="找不到領取地點")
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(location, field, value)
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="pickup_location.updated",
            aggregate_type="pickup_location",
            aggregate_id=location.id,
            data={"updated_fields": sorted(updates)},
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="領取地點代碼或名稱已存在",
        ) from exc
    return location


@operations_router.get(
    "/v1/admin/suppliers",
    response_model=list[SupplierRead],
)
async def list_suppliers(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[SupplierRead]:
    suppliers = list(
        await session.scalars(
            _supplier_query().order_by(
                Supplier.is_active.desc(),
                Supplier.business_name,
            )
        )
    )
    return [_supplier_read(supplier, settings) for supplier in suppliers]


@operations_router.get(
    "/v1/admin/suppliers/{supplier_id}",
    response_model=SupplierRead,
)
async def get_supplier(
    supplier_id: str,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SupplierRead:
    supplier = await session.scalar(
        _supplier_query().where(Supplier.id == supplier_id)
    )
    if supplier is None:
        raise HTTPException(status_code=404, detail="找不到供應者")
    return _supplier_read(supplier, settings)


@operations_router.post(
    "/v1/admin/suppliers",
    response_model=SupplierRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_supplier(
    body: SupplierCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SupplierRead:
    cipher = _cipher(settings)
    supplier_id = new_id()
    aad = _supplier_aad(supplier_id)
    supplier = Supplier(
        id=supplier_id,
        supplier_number=body.supplier_number,
        business_name=body.business_name,
        tax_id=body.tax_id,
        responsible_person_encrypted=cipher.encrypt_text(
            body.responsible_person,
            associated_data=aad,
        ),
        contact_person_encrypted=cipher.encrypt_text(
            body.contact_person,
            associated_data=aad,
        ),
        phone_encrypted=cipher.encrypt_text(body.phone, associated_data=aad),
        email_encrypted=cipher.encrypt_text(str(body.email), associated_data=aad),
        line_id_encrypted=_encrypt_optional(cipher, body.line_id, aad),
        settlement_terms=body.settlement_terms,
        bank_account_encrypted=cipher.encrypt_text(
            body.bank_account,
            associated_data=aad,
        ),
        encryption_key_version=cipher.current_version,
        is_active=body.is_active,
    )
    session.add(supplier)
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="supplier.created",
            aggregate_type="supplier",
            aggregate_id=supplier.id,
            data={"business_name": supplier.business_name},
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="供應商編號或統一編號已存在",
        ) from exc
    supplier = await session.scalar(
        _supplier_query().where(Supplier.id == supplier.id)
    )
    return _supplier_read(supplier, settings)


@operations_router.patch(
    "/v1/admin/suppliers/{supplier_id}",
    response_model=SupplierRead,
)
async def update_supplier(
    supplier_id: str,
    body: SupplierUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SupplierRead:
    supplier = await session.scalar(
        select(Supplier)
        .where(Supplier.id == supplier_id)
        .with_for_update()
    )
    if supplier is None:
        raise HTTPException(status_code=404, detail="找不到供應者")
    updates = body.model_dump(exclude_unset=True, mode="json")
    direct_fields = {
        "supplier_number",
        "business_name",
        "tax_id",
        "settlement_terms",
        "is_active",
    }
    for field in direct_fields & updates.keys():
        setattr(supplier, field, updates[field])
    cipher = _cipher(settings)
    aad = _supplier_aad(supplier.id)
    private_fields = {
        "responsible_person": "responsible_person_encrypted",
        "contact_person": "contact_person_encrypted",
        "phone": "phone_encrypted",
        "email": "email_encrypted",
        "line_id": "line_id_encrypted",
        "bank_account": "bank_account_encrypted",
    }
    for source, target in private_fields.items():
        if source not in updates:
            continue
        value = updates[source]
        if value is None and source != "line_id":
            raise HTTPException(status_code=422, detail=f"{source} 不可為空")
        setattr(
            supplier,
            target,
            _encrypt_optional(cipher, value, aad),
        )
    supplier.encryption_key_version = cipher.current_version
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="supplier.updated",
            aggregate_type="supplier",
            aggregate_id=supplier.id,
            data={"updated_fields": sorted(updates)},
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="供應商編號或統一編號已存在",
        ) from exc
    supplier = await session.scalar(
        _supplier_query().where(Supplier.id == supplier.id)
    )
    return _supplier_read(supplier, settings)


@operations_router.post(
    "/v1/admin/suppliers/{supplier_id}/accreditations",
    response_model=SupplierRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_supplier_accreditation(
    supplier_id: str,
    body: SupplierAccreditationCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SupplierRead:
    supplier = await session.scalar(
        select(Supplier)
        .where(Supplier.id == supplier_id)
        .with_for_update()
    )
    if supplier is None:
        raise HTTPException(status_code=404, detail="找不到供應者")
    accreditation = SupplierAccreditation(
        supplier_id=supplier.id,
        reviewer_id=admin.id,
        **body.model_dump(),
    )
    session.add(accreditation)
    if body.status == SupplierAccreditationStatus.APPROVED:
        if supplier.supplier_number is None:
            reviewed_at = datetime.combine(
                body.reviewed_on,
                time.min,
                tzinfo=timezone.utc,
            )
            supplier.supplier_number = await next_identity_number(
                session,
                Supplier.supplier_number,
                "SUP",
                reviewed_at,
            )
        supplier.accredited_on = body.reviewed_on
        supplier.is_active = True
    elif body.status == SupplierAccreditationStatus.REJECTED:
        supplier.is_active = False
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="supplier.accreditation_recorded",
            aggregate_type="supplier",
            aggregate_id=supplier.id,
            reason=body.result_notes or body.process_notes,
            data={"status": body.status.value},
        )
    )
    await session.commit()
    supplier = await session.scalar(
        _supplier_query().where(Supplier.id == supplier.id)
    )
    return _supplier_read(supplier, settings)
