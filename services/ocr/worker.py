"""Disposable native PDF/image and Tesseract worker. Files stay in its temp dir."""
from io import BytesIO
import json
import math
from pathlib import Path
import subprocess
import sys

from PIL import Image, ImageOps
import pypdfium2 as pdfium


MAX_PIXELS = 20_000_000
MAX_PAGES = 50
MAX_TEXT = 1_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def selected_pages(requested, count):
    if requested is None and not 1<=count<=MAX_PAGES:
        raise ValueError('Select 1 to 50 existing pages')
    selected = list(range(1,count+1)) if requested is None else requested
    if (not selected or len(selected)>MAX_PAGES or len(set(selected))!=len(selected)
            or any(type(page) is not int or not 1<=page<=count for page in selected)):
        raise ValueError('Select 1 to 50 distinct existing pages')
    return selected


def check_size(width, height):
    if width<=0 or height<=0 or width*height>MAX_PIXELS:
        raise ValueError('Maximum rendered page size: 20 megapixels')


def read_text(image, folder):
    check_size(*image.size)
    path=folder/'page.png'
    image.save(path,format='PNG')
    result=subprocess.run(['tesseract',str(path),'stdout','-l','rus+eng','--psm','3'],
                          capture_output=True,timeout=30,check=True)
    return result.stdout.decode('utf-8').strip()


def recognize(data, media_type, requested, folder):
    result=[]
    if media_type=='application/pdf':
        with pdfium.PdfDocument(data) as pdf:
            for number in selected_pages(requested,len(pdf)):
                page=pdf[number-1]
                try:
                    width,height=page.get_size()
                    check_size(math.ceil(width*200/72),math.ceil(height*200/72))
                    bitmap=page.render(scale=200/72,grayscale=True)
                    try:
                        with bitmap.to_pil() as image:
                            result.append({'page':number,'text':read_text(image,folder)})
                    finally:
                        bitmap.close()
                finally:
                    page.close()
    else:
        with Image.open(BytesIO(data)) as source:
            if source.format not in {'PNG','JPEG','TIFF','WEBP'}:
                raise ValueError('Supported images: PNG/JPEG/TIFF/WebP')
            for number in selected_pages(requested,getattr(source,'n_frames',1)):
                source.seek(number-1)
                check_size(*source.size)
                with ImageOps.exif_transpose(source).convert('RGBA') as oriented:
                    with Image.new('RGB',oriented.size,'white') as image:
                        image.paste(oriented,mask=oriented.getchannel('A'))
                        result.append({'page':number,'text':read_text(image,folder)})
    if sum(len(row['text']) for row in result)>MAX_TEXT:
        raise ValueError('Maximum recognized text size exceeded')
    return {'pages':result,'engine':'tesseract','languages':['rus','eng']}


if __name__=='__main__':
    try:
        path=Path(sys.argv[1])
        output=recognize(path.read_bytes(),sys.argv[2],json.loads(sys.argv[3]),path.parent)
        print(json.dumps(output,ensure_ascii=False))
    except subprocess.TimeoutExpired:
        print('Page recognition timed out',file=sys.stderr)
        raise SystemExit(3)
    except Exception:
        # Native parser errors may contain document content or paths.
        print('Invalid document or recognition failed',file=sys.stderr)
        raise SystemExit(2)
