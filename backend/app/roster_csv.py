from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import Counter

from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError, field_validator

from .member_claims import normalize_email, normalize_member_number

MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_CSV_ROWS = 500
FIELD_LABELS = {
    "member_number": "社員編號", "legal_name": "姓名", "email": "Email",
    "phone": "手機", "share_capital_amount": "股金", "share_count": "股數",
}
HEADER_ALIASES = {
    "member_number": ("社員編號", "社員編碼", "member_number"),
    "legal_name": ("姓名", "社員姓名", "legal_name"),
    "email": ("email", "電子信箱", "電子郵件", "電子郵件email"),
    "phone": ("手機", "手機號碼", "電話", "電話/手機號碼", "phone"),
    "share_capital_amount": ("股金", "share_capital_amount"),
    "share_count": ("股數", "share_count"),
}


def normalized_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


class RosterCsvEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member_number: str = Field(min_length=1, max_length=32)
    legal_name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    phone: str = Field(min_length=8, max_length=24)
    share_capital_amount: int = Field(default=0, strict=True, ge=0, le=2147483647)
    share_count: int = Field(default=0, strict=True, ge=0, le=2147483647)

    @field_validator("member_number", mode="before")
    @classmethod
    def member_number_normalized(cls, value: str) -> str:
        return normalize_member_number(value) if isinstance(value, str) else value

    @field_validator("legal_name", mode="before")
    @classmethod
    def name_normalized(cls, value: str) -> str:
        return normalized_text(value) if isinstance(value, str) else value

    @field_validator("email", mode="before")
    @classmethod
    def email_normalized(cls, value: str) -> str:
        return normalize_email(value) if isinstance(value, str) else value

    @field_validator("phone", mode="before")
    @classmethod
    def phone_normalized(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        phone = re.sub(r"[\s()\-]", "", normalized_text(value))
        if phone.startswith("+886"):
            phone = "0" + phone[4:]
        if not re.fullmatch(r"0[0-9]{7,9}", phone):
            raise ValueError("請填寫含開頭 0 的電話，或 +886 格式")
        return phone


class RosterCsvImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[RosterCsvEntry] = Field(min_length=1, max_length=MAX_CSV_ROWS)


class RosterCsvRow(BaseModel):
    row_number: int
    member_number: str
    legal_name: str
    entry: RosterCsvEntry | None = None
    errors: list[str] = Field(default_factory=list)


class RosterCsvPreview(BaseModel):
    rows: list[RosterCsvRow]
    valid_count: int
    error_count: int


class RosterCsvImported(BaseModel):
    imported_count: int


def duplicate_numbers(entries: list[RosterCsvEntry]) -> set[str]:
    return {number for number, count in Counter(row.member_number for row in entries).items() if count > 1}


def parse_roster_csv(content: bytes) -> list[RosterCsvRow]:
    if len(content) > MAX_CSV_BYTES:
        raise ValueError("CSV 檔案不可超過 2 MB")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("cp950")
        except UnicodeDecodeError as exc:
            raise ValueError("請使用 UTF-8 或 Big5 編碼的 CSV") from exc
    if "\x00" in text:
        raise ValueError("檔案不是有效的 CSV")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        headers = next(reader, [])
        if len(headers) > 20:
            raise ValueError("CSV 欄位數不可超過 20 個")
        aliases = {alias: key for key, values in HEADER_ALIASES.items() for alias in values}
        keys = [aliases.get(re.sub(r"\s", "", normalized_text(header).lower())) for header in headers]
        known_keys = [key for key in keys if key]
        if len(known_keys) != len(set(known_keys)):
            raise ValueError("CSV 有重複的欄位名稱")
        missing = [FIELD_LABELS[key] for key in ("member_number", "legal_name", "email", "phone") if key not in keys]
        if missing:
            raise ValueError(f"CSV 缺少欄位：{'、'.join(missing)}")
        unknown = [header for header, key in zip(headers, keys) if key is None]
        if unknown:
            raise ValueError(f"CSV 不支援欄位：{'、'.join(unknown)}。請使用範本欄位")
        rows: list[RosterCsvRow] = []
        while True:
            row_number = reader.line_num + 1
            values = next(reader, None)
            if values is None:
                break
            if not any(value.strip() for value in values):
                continue
            if len(rows) >= MAX_CSV_ROWS:
                raise ValueError("每次最多匯入 500 筆社員")
            data = {key: normalized_text(value) for key, value in zip(keys, values) if key}
            row = RosterCsvRow(row_number=row_number, member_number=normalize_member_number(data.get("member_number", "")), legal_name=data.get("legal_name", ""))
            if len(values) != len(headers):
                row.errors.append("欄位數與標題不一致")
            for key in ("share_capital_amount", "share_count"):
                value = data.get(key, "")
                if not value:
                    data[key] = 0
                elif re.fullmatch(r"[0-9]+", value) and len(value) <= 10:
                    data[key] = int(value)
                else:
                    row.errors.append(f"{FIELD_LABELS[key]}需為非負整數")
            if not row.errors:
                try:
                    row.entry = RosterCsvEntry.model_validate(data)
                    row.member_number = row.entry.member_number
                    row.legal_name = row.entry.legal_name
                except ValidationError as exc:
                    for error in exc.errors():
                        key = str(error["loc"][0])
                        label = FIELD_LABELS.get(key, key)
                        message = f"{label}必填或格式不正確"
                        if key == "phone":
                            message = "手機需含開頭 0，或使用 +886 格式"
                        if key in {"share_count", "share_capital_amount"}:
                            message = f"{label}需介於 0～2147483647"
                        row.errors.append(message)
            rows.append(row)
    except csv.Error as exc:
        raise ValueError("CSV 格式不正確，請檢查引號與分隔符號") from exc
    if not rows:
        raise ValueError("CSV 沒有社員資料")
    counts = Counter(row.member_number for row in rows if row.member_number)
    for row in rows:
        if counts[row.member_number] > 1:
            row.errors.append("社員編號在檔案中重複")
    return rows
