"""Configuration-driven model metadata: capabilities, pricing, and the
internal-id -> provider mapping, loaded from ``models.yaml`` rather than
scattered through application code.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

DEFAULT_CONFIG_PATH = Path(__file__).parent / "models.yaml"


class ModelCapabilities(BaseModel):
    """Not every model -- even within one provider -- supports the same
    things, so this is attached per model in ``models.yaml``, never
    inferred from provider name or guessed from a model-id string prefix
    (e.g. ``if model.startswith("claude")``)."""

    streaming: bool = True
    tools: bool = False
    vision: bool = False
    embeddings: bool = False
    reasoning: bool = False


class PricingConfig(BaseModel):
    """All prices are USD per 1,000,000 tokens -- explicit in the field
    names so a reader never has to guess whether a bare number means
    per-token, per-1K, or per-1M. ``cached_input_per_million`` is
    ``None`` when a provider's cached/cheaper-input pricing for this model
    isn't yet confirmed from an official source (see PROVIDER_NOTES.md),
    not when it's known to be free -- same "not reported" vs "reported as
    zero" distinction as ``Usage``."""

    input_per_million: float
    output_per_million: float
    cached_input_per_million: float | None = None


class ModelConfig(BaseModel):
    """``id`` is what application code passes around (``CompletionRequest.model``).
    ``provider_model_id`` is the literal string an adapter sends upstream,
    resolved from config -- never hardcoded in a service or route. Keeping
    these separate is what stops a provider's naming scheme from leaking
    into the rest of the application; renaming or repointing a model in
    ``models.yaml`` never touches application code."""

    id: str
    provider: str
    provider_model_id: str
    context_window: int
    max_output_tokens: int | None = None
    capabilities: ModelCapabilities
    pricing: PricingConfig


class ModelNotFoundError(LookupError):
    def __init__(self, model_id: str) -> None:
        super().__init__(f"no model configured for internal id '{model_id}'")
        self.model_id = model_id


class ModelRegistry:
    def __init__(self, models: dict[str, ModelConfig]) -> None:
        self._models = models

    @classmethod
    def from_yaml(cls, path: Path = DEFAULT_CONFIG_PATH) -> "ModelRegistry":
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        models = {
            internal_id: ModelConfig(id=internal_id, **config)
            for internal_id, config in raw.get("models", {}).items()
        }
        return cls(models)

    def get(self, model_id: str) -> ModelConfig:
        try:
            return self._models[model_id]
        except KeyError:
            raise ModelNotFoundError(model_id) from None

    def list(self) -> list[ModelConfig]:
        return list(self._models.values())
