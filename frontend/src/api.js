export async function extractAssets(chapter) {
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
