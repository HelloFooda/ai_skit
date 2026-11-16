export interface Character {
  name: string;
  appearance: string;
  stable_features: string;
}
export interface Prop {
  name: string;
  appearance: string;
  state_changes: string;
}
export interface Scene {
  name: string;
  time: string;
  light: string;
  atmosphere: string;
}
export interface Assets {
  characters: Character[];
  props: Prop[];
  scenes: Scene[];
}
export interface ExtractAssetsResponse {
  run_id: string;
  model: string;
  mock: boolean;
  assets: Assets;
  latency_ms: number;
}

export async function extractAssets(chapter: string): Promise<ExtractAssetsResponse> {
  const res = await fetch("/api/pipeline/extract-assets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chapter }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "请求失败");
  }
  return res.json();
}
