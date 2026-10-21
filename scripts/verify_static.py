"""Dependency-free syntax, configuration and packaging checks."""
import ast
from pathlib import Path
root=Path(__file__).resolve().parents[1]
files=list((root/'src').rglob('*.py'))+list((root/'tests').rglob('*.py'))+list((root/'scripts').rglob('*.py'))
files+=list((root/'services').rglob('*.py'))
for path in files:
    ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
required=['Dockerfile','compose.yaml','.env.example','README.md','instructions/system.md','instructions/security.md','skills/code/SKILL.md','skills/documents/SKILL.md','skills/sql/SKILL.md']
required+=['services/ocr/Dockerfile','services/ocr/requirements.lock','services/ocr/server.py','services/ocr/worker.py']
assert all((root/name).is_file() for name in required)
print(f'Syntax OK: {len(files)} Python files; required artifacts OK')
