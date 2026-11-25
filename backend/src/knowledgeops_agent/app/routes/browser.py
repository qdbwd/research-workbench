"""工具设置、只读 CDP 探测及校验后的取证图片。"""

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from knowledgeops_agent.app.dependencies import svc
from knowledgeops_agent.harness.browser.capture import read_screenshot
from knowledgeops_agent.harness.browser.settings import BrowserMode, BrowserSettingsService

router = APIRouter()


class BrowserSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    local_attach_enabled: bool | None = None
    default_mode: BrowserMode | None = None


def browser_settings(request: Request) -> BrowserSettingsService:
    services = svc(request)
    return BrowserSettingsService(services.sessions, services.settings)


@router.get("/api/browser/settings")
def get_settings(request: Request):
    return browser_settings(request).get()


@router.patch("/api/browser/settings")
def patch_settings(body: BrowserSettingsPatch, request: Request):
    return browser_settings(request).update(**body.model_dump())


@router.post("/api/browser/probe")
def probe(request: Request):
    return browser_settings(request).probe()


@router.get("/api/runs/{run_id}/artifacts/{artifact_id}/image")
def screenshot(run_id: str, artifact_id: str, request: Request):
    with svc(request).sessions() as session:
        image = read_screenshot(session, run_id, artifact_id)
    return Response(image, media_type="image/png", headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600",
    })

