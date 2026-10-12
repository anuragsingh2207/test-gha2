import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Path, Request

GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
GISTS_PER_PAGE = 100

logger = logging.getLogger(__name__)


def create_github_client(
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    return httpx.AsyncClient(
        base_url=os.getenv("GITHUB_API_BASE_URL", GITHUB_API_BASE_URL),
        headers=headers,
        timeout=httpx.Timeout(10.0, connect=3.0),
        transport=transport,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    async with create_github_client() as client:
        app.state.github_client = client
        yield


app = FastAPI(title="Public Gists API", lifespan=lifespan)


async def get_github_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.github_client


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/{username}")
async def get_public_gists(
    username: Annotated[
        str,
        Path(
            min_length=1,
            max_length=39,
            pattern=r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$",
        ),
    ],
    client: Annotated[httpx.AsyncClient, Depends(get_github_client)],
) -> list[dict[str, Any]]:
    gists: list[dict[str, Any]] = []
    page = 1

    while True:
        try:
            response = await client.get(
                f"/users/{username}/gists",
                params={"per_page": GISTS_PER_PAGE, "page": page},
            )
        except httpx.TimeoutException as exc:
            logger.warning(
                "GitHub request timed out for user=%s page=%d",
                username,
                page,
            )
            raise HTTPException(
                status_code=504,
                detail="GitHub API request timed out",
            ) from exc
        except httpx.RequestError as exc:
            logger.warning(
                "Unable to reach GitHub for user=%s page=%d: %s",
                username,
                page,
                exc,
            )
            raise HTTPException(
                status_code=502,
                detail="Unable to reach GitHub API",
            ) from exc

        if response.status_code == 404:
            logger.info("GitHub user not found: %s", username)
            raise HTTPException(status_code=404, detail="GitHub user not found")
        if response.status_code in (403, 429):
            logger.warning(
                "GitHub API returned HTTP %d for user=%s page=%d",
                response.status_code,
                username,
                page,
            )
            raise HTTPException(
                status_code=503,
                detail="GitHub API request was forbidden or rate limited",
            )
        if response.status_code != 200:
            logger.error(
                "GitHub API returned HTTP %d for user=%s page=%d",
                response.status_code,
                username,
                page,
            )
            raise HTTPException(
                status_code=502,
                detail="GitHub API returned an error",
            )

        try:
            page_gists = response.json()
        except ValueError as exc:
            logger.error(
                "GitHub API returned invalid JSON for user=%s page=%d",
                username,
                page,
            )
            raise HTTPException(
                status_code=502,
                detail="GitHub API returned invalid JSON",
            ) from exc

        if not isinstance(page_gists, list) or not all(
            isinstance(gist, dict) for gist in page_gists
        ):
            logger.error(
                "GitHub API returned an unexpected response for user=%s page=%d",
                username,
                page,
            )
            raise HTTPException(
                status_code=502,
                detail="GitHub API returned an unexpected response",
            )

        gists.extend(page_gists)
        if len(page_gists) < GISTS_PER_PAGE:
            return gists

        page += 1
