from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .utils import pop_flashes

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def render(request: Request, name: str, user=None, status_code: int = 200, **context):
    context.update({"user": user, "flashes": pop_flashes(request)})
    return templates.TemplateResponse(request, name, context, status_code=status_code)
