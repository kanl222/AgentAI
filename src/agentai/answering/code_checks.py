"""Conservative syntax and source-contract checks; never execute model code."""
import ast
from copy import deepcopy
import re
from .complexity import inspect_complexity
from .test_boundaries import inspect_boundaries


def blank_alias_fields(rows):
    return {field.casefold() for field in re.findall(
        r'\b(\w+(?:\.\w+)?)\s+AS\s+[\'\"]\s*[\'\"]',source_text(rows),re.I)}


def unsupported_label(field,label,question):
    identity = lambda value: re.sub(r'[\W_]+','',value.casefold())
    return label.strip() and label.casefold() not in question.casefold() and identity(label)!=identity(field.rsplit('.',1)[-1])


def sql_tokens(block):
    pattern = r'--[^\n]*|/\*[\s\S]*?\*/|\'(?:\'\'|[^\'])*\'|"(?:""|[^"])*"|\b[A-Za-z_]\w*\b|[^\s]'
    return [token for token in re.finditer(pattern,block) if not token.group().startswith(('--','/*'))]


def sql_aliases(tokens):
    for index,token in enumerate(tokens):
        if token.group().casefold()!='as' or index<1 or index+1>=len(tokens): continue
        label = tokens[index+1].group()
        if len(label)<2 or label[0] not in "\'\"" or label[-1]!=label[0]: continue
        field = tokens[index-1].group()
        if index>=3 and tokens[index-2].group()=='.':
            field = tokens[index-3].group()+'.'+field
        yield field,label[1:-1],token.start(),tokens[index+1].end()


def normalize_sql_aliases(text,rows,question):
    """Drop an invented label only when no other reference depends on it.

    Tokenization keeps comments and SQL string contents opaque. Ambiguous alias
    references stay untouched and are rejected by validation instead.
    """
    fields = blank_alias_fields(rows)
    def normalize(match):
        block = match.group(2)
        tokens = sql_tokens(block)
        edits = []
        for field,label,start,end in sql_aliases(tokens):
            if (field.casefold() in fields and unsupported_label(field,label,question)
                    and sum(t.group().strip("\'\"[]").casefold()==label.casefold() for t in tokens)==1):
                edits.append((start,end))
        for start,end in reversed(edits): block = block[:start]+block[end:]
        return match.group(1)+block+match.group(3)
    return re.sub(r'(```sql\s*\n)([\s\S]*?)(```)',normalize,text,flags=re.I)


def normalize_sort_contract(text,rows,question):
    """Restore a known pure sort key only for an explicit stable-order request.

    Other changes remain proposals subject to validation. No code is executed.
    """
    if not (re.search(r'сохран',question,re.I) and re.search(r'стабиль|равн',question,re.I)):
        return text
    baseline = {node.name:node for module in python_modules(source_text(rows))
                for node in module.body if isinstance(node,ast.FunctionDef)}
    def sorted_calls(function):
        return [node for node in ast.walk(function) if isinstance(node,ast.Call)
                and isinstance(node.func,ast.Name) and node.func.id=='sorted']
    def key(call):
        return next((k.value for k in call.keywords if k.arg=='key'),None)
    def normalize(match):
        block=match.group(2)
        try: module=ast.parse(block)
        except (SyntaxError,ValueError,RecursionError): return match.group(0)
        changed=False
        for function in module.body:
            if not isinstance(function,ast.FunctionDef) or function.name not in baseline: continue
            old_calls=sorted_calls(baseline[function.name])
            if len(old_calls)!=1 or len(sorted_calls(function))!=1: continue
            old=old_calls[0]; old_key=key(old)
            if not isinstance(old_key,ast.Lambda) or isinstance(old_key.body,ast.Tuple): continue
            parameters={arg.arg for arg in old_key.args.args}
            if any(isinstance(n,ast.Name) and n.id not in parameters for n in ast.walk(old_key.body)): continue
            if any(isinstance(n,ast.Call) for n in ast.walk(old_key.body)): continue
            reverse=next((k.value for k in old.keywords if k.arg=='reverse'),ast.Constant(value=False))
            if not isinstance(reverse,ast.Constant) or not isinstance(reverse.value,bool): continue
            for call in sorted_calls(function):
                new_key=key(call)
                if not isinstance(new_key,ast.Lambda) or not isinstance(new_key.body,ast.Tuple): continue
                call.keywords=[k for k in call.keywords if k.arg not in {'key','reverse'}]
                call.keywords += [ast.keyword(arg='key',value=deepcopy(old_key)),ast.keyword(arg='reverse',value=deepcopy(reverse))]
                changed=True
        return match.group(1)+ast.unparse(module)+'\n'+match.group(3) if changed else match.group(0)
    return re.sub(r'(```(?:python|py)\s*\n)([\s\S]*?)(```)',normalize,text,flags=re.I)


def normalize_requested_format(text,question):
    if re.search(r'не\s+переписывай\s+код|только\s+анализ',question,re.I):
        text=re.sub(r'```[^\n]*\n[\s\S]*?```','',text)
        text=re.sub(r'(?m)^\s*```[^\n]*$', '',text)
    return text.strip()


