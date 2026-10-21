"""Internal bounded OCR API. One isolated worker at a time, no archive access."""
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading

from fastapi import FastAPI, HTTPException, Request


app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
MAX_BYTES=20_000_000
GATE=threading.BoundedSemaphore(1)
MEDIA_TYPES={'application/pdf','image/png','image/jpeg','image/tiff','image/webp'}


def run_worker(data, media_type, pages):
    with TemporaryDirectory(prefix='agentai-ocr-') as folder:
        path=Path(folder)/'input.bin'
        path.write_bytes(data)
        process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('worker.py')),
                                  str(path),media_type,json.dumps(pages)],
                                 stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
        try:
            output,_=process.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            if hasattr(os,'killpg'): os.killpg(process.pid,signal.SIGKILL)
            else: process.kill()
            process.communicate()
            raise HTTPException(504,'OCR timed out') from None
        if process.returncode:
            raise HTTPException(504 if process.returncode==3 else 422,'OCR could not recognize this document')
        return json.loads(output)


@app.get('/health')
def health():
    return {'status':'ok'}


@app.post('/recognize')
async def recognize(request: Request, pages: str | None = None):
    media_type=request.headers.get('content-type','').split(';',1)[0]
    if media_type not in MEDIA_TYPES:
        raise HTTPException(415,'Supported: PDF/PNG/JPEG/TIFF/WebP')
    try:
        selected=[int(value) for value in pages.split(',')] if pages is not None else None
        if selected is not None and (not selected or len(selected)>50 or len(set(selected))!=len(selected)
                                     or any(page<1 for page in selected)):
            raise ValueError()
        if int(request.headers.get('content-length','0'))>MAX_BYTES:
            raise HTTPException(413,'Maximum file size: 20 MB')
    except ValueError:
        raise HTTPException(422,'Invalid pages or Content-Length') from None
    if not GATE.acquire(blocking=False):
        raise HTTPException(429,'OCR busy; retry later')
    try:
        body=bytearray()
        async for chunk in request.stream():
            if len(body)+len(chunk)>MAX_BYTES:
                raise HTTPException(413,'Maximum file size: 20 MB')
            body.extend(chunk)
        if not body: raise HTTPException(422,'Empty document')
        # Shield the worker so cancellation cannot release the gate while native
        # parsing is still running. Its own deadline kills the process group.
        task=asyncio.create_task(asyncio.to_thread(run_worker,bytes(body),media_type,selected))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try: await task
            finally: raise
    finally:
        GATE.release()
