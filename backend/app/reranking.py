"""Optional remote reranker; failures retain the original retrieval order."""

import httpx

from .adapters import connection_for
from .errors import AppError


async def rerank(settings, spec, query, candidates):
    try:
        connection = connection_for(settings, spec["rerankProvider"])
        headers = {"Accept": "application/json"}
        if connection.key:
            headers["Authorization" if connection.auth_header == "authorization" else "api-key"] = (
                "Bearer " + connection.key if connection.auth_header == "authorization" else connection.key
            )
        payload = {
            "model": spec["rerankModel"],
            "query": query,
            "documents": [item["excerpt"] for item in candidates[:30]],
            "top_n": min(30, len(candidates)),
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=3), trust_env=False) as client:
            response = await client.post(connection.base_url + "/rerank", json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        rows = data.get("results")
        if not isinstance(rows, list):
            return candidates
        ordered, seen = [], set()
        for row in rows:
            index = row.get("index")
            score = row.get("relevance_score")
            if type(index) is not int or index in seen or not 0 <= index < len(candidates):
                return candidates
            if type(score) not in (int, float) or not 0 <= score <= 1:
                return candidates
            seen.add(index)
            ordered.append({**candidates[index], "score": round(float(score), 5)})
        ordered.extend(item for index, item in enumerate(candidates) if index not in seen)
        return ordered
    except (AppError, httpx.HTTPError, ValueError, TypeError, KeyError):
        return candidates
