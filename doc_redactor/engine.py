"""Rule loading and text matching. File-format handlers live in formats.py."""
import re
from dataclasses import dataclass, field

import yaml

_MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"

BUILTIN_PATTERNS = {
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "email": r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b",
    "phone": r"(?<![\w-])(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![\w-])",
    "date": (
        r"\b\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})\b"
        r"|\b\d{4}-\d{2}-\d{2}\b"
        rf"|\b{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b"
        rf"|\b\d{{1,2}}[\s-]{_MONTH}[\s-]\d{{2,4}}\b"
    ),
    "credit_card": r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{1,4}\b",
    "ip_address": r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
    "zip_code": r"\b\d{5}(?:-\d{4})?\b",
    "url": r"\bhttps?://[^\s<>\"']+",
}

RULE_TYPES = ("builtin", "regex", "keywords", "label")


class RulesError(ValueError):
    pass


@dataclass
class Rule:
    name: str
    regex: re.Pattern
    group: int = 0
    replacement: str | None = None


@dataclass
class Match:
    start: int
    end: int
    rule: str
    text: str


@dataclass
class RuleSet:
    rules: list[Rule]
    replacement: str = "[REDACTED]"
    mask_char: str = ""
    pdf_label: bool = True
    scrub_metadata: bool = True
    source: str = field(default="", repr=False)

    def replacement_for(self, match: Match) -> str:
        if self.mask_char:
            return "".join(c if c.isspace() else self.mask_char for c in match.text)
        rule = next(r for r in self.rules if r.name == match.rule)
        template = rule.replacement if rule.replacement is not None else self.replacement
        return template.replace("{rule}", rule.name)


def _compile(spec: dict, idx: int) -> Rule | None:
    if not isinstance(spec, dict):
        raise RulesError(f"rule #{idx + 1} must be a mapping, got {type(spec).__name__}")
    if spec.get("enabled", True) is False:
        return None
    rtype = spec.get("type", "regex")
    name = str(spec.get("name") or f"rule_{idx + 1}")
    flags = 0 if spec.get("case_sensitive", False) else re.IGNORECASE
    group = int(spec.get("group", 0))

    if rtype == "builtin":
        key = spec.get("pattern_name") or name
        if key not in BUILTIN_PATTERNS:
            raise RulesError(f"rule '{name}': unknown builtin '{key}'. Choose from: {', '.join(BUILTIN_PATTERNS)}")
        pattern = BUILTIN_PATTERNS[key]
        if key == "date":
            flags = re.IGNORECASE
    elif rtype == "regex":
        pattern = spec.get("pattern")
        if not pattern:
            raise RulesError(f"rule '{name}': regex rules need a 'pattern'")
    elif rtype == "keywords":
        values = [str(v) for v in (spec.get("values") or []) if str(v).strip()]
        if not values:
            raise RulesError(f"rule '{name}': keywords rules need a non-empty 'values' list")
        alts = "|".join(r"\s+".join(map(re.escape, v.split())) for v in sorted(values, key=len, reverse=True))
        pattern = rf"(?<!\w)(?:{alts})(?!\w)" if spec.get("whole_word", True) else f"(?:{alts})"
    elif rtype == "label":
        labels = [str(v) for v in (spec.get("labels") or []) if str(v).strip()]
        if not labels:
            raise RulesError(f"rule '{name}': label rules need a non-empty 'labels' list")
        alts = "|".join(r"\s+".join(map(re.escape, v.split())) for v in sorted(labels, key=len, reverse=True))
        # value after "Label:" up to end of line, a tab / 2+ spaces (next column), ";", the next label,
        # or a sentence-ending ". " (a period after 1-2 letter tokens like "Q." or "Dr." does not end it)
        stop = (
            rf"\t| {{2,}}|;|\.?[ \t]*$|\s+(?:{alts})[ \t]*[:#]"
            r"|(?<=[A-Za-z0-9]{3})(?<!Mrs)(?<!Mr)(?<!Ms)\.(?:\s|$)"
        )
        pattern = rf"(?<!\w)(?:{alts})[ \t]*[:#][ \t]*([^\n\t;]*?[^\s.;])(?={stop})"
        group = 1
        flags |= re.MULTILINE
    else:
        raise RulesError(f"rule '{name}': unknown type '{rtype}'. Choose from: {', '.join(RULE_TYPES)}")

    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        raise RulesError(f"rule '{name}': invalid regex: {exc}") from exc
    if group > regex.groups:
        raise RulesError(f"rule '{name}': group {group} does not exist in the pattern")
    replacement = spec.get("replacement")
    return Rule(name=name, regex=regex, group=group, replacement=None if replacement is None else str(replacement))


def load_rules(text: str) -> RuleSet:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise RulesError(f"rules file is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RulesError("rules file must have 'settings' and 'rules' sections")
    settings = data.get("settings") or {}
    specs = data.get("rules") or []
    if not isinstance(specs, list):
        raise RulesError("'rules' must be a list")
    rules = [r for i, s in enumerate(specs) if (r := _compile(s, i)) is not None]
    names = [r.name for r in rules]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise RulesError(f"duplicate rule names: {', '.join(sorted(dupes))}")
    return RuleSet(
        rules=rules,
        replacement=str(settings.get("replacement", "[REDACTED]")),
        mask_char=str(settings.get("mask_char") or "")[:1],
        pdf_label=bool(settings.get("pdf_label", True)),
        scrub_metadata=bool(settings.get("scrub_metadata", True)),
        source=text,
    )


def load_rules_file(path) -> RuleSet:
    with open(path, encoding="utf-8") as fh:
        return load_rules(fh.read())


def find_matches(text: str, ruleset: RuleSet) -> list[Match]:
    """All rule hits in text, overlaps merged (earlier, then longer, match wins)."""
    hits = []
    for rule in ruleset.rules:
        for m in rule.regex.finditer(text):
            s, e = m.span(rule.group)
            if s < 0 or e <= s:
                continue
            hits.append(Match(s, e, rule.name, text[s:e]))
    hits.sort(key=lambda h: (h.start, -(h.end - h.start)))
    merged: list[Match] = []
    for h in hits:
        if merged and h.start < merged[-1].end:
            last = merged[-1]
            if h.end > last.end:
                merged[-1] = Match(last.start, h.end, last.rule, text[last.start:h.end])
            continue
        merged.append(h)
    return merged


def redact_text(text: str, ruleset: RuleSet) -> tuple[str, list[Match]]:
    matches = find_matches(text, ruleset)
    out, pos = [], 0
    for m in matches:
        out.append(text[pos:m.start])
        out.append(ruleset.replacement_for(m))
        pos = m.end
    out.append(text[pos:])
    return "".join(out), matches
