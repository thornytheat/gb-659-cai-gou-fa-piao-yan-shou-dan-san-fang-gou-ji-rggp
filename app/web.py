from fastapi import Request


def page(request: Request, template: str, title: str, endpoint: str,
         rows: list[dict], extra: dict | None = None):
    context = {"title": title, "endpoint": endpoint, "rows": rows,
               "version": request.app.state.version}
    context.update(extra or {})
    return request.app.state.templates.TemplateResponse(request, template, context)
