import ast
from importlib.metadata import requires
from pathlib import Path

from packaging.requirements import Requirement

REPOSITORY_ROOT = Path(__file__).parents[3]
PACKAGES_ROOT = REPOSITORY_ROOT / "packages"


def normalized_requirements(package: str) -> set[str]:
    return {Requirement(requirement).name.lower() for requirement in requires(package) or ()}


def imported_modules(source_root: Path) -> set[str]:
    modules: set[str] = set()
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
    return modules


def test_llm_package_does_not_depend_on_higher_layers() -> None:
    dependencies = normalized_requirements("code-agent-llm")
    imports = imported_modules(PACKAGES_ROOT / "code-agent-llm" / "src")

    assert "code-agent-core" not in dependencies
    assert "code-agent-cli" not in dependencies
    assert not any(
        module == "code_agent_core" or module.startswith("code_agent_core.") for module in imports
    )
    assert not any(module == "code_agent" or module.startswith("code_agent.") for module in imports)


def test_core_package_only_depends_downward() -> None:
    dependencies = normalized_requirements("code-agent-core")
    imports = imported_modules(PACKAGES_ROOT / "code-agent-core" / "src")

    assert "code-agent-llm" in dependencies
    assert "code-agent-cli" not in dependencies
    assert not any(module == "code_agent" or module.startswith("code_agent.") for module in imports)
