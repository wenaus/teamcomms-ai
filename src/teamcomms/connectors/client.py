"""Authenticated HTTP operations and bounded SSE parsing for host receivers."""

import json
import httpx


class ServiceError(RuntimeError):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


class ServiceClient:
    def __init__(self, config):
        self.config = config
        self.http = httpx.AsyncClient(timeout=35, follow_redirects=False)

    def headers(self):
        return {"Authorization": "Bearer " + self.config.token()}

    async def close(self):
        await self.http.aclose()

    async def request(self, method, path, body=None):
        options = {"params" if method == "GET" else "json": body} if body is not None else {}
        response = await self.http.request(method, self.config.url + path, headers=self.headers(), **options)
        try:
            result = response.json()
        except ValueError:
            raise ServiceError("Service returned a non-JSON response", response.status_code) from None
        if response.is_error or "error" in result:
            raise ServiceError(result.get("error", f"Service HTTP {response.status_code}"), response.status_code)
        return result

    async def get(self, path, **query):
        return await self.request("GET", "/api/comms" + path, query)

    async def post(self, path, body):
        return await self.request("POST", "/api/comms" + path, body)

    async def stream(self, session_id, after):
        async with self.http.stream("GET", self.config.url + "/api/comms/stream", headers=self.headers(),
                params={"session_id": session_id, "after": after}) as response:
            if response.status_code != 200:
                raise ServiceError(f"Stream HTTP {response.status_code}", response.status_code)
            item = {}
            size = 0
            async for line in response.aiter_lines():
                size += len(line.encode())
                if size > 262144:
                    raise ServiceError("Stream event exceeded size bound")
                if line == "":
                    if item:
                        if item.get("event") == "error":
                            raise ServiceError(item["data"]["error"], item["data"].get("status", 0))
                        yield item
                    item, size = {}, 0
                elif line.startswith("event: "):
                    item["event"] = line[7:]
                elif line.startswith("data: "):
                    item["data"] = json.loads(line[6:])
                elif line.startswith("id: "):
                    item["id"] = int(line[4:])
