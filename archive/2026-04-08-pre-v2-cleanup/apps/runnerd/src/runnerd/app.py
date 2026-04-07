from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException

from .models import OwnerReplyIn, WakeRequestBody, WakeResponse
from .service import RunnerdService


def create_app(service: RunnerdService | None = None) -> FastAPI:
    runnerd = service or RunnerdService(state_root=Path.home() / ".clawroom" / "runnerd")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            runnerd.shutdown()

    app = FastAPI(title="ClawRoom runnerd", version="0.1.0", lifespan=lifespan)
    app.state.runnerd = runnerd

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return runnerd.healthz()

    @app.post("/wake", response_model=WakeResponse)
    def wake(body: WakeRequestBody) -> WakeResponse:
        try:
            run = runnerd.wake(body.package)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return WakeResponse(
            accepted=True,
            run_id=run.run_id,
            runner_kind=run.runner_kind,
            status=run.status,
            reason=run.reason,
        )

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        try:
            run = runnerd.get_run(run_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return run.model_dump(mode="json")

    @app.post("/runs/{run_id}/owner-reply")
    def owner_reply(run_id: str, body: OwnerReplyIn) -> dict[str, object]:
        try:
            run = runnerd.submit_owner_reply(run_id, text=body.text, owner_request_id=body.owner_request_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return run.model_dump(mode="json")

    @app.post("/runs/{run_id}/cancel")
    def cancel(run_id: str) -> dict[str, object]:
        try:
            run = runnerd.cancel_run(run_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return run.model_dump(mode="json")

    return app


app = create_app()
