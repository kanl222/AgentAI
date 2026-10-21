"""Extract source locations and bounded text chunks."""
import uuid
from io import BytesIO
from pathlib import Path
from ..settings import ChunkSettings
from ..adapters import ocr

def chunks(text, source, location):
    settings = ChunkSettings.from_env()
    lines = text.splitlines()
    pending, count = [], 0
    def row(body,first,last):
        citation = f'{location}#L{first}-L{last}'
        return {'id':str(uuid.uuid5(uuid.NAMESPACE_URL,source+citation+body)),
                'source':source,'text':body,'citation':citation}
    for number,line in enumerate(lines,1):
        width = min(3000, settings.chars-settings.overlap-1)
        for part in [line[i:i+width] for i in range(0,len(line),width)] or ['']:
            if pending and (count+len(part)+1>settings.chars or number-pending[0][0]>=settings.lines):
                body='\n'.join(value for _,value in pending)
                if body.strip():
                    yield row(body,pending[0][0],pending[-1][0])
                # Carry a bounded tail and its original line locations. Limit both
                # characters and lines so short lines cannot make overlap dominate.
                tail, remaining = [], settings.overlap
                for line_number, value in reversed(pending[-max(1,settings.lines//5):]):
                    if remaining <= 0 or number-line_number >= settings.lines-1:
                        break
                    value = value[-remaining:]
                    tail.insert(0,(line_number,value))
                    remaining -= len(value)+1
                pending = tail
                count = sum(len(value)+1 for _,value in pending)
            pending.append((number,part))
            count+=len(part)+1
    body='\n'.join(value for _,value in pending)
    if body.strip():
        yield row(body,pending[0][0],pending[-1][0])


def document(data, filename, source):
    suffix = Path(filename).suffix.lower()
    location = f'document:{source}/{Path(filename).name}'
    if suffix == '.pdf':
        from .pdf import pages
        texts=list(pages(data))
        missing=[page for page,text in enumerate(texts,1) if not text.strip()]
        recognized=ocr.recognize(data,'application/pdf',missing) if missing and ocr.enabled() else {}
        for page, text in enumerate(texts, 1):
            for row in chunks(recognized.get(page,text), source, f'{location}?page={page}'):
                if page in recognized: row['extraction_method']='ocr'
                yield row
    elif suffix == '.docx':
        from docx import Document
        from docx.table import Table
        doc = Document(BytesIO(data))
        paragraph_index, table_index = 0,0
        for item in doc.iter_inner_content():
            if isinstance(item,Table):
                table_index += 1
                header = [cell.text for cell in item.rows[0].cells] if item.rows else []
                for ri,row in enumerate(item.rows,1):
                    cells = [cell.text for cell in row.cells]
                    text = ' | '.join(cells) if ri==1 else ' | '.join(f'{h}: {value}' for h,value in zip(header,cells))
                    yield from chunks(text,source,f'{location}?table={table_index}&row={ri}')
            else:
                paragraph_index += 1
                yield from chunks(item.text,source,f'{location}?paragraph={paragraph_index}')
    elif suffix in {'.txt', '.md'}:
        yield from chunks(data.decode('utf-8-sig'), source, location)
    elif suffix in {'.png','.jpg','.jpeg','.tif','.tiff','.webp'}:
        media_type={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg',
                    '.tif':'image/tiff','.tiff':'image/tiff','.webp':'image/webp'}[suffix]
        for page,text in sorted(ocr.recognize(data,media_type).items()):
            for row in chunks(text,source,f'{location}?page={page}'):
                yield dict(row,extraction_method='ocr')
    else:
        raise ValueError('Supported: PDF/DOCX/TXT/MD/PNG/JPEG/TIFF/WebP')
