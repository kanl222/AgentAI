"""Detect asserted pairs when a source only supplies two unpaired lists."""
import re

from ..knowledge.text import lexical_tokens


def features(text):
    return {word for word in lexical_tokens(text) if len(word)>=3 and word not in {'для','это','что'}}


def unpaired_table_claims(body, rows):
    issues=[]
    for row in rows:
        lines=row['text'].splitlines()
        for start,line in enumerate(lines):
            if not re.match(r'\s*1[.)]\s+',line): continue
            labels=[]; end=start
            while end<len(lines) and re.match(r'\s*\d+[.)]\s+',lines[end]):
                labels.append(re.sub(r'^\s*\d+[.)]\s+','',lines[end])); end+=1
            if len(labels)<2: continue
            if not re.search(r'табл|tables?', ' '.join(lines[end:end+3]),re.I): continue
            values=[]
            for candidate in lines[end:end+10]:
                if re.search(r'[а-яё]',candidate,re.I):
                    if values:break
                    continue
                identifiers=re.findall(r'[A-Za-z_][A-Za-z_0-9]*',candidate)
                if identifiers:values.append(identifiers[-1])
            if len(values)<2:continue
            answer_lines=body.splitlines()
            for index,claim in enumerate(answer_lines):
                mentioned=[value for value in values if re.search(r'(?<!\w)'+re.escape(value)+r'(?!\w)',claim)]
                if len(mentioned)!=1:continue
                context=' '.join(answer_lines[max(0,index-1):index+1])
                label=next((label for label in labels if len(features(label)&features(context))>=2),None)
                if not label:continue
                # Another retrieved passage may actually document the exact pair.
                explicit=any(mentioned[0] in source_line and len(features(label)&features(source_line))>=2
                             for other in rows for source_line in other['text'].splitlines())
                if explicit:continue
                excerpt='\n'.join(lines[max(0,start-1):end+1+len(values)])
                issues.append((row,excerpt));break
    return issues
