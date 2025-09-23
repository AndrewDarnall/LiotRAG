""" RAG Microservice Client """
from httpx import AsyncClient
from src.frontend.models.endpoints import APIEndpoint
import html
import re

async def fetch_external_context(prompt: str) -> str:
    async with AsyncClient() as client:
        try:
            resp = await client.post(APIEndpoint.RAG_SERVICE.url, json={"query": prompt})
            resp.raise_for_status()
            data = resp.json()  # If your httpx version supports this synchronously. Otherwise: await resp.json()

            # If the API adds "summary" key in the future, return it directly
            if "summary" in data:
                return data["summary"]

            results = data.get("results", {})
            if not results:
                return "No relevant context found."

            summaries = []
            for chunk_id, chunk_data in results.items():
                content = chunk_data.get("content", "")
                if not content.strip():
                    continue

                # Unescape common escape sequences in content (e.g. javascript:void\(0\))
                # Replace literal backslash escapes with normal chars
                unescaped_content = re.sub(r"\\(.)", r"\1", content)

                # Alternatively, unescape HTML entities if present
                unescaped_content = html.unescape(unescaped_content)

                # Replace line breaks with space for cleaner inline summary
                clean = unescaped_content.replace("\r", " ").replace("\n", " ").strip()

                if clean:
                    summaries.append(clean)

            # Join with a double newline for readability
            return "\n\n".join(summaries) if summaries else "No relevant context found."

        except Exception as e:
            print(f"❌ RAG fetch failed: {e}")
            raise
