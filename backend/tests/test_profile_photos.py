from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.models import Product, ProfilePhotoAsset, GroupBundle
from app.routers.photos import photos_router
from app.routers.membership import membership_router
from tests.support import api_test_context, auth_headers
from tests.test_meal_images import photo_bytes
from tests.test_v2_routers import v2_context


@pytest.mark.asyncio
async def test_product_upload_permissions_normalization_replace_and_shared_images(v2_context):
    session = v2_context["session"]
    product = Product(slug="upload-product", name="上傳商品", category="加工品", unit="份", member_price=100, nonmember_price=120, stock_quantity=10)
    session.add(product)
    await session.commit()
    async with api_test_context(session, [photos_router]) as client:
        path = f"/v1/admin/products/{product.id}/photo"
        for headers, status in [({}, 401), (auth_headers(v2_context["member_a"]), 403)]:
            consumed = []
            async def body():
                consumed.append(True)
                yield b"not an image"
            response = await client.post(path, headers={**headers, "Content-Type": "image/jpeg"}, content=body())
            assert response.status_code == status
            assert consumed == []
        admin = auth_headers(v2_context["admin"])
        response = await client.post(path, headers={**admin, "Content-Type": "image/jpeg"}, content=photo_bytes())
        assert response.status_code == 200, response.text
        url = response.json()["url"]
        public = await client.get(url)
        assert public.status_code == 200
        image = Image.open(BytesIO(public.content))
        assert image.size == (20, 40) and not image.getexif()
        bundle = GroupBundle(name="共用照片套組", image_url=url, member_price=100, nonmember_price=120)
        session.add(bundle)
        await session.commit()
        response = await client.post(path, headers={**admin, "Content-Type": "image/png"}, content=photo_bytes("PNG"))
        assert response.status_code == 200
        await session.refresh(bundle)
        assert bundle.image_url == response.json()["url"] != url
        assert (await client.get(url)).status_code == 404
        assert await session.scalar(select(func.count()).select_from(ProfilePhotoAsset)) == 1
        assert product.member_price == 100
        assert (await client.delete(path, headers=admin)).status_code == 200
        await session.refresh(bundle)
        assert bundle.image_url is None
        assert await session.scalar(select(func.count()).select_from(ProfilePhotoAsset)) == 0


@pytest.mark.asyncio
async def test_directory_photo_is_private_and_follows_visibility_and_membership(v2_context):
    session = v2_context["session"]
    owner, other, outsider = [auth_headers(v2_context[key]) for key in ("member_a", "member_b", "applicant")]
    async with api_test_context(session, [photos_router, membership_router]) as client:
        response = await client.post("/v1/members/me/photo", headers={**owner, "Content-Type": "image/jpeg"}, content=photo_bytes())
        assert response.status_code == 200, response.text
        url = response.json()["url"]
        assert (await client.get(url)).status_code == 401
        assert (await client.get(url, headers=outsider)).status_code == 403
        assert (await client.get(url, headers=other)).status_code == 404
        own_photo = await client.get(url, headers=owner)
        assert own_photo.status_code == 200
        assert own_photo.headers["cache-control"] == "private, no-store"
        assert (await client.get(url.replace("member-photos", "product-photos"))).status_code == 404
        profile = {"is_public": True, "nickname": "公開社員", "expertise": "種菜", "bio": ""}
        saved = await client.put("/v1/members/me/directory", headers=owner, json=profile)
        assert saved.json()["avatar_url"] == url
        assert (await client.get(url, headers=other)).status_code == 200
        assert (await client.post("/v1/members/me/photo", headers={**outsider, "Content-Type": "image/jpeg"}, content=photo_bytes())).status_code == 403
        profile["is_public"] = False
        assert (await client.put("/v1/members/me/directory", headers=owner, json=profile)).status_code == 200
        assert (await client.get(url, headers=other)).status_code == 404
        assert (await client.delete("/v1/members/me/photo", headers=owner)).status_code == 200
        assert (await client.get(url, headers=owner)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type,content,expected", [
    ("image/jpeg", b"not-a-photo", 422), ("image/svg+xml", b"<svg/>", 415),
    ("image/png", b"x" * (5 * 1024 * 1024 + 1), 413),
])
async def test_profile_upload_rejects_invalid_files(v2_context, content_type, content, expected):
    async with api_test_context(v2_context["session"], [photos_router]) as client:
        response = await client.post("/v1/members/me/photo", headers={**auth_headers(v2_context["member_a"]), "Content-Type": content_type}, content=content)
        assert response.status_code == expected
        assert await v2_context["session"].scalar(select(func.count()).select_from(ProfilePhotoAsset)) == 0
