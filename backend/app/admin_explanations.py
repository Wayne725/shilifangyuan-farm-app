from typing import Annotated

from pydantic import BeforeValidator


def normalize_admin_explanation(value: object) -> object:
    if value is None:
        return ""
    return value.strip() if isinstance(value, str) else value


AdminExplanation = Annotated[str, BeforeValidator(normalize_admin_explanation)]
