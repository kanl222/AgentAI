"""HTTP routes for system."""
import os
from pathlib import Path
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from ...access import require


def create_router(application):
    router = APIRouter()

    def user(authorization: str | None = Header(default=None)):
        return application.authenticate(authorization)

    @router.get('/session')
    def session(p=Depends(user)):
        require(p,'reader')
        return {'name':p['name'],'roles':p['roles'],'sources':p['sources']}


    @router.get('/health')
    def health():
        return {'status':'alive'}


    @router.get('/ready')
    def ready(p=Depends(user)):
        import httpx
        require(p,'reader')
        try:
            with httpx.Client(timeout=5) as client:
                headers={'Authorization':'Bearer '+os.getenv('LMSTUDIO_API_KEY','lm-studio')}
                response=client.get(os.getenv('LMSTUDIO_BASE_URL','http://host.docker.internal:1234/v1').rstrip('/')+'/models',headers=headers)
                response.raise_for_status()
                if os.getenv('DATABASE_URL'):
                    with application.database().connect() as db: db.execute('SELECT 1')
                names={m['id'] for m in response.json()['data']}
                wanted={os.getenv('CHAT_MODEL','qwen3-4b'),os.getenv('EMBED_MODEL','text-embedding-nomic-embed-text-v1.5')}
                if not wanted <= names:
                    raise ValueError('Models missing')
                response=client.get(os.getenv('QDRANT_URL','http://qdrant:6333')+'/readyz')
                response.raise_for_status()
        except Exception as error:
            raise HTTPException(503,'Dependencies unavailable; check models and services') from error
        return {'status':'ready'}


    @router.get('/',include_in_schema=False)
    @router.get('/library',include_in_schema=False)
    def library():
        return FileResponse('web/api-console/index.html',headers={'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",'Cache-Control':'no-store'})


    @router.get('/favicon.ico',include_in_schema=False)
    def favicon():
        return Response(status_code=204)


    @router.get('/app.js',include_in_schema=False)
    @router.get('/app.css',include_in_schema=False)
    def console_asset(request: Request):
        return FileResponse(Path('web/api-console')/request.url.path.rsplit('/',1)[-1],
                            headers={'X-Content-Type-Options':'nosniff','Cache-Control':'no-cache'})


    @router.get('/assets/{name}',include_in_schema=False)
    def asset(name: str):
        if name not in {'library.js','library.css'}: raise HTTPException(404)
        target = {'library.js':'app.js','library.css':'app.css'}[name]
        return FileResponse(Path('web/api-console')/target,headers={'X-Content-Type-Options':'nosniff','Cache-Control':'no-cache'})

    return router
