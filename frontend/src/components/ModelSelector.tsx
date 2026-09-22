import { useEffect, useState } from "react";
import { listModels } from "../api/models";
import type { ModelSummary } from "../api/types";

interface Props {
  tenantId: string;
  value: string;
  onChange: (modelId: string) => void;
  disabled: boolean;
}

export function ModelSelector({ tenantId, value, onChange, disabled }: Props) {
  const [models, setModels] = useState<ModelSummary[]>([]);

  useEffect(() => {
    let cancelled = false;
    listModels(tenantId)
      .then((fetched) => {
        if (cancelled) return;
        setModels(fetched);
        if (!value && fetched.length > 0) onChange(fetched[0].id);
      })
      .catch(() => {
        if (!cancelled) setModels([]);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-fetch when the tenant changes
  }, [tenantId]);

  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled || models.length === 0}>
      {models.length === 0 && <option value="">No models configured</option>}
      {models.map((model) => (
        <option key={model.id} value={model.id}>
          {model.id} ({model.provider})
        </option>
      ))}
    </select>
  );
}
