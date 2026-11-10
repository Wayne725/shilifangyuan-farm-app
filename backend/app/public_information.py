from pydantic import BaseModel, ConfigDict, Field, model_validator


class PublicInformation(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    published: bool = False
    legal_name: str = Field(default='', max_length=160)
    tax_id: str = Field(default='', max_length=8)
    address: str = Field(default='', max_length=300)
    phone: str = Field(default='', max_length=40)
    email: str = Field(default='', max_length=254)
    policy_version: str = Field(default='', max_length=40)
    privacy: str = Field(default='', max_length=30000)
    terms: str = Field(default='', max_length=30000)
    returns: str = Field(default='', max_length=30000)

    @model_validator(mode='after')
    def published_information_is_complete(self):
        if not self.published:
            return self
        if any(not getattr(self, name) for name in (
            'legal_name', 'tax_id', 'address', 'phone', 'email', 'policy_version', 'privacy', 'terms', 'returns',
        )):
            raise ValueError('請補齊營業資訊及三份政策內容後再公開')
        from .schemas import is_taiwan_tax_id
        from pydantic import TypeAdapter, EmailStr
        if not is_taiwan_tax_id(self.tax_id):
            raise ValueError('營業統一編號格式不正確')
        TypeAdapter(EmailStr).validate_python(self.email)
        import re
        if not re.fullmatch(r'\+?[0-9 ()-]+', self.phone) or not 7 <= len(re.sub(r'\D', '', self.phone)) <= 15:
            raise ValueError('客服電話格式不正確')
        return self


def require_registration_consent(body, information: PublicInformation) -> dict:
    if not information.published:
        return {}
    from fastapi import HTTPException
    from datetime import datetime, timezone
    if not body.data_consent or body.policy_version != information.policy_version:
        raise HTTPException(422, '請閱讀並同意目前版本的個資告知與服務條款')
    return {'data_consent_version': information.policy_version, 'data_consented_at': datetime.now(timezone.utc)}
