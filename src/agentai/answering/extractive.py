"""A source quote is preferable to a repeated, provably unsupported claim."""
def evidence_answer(issues, rows):
    permitted={'unsupported_absence','contradictory_absence','ambiguous_list_mapping','table_value_mismatch','citation_support','unverified_address'}
    relevant=[issue for issue in issues if issue['code'] in permitted]
    if not relevant:return ''
    sources={row['id']:row for row in rows}
    sections=[]; seen=set()
    for issue in relevant:
        for item in issue['evidence']:
            row=sources.get(item['id'])
            if not row or not row.get('ref') or row['id'] in seen:continue
            seen.add(row['id'])
            original=row['text'] if issue['code']=='table_value_mismatch' else item['text']
            text=original[:1800].strip()
            # Keep a clipped final line visibly separate from the source quote.
            if len(original)>=1800 and '\n' in text:text=text.rsplit('\n',1)[0]
            sections.append('\n'.join('> '+line for line in text.splitlines())+'\n\n['+row['ref']+']')
            if len(sections)>=3:break
        if len(sections)>=3:break
    if not sections:return ''
    lead='В материалах найдены следующие сведения. Ниже — исходные фрагменты:\n\n'
    if any(issue['code']=='ambiguous_list_mapping' for issue in relevant):
        lead+='Операции и таблицы перечислены группами; отдельное соответствие каждой операции не установлено.\n\n'
    return lead+'\n\n'.join(sections)
