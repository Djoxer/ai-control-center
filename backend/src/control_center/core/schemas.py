"""Shared base for every API schema: snake_case in Python, camelCase in JSON."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,   # gpu_ratio -> gpuRatio in JSON and OpenAPI
        populate_by_name=True,      # Python code may still construct with gpu_ratio=...
    )
