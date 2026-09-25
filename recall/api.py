"""FastAPI app skeleton. Starts one bee proxy for the app's lifetime rather
than spawning a subprocess per request."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from recall.bee.client import BeeClient
from recall.bee.process import BeeProxy


@asynccontextmanager
async def lifespan(app: FastAPI):
    proxy = BeeProxy()
    proxy.__enter__()
    app.state.bee_client = BeeClient(proxy.base_url)
    try:
        yield
    finally:
        app.state.bee_client.close()
        proxy.__exit__(None, None, None)


app = FastAPI(title="Recall", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/bee/me")
def bee_me() -> dict:
    return app.state.bee_client.get_me()
