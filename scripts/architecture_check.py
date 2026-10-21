"""Check dependency direction without importing models or connecting to storage."""
import ast
from importlib.util import resolve_name
from pathlib import Path

root = Path(__file__).resolve().parents[1]/'src/agentai'
failures = []
for module in ('access', 'answering', 'chat', 'ingestion', 'knowledge', 'execution', 'adapters'):
    for path in (root/module).glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            imports = ([node.module] if isinstance(node, ast.ImportFrom)
                       else [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            for name in imports:
                if name and (name.split('.')[0] in {'fastapi', 'starlette', 'transport'} or name.startswith('agentai.transport')
                             or name in {'agentai.api', 'agentai.core', 'agentai.agent', 'api', 'core', 'agent'}):
                    failures.append(f'{path.relative_to(root)}:{node.lineno}: {name}')
if failures:
    raise SystemExit('\n'.join(failures))

modules = {}
for path in root.rglob('*.py'):
    relative = path.relative_to(root).with_suffix('')
    parts = list(relative.parts)
    if parts[-1] == '__init__':
        parts.pop()
    modules['.'.join(['agentai', *parts])] = path
graph = {name: set() for name in modules}
for name, path in modules.items():
    package = name if path.name == '__init__.py' else name.rpartition('.')[0]
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        names = []
        if isinstance(node, ast.ImportFrom):
            base = resolve_name('.'*node.level+(node.module or ''), package) if node.level else node.module
            names = [base, *[base+'.'+alias.name for alias in node.names]] if base else []
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        graph[name].update(dependency for dependency in names if dependency in modules and dependency != name)

visiting, finished = set(), set()
def check(name, trail):
    if name in visiting:
        raise SystemExit('Import cycle: '+' -> '.join([*trail, name]))
    if name in finished:
        return
    visiting.add(name)
    for dependency in graph[name]:
        check(dependency, [*trail, name])
    visiting.remove(name)
    finished.add(name)

for name in graph:
    check(name, [])
print(f'Architecture OK: {len(modules)} modules, no import cycles or HTTP dependencies in application modules')
