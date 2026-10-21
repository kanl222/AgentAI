"""Internal OCR client; no credentials or archive access cross this boundary."""
import os
import httpx


def enabled():
    return bool(os.getenv('OCR_URL','').strip())


def recognize(data, media_type, pages=None):
    url=os.getenv('OCR_URL','').strip().rstrip('/')
    if not url: raise ValueError('OCR is not configured; set OCR_URL')
    params={'pages':','.join(str(page) for page in pages)} if pages is not None else {}
    try:
        with httpx.Client(timeout=httpx.Timeout(210,connect=5),trust_env=False) as client:
            response=client.post(url+'/recognize',params=params,content=data,headers={'Content-Type':media_type})
            response.raise_for_status()
            rows=response.json()['pages']
        result={}
        for row in rows:
            number,text=row['page'],row['text']
            if type(number) is not int or number<1 or number in result or not isinstance(text,str):
                raise ValueError('Invalid OCR response')
            result[number]=text
        if (not result or len(result)>50 or sum(len(text) for text in result.values())>1_000_000
                or (pages is not None and set(result)!=set(pages))
                or (pages is None and set(result)!=set(range(1,len(result)+1)))):
            raise ValueError('Incomplete OCR response')
        return result
    except (httpx.HTTPError,KeyError,TypeError,ValueError):
        raise ValueError('OCR unavailable, document rejected, or incomplete recognition; indexing was not published') from None