def python_modules(text):
    for block in re.findall(r'```(?:python|py|text)\s*\n([\s\S]*?)```',text,re.I):
        block = re.sub(r'^\s*\d{1,5}: ?', '', block, flags=re.M)
        try:
            yield ast.parse(block)
        except (SyntaxError, ValueError, RecursionError):
            continue


def source_text(rows):
    groups = {}
    for row in rows:
        location = row['citation'].split('#')[0]
        match = re.search(r'#L(\d+)-L\d+',row['citation'])
        if not match:
            groups.setdefault(location,{}).update({len(groups.get(location,{})):row['text']})
            continue
        lines = groups.setdefault(location,{})
        for index,line in enumerate(row['text'].splitlines(),int(match.group(1))):
            if len(line) > len(lines.get(index,'')):
                lines[index] = line
    return '\n'.join('\n'.join(lines[n] for n in sorted(lines)) for lines in groups.values())


def checks(question, text, rows):
    issues = []
    original = source_text(rows)
    preserve = bool(re.search(r'сохран|улучш|оптимиз|рефактор',question,re.I))
    baseline = {node.name:node for module in python_modules(original)
                for node in module.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))}

    def keys(function):
        return [keyword.value for node in ast.walk(function) if isinstance(node,ast.Call)
                and isinstance(node.func,ast.Name) and node.func.id=='sorted'
                for keyword in node.keywords if keyword.arg=='key' and isinstance(keyword.value,ast.Lambda)]

    for module in python_modules(text):
        for node in module.body:
            if isinstance(node,ast.FunctionDef) and node.name.startswith('test_') and node.args.args and node.args.args[0].arg=='self':
                issues.append(('incomplete_test','Тест с self находится вне класса TestCase. Верни законченный тестовый модуль.'))
            if not preserve or not isinstance(node,ast.FunctionDef) or node.name not in baseline:
                continue
            before, after = keys(baseline[node.name]), keys(node)
            if (any(not isinstance(key.body,ast.Tuple) for key in before)
                    and any(isinstance(key.body,ast.Tuple) and len(key.body.elts)>1 for key in after)):
                issues.append(('sort_contract','В '+node.name+' добавлен второй ключ сортировки. Он меняет порядок равных значений; сохрани исходную стабильность сортировки.'))
    for block in re.findall(r'```(?:python|py)\s*\n([\s\S]*?)```',text,re.I):
        try:
            ast.parse(block)
        except (SyntaxError,ValueError,RecursionError):
            issues.append(('python_syntax','Предложенный Python содержит синтаксическую ошибку. Верни законченный метод и тесты.'))
    prose = re.sub(r'```[\s\S]*?```','',text)
    if ('сложност' in question.lower() and baseline and 'sorted(' in original
            and re.search(r'O\s*\(',prose) and not re.search(r'log|логарифм',prose,re.I)):
        issues.append(('sort_cost','Оценка сложности пропускает сортировку: учти число элементов вложенных коллекций и U log U для U итогов.'))
    nested = any(isinstance(node,ast.For) and any(
        isinstance(child,(ast.For,ast.comprehension)) for child in ast.walk(node) if child is not node)
        for function in baseline.values() for node in ast.walk(function))
    cost_prose = '\n'.join(line for line in prose.splitlines() if re.search(
        r'сложност|O\s*\(|позици|вложенн|внутренн|товар|\bI\b',line,re.I))
    if ('сложност' in question.lower() and nested and re.search(r'O\s*\(',prose)
            and not re.search(r'позици|items|вложенн|внутренн|товар|\bI\b',cost_prose,re.I)):
        issues.append(('nested_cost','Пропущен обход вложенных элементов. Учти I как их общее число, отдельно размеры исходных коллекций и U log U для сортировки итогов. Укажи полную оценку времени и памяти.'))
    if 'сложност' in question.lower() and nested:
        formulas=re.findall(r'O\s*\(([^)]*)\)',prose)
        if any('+' in formula and re.search(r'log',formula,re.I) and not re.search(r'\bI\b',formula,re.I)
               for formula in formulas) and re.search(r'\bI\b[^.\n]{0,60}общ',prose,re.I):
            issues.append(('nested_cost','Общее число вложенных элементов I упомянуто, но пропущено в итоговой формуле. Включи + I в итоговую оценку, а не только в отдельное пояснение.'))

    issues.extend(inspect_complexity(question,prose,nested))
    issues.extend(inspect_boundaries(question,list(python_modules(text)),original))

    # A blank source alias does not establish a column's human meaning. Keep
    # original identifiers instead of inventing labels from abbreviations.
    blank_fields = blank_alias_fields(rows)
    for block in re.findall(r'```sql\s*\n([\s\S]*?)```',text,re.I):
        for field,label,_,_ in sql_aliases(sql_tokens(block)):
            if field.casefold() in blank_fields and label.strip():
                # An explicit requested label is allowed; a source's empty alias
                # is otherwise not evidence for a new semantic description.
                if unsupported_label(field,label,question):
                    issues.append(('unsupported_sql_alias','Назначение '+field+' не раскрыто в источнике. Убери неподтверждённую подпись '+label+' и сохрани имя поля.'))
    return issues
