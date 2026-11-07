"""Каталог модулей: чтение, валидация и свод по `modules/*/module.yaml` (M0.2).

Ошибки валидации всегда несут номер строки манифеста — «битый манифест → invalid_manifest
с номером строки» из роадмапа. Никаких «молча пропустим»: битый модуль не попадает в каталог,
но остальные читаются, чтобы `plan` показывал всё сразу, а не по одному.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import yamlmini

TYPES = {"skill", "plugin", "ui", "theme", "i18n", "mcp"}
KINDS = {"desktop", "python", "skill", "skin", "locale", "mcp_json", "patch", "state"}
KIND_BY_TYPE = {
    "skill": {"skill"},
    "theme": {"skin"},
    "i18n": {"locale"},
    "mcp": {"mcp_json"},
    # `ui` держит обе половины одного id (ADR-007): страницу (desktop) и бэкенд (python)
    "ui": {"desktop", "patch", "python"},
    "plugin": {"python", "desktop", "mcp_json"},
}
ALWAYS_OK_KINDS = {"state"}
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")


class ManifestError(Exception):
    """Один или несколько дефектов манифеста. `errors` — [(строка, сообщение)]."""

    def __init__(self, module_dir: Path, errors: list[tuple[int, str]]) -> None:
        self.module_dir = module_dir
        self.errors = errors
        head = "; ".join(f"строка {no}: {msg}" if no else msg for no, msg in errors[:4])
        super().__init__(f"invalid_manifest {module_dir.name}: {head}")


@dataclass
class Artifact:
    kind: str
    src: str
    dst: str
    apply: Optional[str] = None
    optional: bool = False


@dataclass
class Module:
    id: str
    type: str
    version: str
    description: str = ""
    author: str = ""
    license: str = ""
    platforms: list[str] = field(default_factory=list)
    requires: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    hooks: list[str] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    path: Optional[Path] = None

    @property
    def dir_name(self) -> str:
        return self.path.name if self.path else self.id

    def as_db_row(self) -> dict[str, Any]:
        import json

        return {
            "id": self.id,
            "type": self.type,
            "version": self.version,
            "description": self.description,
            "repo_path": str(self.path) if self.path else "",
            "capabilities": json.dumps(self.capabilities, ensure_ascii=False),
        }


def _str_list(value: Any, path: tuple, marks, errors: list[tuple[int, str]], label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append((marks.get(path, 0), f"{label}: ожидался список"))
        return []
    out: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append((marks.get(path + (idx,), 0), f"{label}[{idx}]: ожидалась непустая строка"))
            continue
        out.append(item.strip())
    return out


def _dep_list(value: Any, path: tuple, marks, errors: list[tuple[int, str]], label: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append((marks.get(path, 0), f"{label}: ожидался список id или словарей"))
        return []
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(value):
        pos = marks.get(path + (idx,), 0)
        if isinstance(item, str) and item.strip():
            out.append({"id": item.strip()})
        elif isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
            out.append({"id": item["id"].strip(), "min_version": item.get("min_version")})
        else:
            errors.append((pos, f"{label}[{idx}]: ожидался id или {{id, min_version}}"))
    return out


def _rel_path_ok(value: str) -> Optional[str]:
    """Возвращает текст ошибки или None. Абсолютные пути и `..` запрещены.

    Префикс `@repo/` — явная ссылка на общий источник в корне репозитория (например,
    `@repo/plugins/ui-dispatcher`): модуль-обёртка не держит вторую копию кода.
    """
    if not value or not value.strip():
        return "пустой путь"
    text = value.strip().replace("\\", "/")
    if text.startswith("@repo/"):
        text = text[len("@repo/"):]
        if not text:
            return "@repo/ без пути"
    if re.match(r"^[A-Za-z]:", text) or text.startswith("/"):
        return "абсолютный путь; dst/src задаются относительно своей базы"
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if ".." in parts:
        return "выход за пределы каталога (`..`)"
    return None


def resolve_src(module_dir: Path, src: str) -> Path:
    """`src` манифеста → абсолютный путь. `@repo/...` считается от корня репозитория."""
    text = src.strip().replace("\\", "/")
    if text.startswith("@repo/"):
        return (module_dir.parents[1] / text[len("@repo/"):]).resolve()
    return (module_dir / text).resolve()


def load_manifest(path: Path, *, validate_files: bool = True) -> Module:
    """Читает и валидирует один `module.yaml`. Бросает ManifestError со списком дефектов."""
    errors: list[tuple[int, str]] = []
    try:
        raw, marks = yamlmini.load(path)
    except yamlmini.YAMLError as exc:
        raise ManifestError(path.parent, [(getattr(exc, "line", 0), str(exc))]) from exc
    if not isinstance(raw, dict):
        raise ManifestError(path.parent, [(0, "манифест должен быть словарём верхнего уровня")])

    def mark(*key_path: Any) -> int:
        return marks.get(key_path, 0)

    module_id = raw.get("id")
    if not isinstance(module_id, str) or not module_id.strip():
        errors.append((mark("id"), "id обязателен"))
        module_id = path.parent.name
    else:
        module_id = module_id.strip()
        if not ID_RE.match(module_id):
            errors.append((mark("id"), f"id {module_id!r} не kebab-case (a-z, 0-9, дефисы)"))
        if module_id != path.parent.name:
            errors.append((mark("id"), f"id {module_id!r} не совпадает с именем папки {path.parent.name!r}"))

    module_type = raw.get("type")
    if module_type not in TYPES:
        errors.append((mark("type"), f"type {module_type!r} не из {sorted(TYPES)}"))
        module_type = ""

    version = raw.get("version")
    if not isinstance(version, str) or not SEMVER_RE.match(str(version)):
        errors.append((mark("version"), f"version {version!r} не semver (X.Y.Z)"))

    artifacts_raw = raw.get("artifacts")
    artifacts: list[Artifact] = []
    if not isinstance(artifacts_raw, list) or not artifacts_raw:
        errors.append((mark("artifacts"), "artifacts обязателен и не может быть пустым"))
    else:
        for idx, item in enumerate(artifacts_raw):
            base = ("artifacts", idx)
            if not isinstance(item, dict):
                errors.append((mark(*base), "артефакт должен быть словарём {kind, src, dst}"))
                continue
            kind = item.get("kind")
            src = item.get("src")
            dst = item.get("dst")
            optional = bool(item.get("optional", False))
            bad = False
            if kind not in KINDS:
                errors.append((mark(*base, "kind"), f"kind {kind!r} не из {sorted(KINDS)}"))
                bad = True
            if not isinstance(src, str) or _rel_path_ok(src):
                errors.append((mark(*base, "src"), f"src: {_rel_path_ok(src) if isinstance(src, str) else 'ожидалась строка'}"))
                bad = True
            if not isinstance(dst, str) or _rel_path_ok(dst):
                errors.append((mark(*base, "dst"), f"dst: {_rel_path_ok(dst) if isinstance(dst, str) else 'ожидалась строка'}"))
                bad = True
            if bad:
                continue
            allowed = KIND_BY_TYPE.get(module_type, set()) | ALWAYS_OK_KINDS
            if allowed and kind not in allowed:
                errors.append((mark(*base, "kind"), f"kind {kind!r} не подходит для type {module_type!r} (можно {sorted(allowed)})"))
            kind_s = str(kind)
            src_s = str(src).strip()
            dst_s = str(dst).strip().replace("\\", "/")
            src_path = resolve_src(path.parent, src_s)
            if validate_files and not src_path.exists() and not optional:
                errors.append((mark(*base, "src"), f"src не найден: {src}"))
            artifacts.append(Artifact(kind=kind_s, src=src_s, dst=dst_s,
                                      apply=item.get("apply"), optional=optional))

    config_raw = raw.get("config") or {}
    config: dict[str, Any] = {}
    if not isinstance(config_raw, dict):
        errors.append((mark("config"), "config должен быть словарём ключ → значение"))
    else:
        for key, value in config_raw.items():
            if not isinstance(key, str) or not key.strip():
                errors.append((mark("config"), "config: пустой ключ"))
                continue
            if not isinstance(value, (str, int, float, bool, list, dict)) and value is not None:
                errors.append((mark("config", key), f"config.{key}: недопустимый тип значения"))
                continue
            config[key.strip()] = value

    capabilities = _str_list(raw.get("capabilities"), ("capabilities",), marks, errors, "capabilities")
    requires = _dep_list(raw.get("requires"), ("requires",), marks, errors, "requires")
    conflicts = _dep_list(raw.get("conflicts"), ("conflicts",), marks, errors, "conflicts")
    hooks = _str_list(raw.get("hooks"), ("hooks",), marks, errors, "hooks")

    settings_raw = raw.get("settings") or {}
    if not isinstance(settings_raw, dict):
        errors.append((mark("settings"), "settings должен быть словарём"))
        settings_raw = {}

    if errors:
        raise ManifestError(path.parent, errors)

    return Module(
        id=module_id,
        type=module_type,
        version=str(version),
        description=str(raw.get("description") or ""),
        author=str(raw.get("author") or ""),
        license=str(raw.get("license") or ""),
        platforms=_str_list(raw.get("platforms"), ("platforms",), marks, errors, "platforms"),
        requires=requires,
        conflicts=conflicts,
        capabilities=capabilities,
        artifacts=artifacts,
        config=config,
        hooks=hooks,
        settings=dict(settings_raw),
        path=path.parent,
    )


@dataclass
class Catalog:
    modules: list[Module] = field(default_factory=list)
    errors: list[tuple[Path, ManifestError]] = field(default_factory=list)

    def by_id(self) -> dict[str, Module]:
        return {m.id: m for m in self.modules}

    def conflicts(self) -> list[tuple[int, str]]:
        """Перекрёстные проверки: дубли id, ссылки на отсутствующие requires, конфликты."""
        problems: list[tuple[int, str]] = []
        ids = [m.id for m in self.modules]
        seen: set[str] = set()
        for module_id in ids:
            if module_id in seen:
                problems.append((0, f"дубль id {module_id!r} в каталоге"))
            seen.add(module_id)
        known = set(ids)
        reported: set[tuple[str, str]] = set()
        for module in self.modules:
            for dep in module.requires:
                if dep["id"] not in known:
                    problems.append((0, f"{module.id}: requires {dep['id']!r} отсутствует в каталоге"))
            for dep in module.conflicts:
                other_id = str(dep["id"])
                pair = (module.id, other_id) if module.id <= other_id else (other_id, module.id)
                if other_id not in known or pair in reported:
                    continue
                reported.add(pair)
                problems.append((0, f"{module.id} конфликтует с {other_id!r}, оба в каталоге"))
        return problems


def load_catalog(modules_dir: Path, *, validate_files: bool = True) -> Catalog:
    catalog = Catalog()
    if not modules_dir.is_dir():
        catalog.errors.append((modules_dir, ManifestError(modules_dir, [(0, "каталог modules/ не найден")])))
        return catalog
    for path in sorted(modules_dir.glob("*/module.yaml")):
        try:
            catalog.modules.append(load_manifest(path, validate_files=validate_files))
        except ManifestError as exc:
            catalog.errors.append((path, exc))
    return catalog


def summarize(modules: Iterable[Module]) -> str:
    lines = [f"{'id':28} {'type':7} {'version':9} artifacts"]
    for module in sorted(modules, key=lambda m: m.id):
        lines.append(f"{module.id:28} {module.type:7} {module.version:9} {len(module.artifacts)}")
    return "\n".join(lines)
