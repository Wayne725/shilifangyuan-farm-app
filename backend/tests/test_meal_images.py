from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.models import AdminAudit, MealPhotoAsset
from tests.support import auth_headers
from tests.test_v2_routers import v2_context


def photo_bytes(fmt="JPEG"):
    output = BytesIO()
    image = Image.new("RGB", (40, 20), "green")
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private metadata"
    image.save(output, fmt, exif=exif)
    return output.getvalue()


async def create_meal(context, name="照片餐盒"):
    response = await context["client"].post("/v1/admin/meals", headers=auth_headers(context["admin"]), json={
        "name": name, "price": 130,
        "option_groups": [{"name": "附餐", "options": [{"name": "飲品", "price_delta": 20}]}],
    })
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_upload_main_and_option_photos_are_normalized_public_and_replaceable(v2_context):
    client, session = v2_context["client"], v2_context["session"]
    admin = auth_headers(v2_context["admin"])
    meal = await create_meal(v2_context)
    option_id = meal["option_groups"][0]["options"][0]["id"]
    path = f"/v1/admin/meals/{meal['id']}/photo"
    urls = []
    for suffix in ["", f"?option_id={option_id}"]:
        response = await client.post(path + suffix, headers={**admin, "Content-Type": "image/jpeg"}, content=photo_bytes())
        assert response.status_code == 200, response.text
        data = response.json()
        url = data["option_groups"][0]["options"][0]["image_url"] if suffix else data["image_url"]
        urls.append(url)
        public = await client.get(url)
        assert public.status_code == 200
        assert public.headers["content-type"] == "image/webp"
        assert public.headers["x-content-type-options"] == "nosniff"
        image = Image.open(BytesIO(public.content))
        assert image.size == (20, 40)
        assert not image.getexif()
        assert data["price"] == meal["price"]
        assert data["option_groups"][0]["options"][0]["price_delta"] == 20
    assert await session.scalar(select(func.count()).select_from(MealPhotoAsset)) == 2
    replaced = await client.post(path, headers={**admin, "Content-Type": "image/png"}, content=photo_bytes("PNG"))
    assert replaced.status_code == 200
    assert replaced.json()["image_url"] != urls[0]
    assert (await client.get(urls[0])).status_code == 404
    assert await session.scalar(select(func.count()).select_from(MealPhotoAsset)) == 2
    removed = await client.delete(path + f"?option_id={option_id}", headers=admin)
    assert removed.json()["option_groups"][0]["options"][0]["image_url"] is None
    assert removed.json()["image_url"] == replaced.json()["image_url"]
    assert (await client.get(urls[1])).status_code == 404
    assert await session.scalar(select(func.count()).select_from(AdminAudit).where(AdminAudit.action.like("meal.photo_%"))) == 4


@pytest.mark.asyncio
async def test_photo_upload_authentication_precedes_reading_the_body(v2_context):
    meal = await create_meal(v2_context)
    for headers, expected in [({}, 401), (auth_headers(v2_context["applicant"]), 403)]:
        consumed = []
        async def stream():
            consumed.append(True)
            yield b"x" * 1024
        response = await v2_context["client"].post(f"/v1/admin/meals/{meal['id']}/photo", headers={**headers, "Content-Type": "image/png"}, content=stream())
        assert response.status_code == expected
        assert not consumed


@pytest.mark.asyncio
async def test_invalid_images_limits_and_foreign_options_do_not_modify_meal(v2_context):
    meal = await create_meal(v2_context)
    other = await create_meal(v2_context, "另一餐盒")
    client, admin = v2_context["client"], auth_headers(v2_context["admin"])
    path = f"/v1/admin/meals/{meal['id']}/photo"
    for content_type, content, expected in [
        ("image/svg+xml", b"<svg/>", 415), ("image/png", b"not an image", 422),
        ("image/png", b"x" * (5 * 1024 * 1024 + 1), 413),
        ("image/webp", photo_bytes()[:50], 422),
    ]:
        response = await client.post(path, headers={**admin, "Content-Type": content_type}, content=content)
        assert response.status_code == expected, response.text
    async def chunked():
        for _ in range(6):
            yield b"x" * (1024 * 1024)
    response = await client.post(path, headers={**admin, "Content-Type": "image/png"}, content=chunked())
    assert response.status_code == 413
    foreign = other["option_groups"][0]["options"][0]["id"]
    assert (await client.post(path + f"?option_id={foreign}", headers={**admin, "Content-Type": "image/jpeg"}, content=photo_bytes())).status_code == 422
    assert (await client.delete(path, headers=auth_headers(v2_context["applicant"]))).status_code == 403
    assert await v2_context["session"].scalar(select(func.count()).select_from(MealPhotoAsset)) == 0
