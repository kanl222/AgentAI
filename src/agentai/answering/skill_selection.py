"""Choose the initial skill independently of the model's tool decisions."""
import re


def select_skill(question, rows):
    if re.search(r'\bsql\b|\bjoin\b|\bselect\b|\bgroup\s+by\b|'
                 r'схем\w*\s+(?:бд|базы|таблиц)|структур\w*\s+(?:бд|базы|таблиц)',
                 question,re.I):
        return 'sql'
    evidence='\n'.join(row['text'][:1000] for row in rows)
    sql_evidence=bool(re.search(r'\bSELECT\b[\s\S]{0,1000}\bFROM\b',evidence,re.I))
    if re.search(r'запрос',question,re.I) and sql_evidence:
        return 'sql'
    if re.search(r'код|функци|метод|оптимиз|рефактор',question,re.I):
        return 'code'
    if re.search(r'\bdef\s+\w+\s*\(',evidence):
        return 'code'
    if sql_evidence:
        return 'sql'
    return 'documents'
