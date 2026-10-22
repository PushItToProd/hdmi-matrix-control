"""Server-rendered controls; automatic refreshes never read the device."""

import logging
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)
ROOT = Path(__file__).parent
TEMPLATES = Environment(loader=FileSystemLoader(ROOT / 'templates'),
                        autoescape=select_autoescape())
MODES = {
    'single': ('Single input', ('input',)),
    '2x2': ('Four-way grid', ('combination',)),
    '2plr': ('Side by side', ('left', 'right')),
    '2pud': ('Top and bottom', ('top', 'bottom')),
    '1b3s': ('One large + three small', ('combination',)),
    'pip': ('Picture in picture', ('main', 'small')),
}
LABELS = {'input': 'Full-screen input', 'combination': 'Arrangement',
          'left': 'Left input', 'right': 'Right input', 'top': 'Top input',
          'bottom': 'Bottom input', 'main': 'Main input', 'small': 'Small input'}


def create_ui_router(get_matrix, get_cache, status_to_json, apply_outputs, apply_model):
    router = APIRouter(include_in_schema=False)
    router.mount('/ui/static', StaticFiles(directory=ROOT / 'static'), name='ui-static')

    def context(cache):
        memory = cache.remembered()
        observed = memory.observation
        state = status_to_json(observed.value) if observed else {'outputs': {}, 'inputs': {}}
        for output, route in memory.requested.items():
            previous = state['outputs'].get(output, {})
            # A layout's fields must replace the previous layout's fields.
            state['outputs'][output] = ({'copy_a': previous.get('copy_a')}
                                         if output == 'B' else {}) | route
        return dict(state=state, requested=memory.requested, uncertain=memory.uncertain,
                    observed_at=observed.observed_at.isoformat(timespec='seconds') if observed else None,
                    modes=MODES, labels=LABELS)

    def render(cache, template='state.html', message='', error=False):
        return HTMLResponse(TEMPLATES.get_template(template).render(
            **context(cache), message=message, error=error),
            headers={'Cache-Control': 'no-store'})

    def refresh(cache):
        try:
            if cache.refresh_display():
                return '', False
            return 'Device refresh is limited to once every five seconds. Showing retained state.', False
        except Exception as exc:
            logger.exception('UI device refresh failed')
            return f'Device read failed: {exc}. Showing retained state.', True

    @router.get('/', response_class=HTMLResponse)
    def index(cache=Depends(get_cache)):
        memory = cache.remembered()
        message, error = ('', False)
        if memory.observation is None and not memory.requested:
            message, error = refresh(cache)
        return render(cache, 'index.html', message, error)

    @router.get('/ui/state', response_class=HTMLResponse)
    def state(cache=Depends(get_cache)):
        return render(cache)

    @router.post('/ui/refresh', response_class=HTMLResponse)
    def refresh_state(cache=Depends(get_cache)):
        message, error = refresh(cache)
        return render(cache, message=message, error=error)

    @router.get('/ui/layout', response_class=HTMLResponse)
    def layout(mode: str = 'single', cache=Depends(get_cache)):
        if mode not in MODES:
            raise HTTPException(400, 'Unknown layout')
        current = context(cache)['state']['outputs'].get('A', {})
        return HTMLResponse(TEMPLATES.get_template('layout_fields.html').render(
            mode=mode, fields=MODES[mode][1], labels=LABELS, current=current))

    def submit(values, matrix, cache):
        try:
            if values.get('output') in ('A', 'B'):
                payload = {values['output']: int(values.get('input', ''))}
            else:
                mode = values.get('mode', '')
                if mode not in MODES:
                    raise ValueError('Choose a valid layout')
                route = {'mode': mode}
                route.update({field: int(values.get(field, '')) for field in MODES[mode][1]})
                payload = {'A': route}
            apply_outputs(apply_model.model_validate(payload), matrix, cache)
            return render(cache, message='Command acknowledged. Showing requested routing; device not re-read.')
        except (ValueError, ValidationError) as exc:
            return render(cache, message=f'Invalid selection: {exc}', error=True)
        except HTTPException as exc:
            if exc.status_code in (400, 422):
                return render(cache, message=f'Invalid selection: {exc.detail}', error=True)
            return render(cache, message=f'Command failed ({exc.status_code}): {exc.detail}. Refresh from device to verify routing.', error=True)

    @router.post('/ui/apply', response_class=HTMLResponse)
    async def apply(request: Request, matrix=Depends(get_matrix), cache=Depends(get_cache)):
        values = {key: items[-1] for key, items in parse_qs((await request.body()).decode()).items()}
        return await run_in_threadpool(submit, values, matrix, cache)

    return router
