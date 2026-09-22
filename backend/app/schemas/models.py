"""What the frontend is allowed to know about a configured model.

Deliberately not `ModelConfig` itself -- this excludes `pricing` and
`provider_model_id` by construction (they're not fields on this model at
all, not fields that happen to be omitted), so there's no risk of an
unrelated response-model change accidentally exposing either later.
"""

from pydantic import BaseModel

from app.providers.models import ModelCapabilities


class ModelSummary(BaseModel):
    id: str
    provider: str
    context_window: int
    capabilities: ModelCapabilities
