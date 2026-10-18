"""Run with Python 3.11+; optionally put dependencies in .test-deps."""
import sys
import uuid
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'.test-deps'),str(root/'src')]
import pytest
temporary=root/'work'/('pytest-'+uuid.uuid4().hex)
temporary.parent.mkdir(parents=True,exist_ok=True)
raise SystemExit(pytest.main(['-q','--tb=short','--basetemp',str(temporary),str(root/'tests')]))
