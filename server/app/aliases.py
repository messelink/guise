import re
import secrets
import subprocess
from dataclasses import dataclass
from typing import Literal


RANDOM_LEN = 8
RANDOM_RE = re.compile(r"^[a-f0-9]{8}$")
SLUG_RE = re.compile(r"^[a-z0-9_]+$")
_SLUG_REPLACE = re.compile(r"[^a-z0-9]+")
_SLUG_COLLAPSE = re.compile(r"_+")
EMAIL_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]*@[a-z0-9][a-z0-9.-]*$")


Kind = Literal["managed_labeled", "managed_unlabeled", "unmanaged"]


@dataclass(frozen=True)
class AliasRow:
    local_part: str
    target: str
    kind: Kind
    label: str = ""

    @property
    def address(self) -> str:
        return self.local_part  # domain attached by caller


def slugify(label: str) -> str:
    """Reduce a user-supplied label to [a-z0-9_]+.

    Lowercases, replaces any run of non-alphanumerics with a single underscore,
    collapses consecutive underscores, trims leading/trailing underscores.
    Returns empty string if nothing usable remains.
    """
    lowered = (label or "").lower()
    replaced = _SLUG_REPLACE.sub("_", lowered)
    collapsed = _SLUG_COLLAPSE.sub("_", replaced)
    return collapsed.strip("_")


def random_prefix() -> str:
    return secrets.token_hex(RANDOM_LEN // 2)


def make_local_part(tag: str, slug: str, random: str | None = None) -> str:
    """Build the local-part: <tag><random> or <tag><random>-<slug>."""
    rnd = random if random is not None else random_prefix()
    if slug:
        return f"{tag}{rnd}-{slug}"
    return f"{tag}{rnd}"


def classify(local_part: str, tag: str) -> tuple[Kind, str]:
    """Classify a local-part. Returns (kind, label)."""
    if not local_part.startswith(tag):
        return "unmanaged", ""
    rest = local_part[len(tag):]
    if RANDOM_RE.match(rest):
        return "managed_unlabeled", ""
    if "-" in rest:
        head, _, label = rest.partition("-")
        if RANDOM_RE.match(head) and label and SLUG_RE.match(label):
            return "managed_labeled", label
    return "unmanaged", ""


def parse_alias_list(stdout: str) -> list[tuple[str, str]]:
    """Parse `setup alias list` stdout into (alias, target) pairs.

    Each non-blank line is `* alias target`. Extra whitespace tolerated.
    Lines that don't match the shape are skipped silently.
    """
    rows: list[tuple[str, str]] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or not line.startswith("*"):
            continue
        parts = line[1:].split()
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def _run_setup(container: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", container, "setup", *args],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def list_aliases(container: str) -> list[tuple[str, str]]:
    result = _run_setup(container, "alias", "list")
    if result.returncode != 0:
        raise RuntimeError(f"setup alias list failed: {result.stderr.strip()}")
    return parse_alias_list(_strip_ansi(result.stdout))


def _check_email_shape(alias: str, target: str) -> None:
    """Reject anything that's not a well-formed email address shape.

    Defense-in-depth against argv-injection: even though we pass values as
    argv elements (no shell), a value starting with '-' could be interpreted
    as a flag by the downstream `setup` script. Constraining inputs to the
    [a-z0-9._+-]+@[a-z0-9.-]+ shape rules that out entirely.
    """
    if not EMAIL_RE.match(alias):
        raise ValueError(f"alias does not match expected shape: {alias!r}")
    if not EMAIL_RE.match(target):
        raise ValueError(f"target does not match expected shape: {target!r}")


def add_alias(container: str, alias: str, target: str) -> None:
    _check_email_shape(alias, target)
    result = _run_setup(container, "alias", "add", alias, target)
    if result.returncode != 0:
        raise RuntimeError(f"setup alias add failed: {result.stderr.strip() or result.stdout.strip()}")


def del_alias(container: str, alias: str, target: str) -> None:
    _check_email_shape(alias, target)
    result = _run_setup(container, "alias", "del", alias, target)
    if result.returncode != 0:
        raise RuntimeError(f"setup alias del failed: {result.stderr.strip() or result.stdout.strip()}")


def build_view(rows: list[tuple[str, str]], target_email: str, tag: str) -> dict[str, list[AliasRow]]:
    """Filter rows by target and bucket into managed/unmanaged sections."""
    managed: list[AliasRow] = []
    unmanaged: list[AliasRow] = []
    for alias, target in rows:
        if target != target_email:
            continue
        local, _, _ = alias.partition("@")
        kind, label = classify(local, tag)
        row = AliasRow(local_part=alias, target=target, kind=kind, label=label)
        if kind == "unmanaged":
            unmanaged.append(row)
        else:
            managed.append(row)
    return {"managed": managed, "unmanaged": unmanaged}
