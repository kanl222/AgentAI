"""HTTP routes for system."""
from pathlib import Path
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from ...access import require
from ...adapters.service_checks import check_services


def create_router(application):
    router = APIRouter()

    def user(authorization: str | None = Header(default=None)):
        return application.authenticate(authorization)

    @router.get('/session')
    @require('reader')
    def session(p=Depends(user)):
        return {'name': p['name'], 'roles': p['roles'], 'sources': p['sources']}

    @router.get('/health')
    def health():
        return {'status': 'alive'}

    @router.get('/ready')
    @router.get('/diagnostics/services')
    @require('reader')
    def ready(p=Depends(user)):
        """Check dependencies independently without starting model inference."""
        report = check_services()
        return JSONResponse(report, status_code=200 if report['status'] == 'ready' else 503,
                            headers={'Cache-Control': 'no-store'})

    @router.get('/', include_in_schema=False)
    @router.get('/library', include_in_schema=False)
    def library():
        return FileResponse('web/api-console/index.html', headers={'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'", 'Cache-Control': 'no-store'})

    @router.get('/favicon.ico', include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    @router.get('/app.js', include_in_schema=False)
    @router.get('/app.css', include_in_schema=False)
    def console_asset(request: Request):
        return FileResponse(Path('web/api-console') / request.url.path.rsplit('/', 1)[-1],
                            headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-cache'})

    @router.get('/assets/{name}', include_in_schema=False)
    def asset(name: str):
        if name not in {'library.js', 'library.css'}:
            raise HTTPException(404)
        target = {'library.js': 'app.js', 'library.css': 'app.css'}[name]
        return FileResponse(Path('web/api-console') / target, headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-cache'})

    return router
