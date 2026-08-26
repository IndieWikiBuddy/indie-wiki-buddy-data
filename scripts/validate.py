#!/usr/bin/env python3
"""Check the files in data/. Print every problem and exit 1 if any."""

import bisect
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
FAVICON_DIR = os.path.join(ROOT, "favicons")

FILENAME_RE = re.compile(r"^sites([A-Z]+)\.json$")
# Lowercase host, optional port. Non-ASCII letters allowed.
HOST_RE = re.compile(r"^[a-z0-9\-\u00a1-\uffff]+(\.[a-z0-9\-\u00a1-\uffff]+)+(:\d+)?$")

# Characters that look empty but survive str.strip().
INVISIBLE_CHARS = "\u200b\u200c\u200d\u2060\ufeff"

# Allowed values. Extend when the data adds a new one.
KNOWN_PLATFORMS = {"mediawiki", "dokuwiki", "moinmoin"}
KNOWN_TAGS = {"official"}
KNOWN_HOSTS = {"wiki.gg", "Miraheze", "ShoutWiki", "Hooded Horse", "Paradox", "Telepedia"}
KNOWN_ORIGIN_FARMS = {"fandom.com", "neoseeker.com", "fextralife.com"}

errors = []
warnings = []


class Loc:
    """Where a problem is: the printed prefix plus the annotation target."""

    def __init__(self, label, path=None, line=1, title="validation", fields=None):
        self.label = label
        self.path = path
        self.line = line
        self.title = title
        self.fields = fields or {}  # field name -> [line, ...] within this entry

    def at(self, suffix, line=None, title=None, fields=None):
        return Loc(
            self.label + suffix,
            self.path,
            line or self.line,
            title or self.title,
            fields or self.fields,
        )

    def field(self, name):
        lines = self.fields.get(name)
        if not lines:
            return self
        return self.at("", line=next((l for l in lines if l >= self.line), lines[-1]))

    def __str__(self):
        return self.label


def err(where, msg, detail=None):
    """Record an error."""
    errors.append((where, msg, detail))


def warn(where, msg, detail=None):
    warnings.append((where, msg, detail))


def is_blank(value):
    return not value or all(c.isspace() or c in INVISIBLE_CHARS for c in value)


def check_known(value, known, const_name, field, where):
    if value not in known:
        err(
            where,
            f"unknown {field} "
            f"(should be one of: {', '.join(sorted(known))}; ",
            value,
        )


def check_base_url(value, field, where):
    if "://" in value:
        err(where, f"{field} must not include a scheme", value)
        return
    if value.endswith("/"):
        err(where, f"{field} must not end with '/'", value)
        return
    host, slash, path = value.partition("/")
    if not HOST_RE.match(host):
        err(where, f"{field} host must be a lowercase hostname with optional port", host)
    if slash and not re.match(r"^[^\s?#]+$", path):
        err(where, f"{field} path must not contain whitespace, '?', or '#'", f"/{path}")


def check_origin_base_url(value, field, where):
    check_base_url(value, field, where)
    host = value.partition("/")[0].partition(":")[0]
    if not any(host == farm or host.endswith("." + farm) for farm in KNOWN_ORIGIN_FARMS):
        err(
            where,
            f"{field} is not on a known wiki farm "
            f"(known: {', '.join(sorted(KNOWN_ORIGIN_FARMS))}; "
            f"extend KNOWN_ORIGIN_FARMS in scripts/validate.py if this is a new farm)",
            value,
        )


def check_path(value, field, where):
    if not value.startswith("/"):
        err(where, f"{field} must start with '/'", value)
    elif any(c.isspace() for c in value):
        err(where, f"{field} must not contain whitespace", value)
    elif ".." in value:
        err(where, f"{field} must not contain '..'", value)


def check_main_page(value, field, where):
    if "://" in value:
        err(where, f"{field} must be a page name, not a URL", value)
    elif value != value.strip():
        err(where, f"{field} has leading or trailing whitespace", value)
    elif value.startswith("/"):
        err(where, f"{field} must not start with '/'", value)


def check_platform(value, field, where):
    check_known(value, KNOWN_PLATFORMS, "KNOWN_PLATFORMS", field, where)


def check_host(value, field, where):
    check_known(value, KNOWN_HOSTS, "KNOWN_HOSTS", field, where)


def check_tags(value, field, where):
    for tag in value:
        if not isinstance(tag, str):
            err(where, "tags must be strings")
        else:
            check_known(tag, KNOWN_TAGS, "KNOWN_TAGS", field, where)


# field name -> (type, required, format checker)
SITE_FIELDS = {
    "id": (str, True, None),  # checked in validate_site
    "origins_label": (str, True, None),
    "origins": (list, True, None),  # checked in validate_site
    "destination": (str, True, None),
    "destination_base_url": (str, True, check_base_url),
    "destination_platform": (str, True, check_platform),
    "destination_icon": (str, True, None),  # checked in validate_site
    "destination_main_page": (str, True, check_main_page),
    "destination_search_path": (str, True, check_path),
    "destination_content_path": (str, True, check_path),
    "destination_host": (str, False, check_host),
    "destination_content_prefix": (str, False, None),
    "destination_content_suffix": (str, False, None),
    "tags": (list, False, check_tags),
}

ORIGIN_FIELDS = {
    "origin": (str, True, None),
    "origin_base_url": (str, True, check_origin_base_url),
    "origin_content_path": (str, True, check_path),
    "origin_main_page": (str, True, check_main_page),
    "destination_content_prefix": (str, False, None),
}


def check_fields(obj, schema, where):
    for name, (ftype, required, checker) in schema.items():
        if name not in obj:
            if required:
                err(where, f"missing required field '{name}'")
            continue
        fwhere = where.field(name)
        value = obj[name]
        if not isinstance(value, ftype):
            err(fwhere, f"field '{name}' must be {ftype.__name__}")
            continue
        if ftype is str and is_blank(value):
            err(fwhere, f"field '{name}' is empty")
            continue
        if checker:
            checker(value, name, fwhere)
    for name in obj:
        if name not in schema:
            err(where.field(name), "unknown field", name)
    present = [name for name in obj if name in schema]
    if present != sorted(present, key=list(schema).index):
        err(where, "fields out of canonical order; run 'python3 scripts/canonicalize.py'")


def misplaced(ids):
    """The ids to move to make the list sorted: everything outside a longest sorted run."""
    tails, tail_at, prev = [], [], [None] * len(ids)
    for i, value in enumerate(ids):
        k = bisect.bisect_right(tails, value)
        if k == len(tails):
            tails.append(value)
            tail_at.append(i)
        else:
            tails[k] = value
            tail_at[k] = i
        prev[i] = tail_at[k - 1] if k else None
    keep = set()
    i = tail_at[-1] if tail_at else None
    while i is not None:
        keep.add(i)
        i = prev[i]
    return [value for i, value in enumerate(ids) if i not in keep]


def data_files():
    """Return (lang, path) pairs and rejected filenames. Skips dotfiles."""
    matched, rejected = [], []
    for filename in sorted(os.listdir(DATA_DIR)):
        if filename.startswith("."):
            continue
        path = os.path.join(DATA_DIR, filename)
        m = FILENAME_RE.match(filename)
        if m and os.path.isfile(path):
            matched.append((m.group(1), path))
        else:
            rejected.append(filename)
    return matched, rejected


def reject_symlinks():
    """Error on any symlink under data/ or favicons/"""
    for top in (DATA_DIR, FAVICON_DIR):
        for dirpath, dirnames, filenames in os.walk(top):
            for name in dirnames + filenames:
                full = os.path.join(dirpath, name)
                if os.path.islink(full):
                    rel = os.path.relpath(full, ROOT)
                    err(Loc(rel, path=rel, title=os.path.basename(rel)), "symlinks are not allowed", os.readlink(full))


KEY_RE = re.compile(r'"([A-Za-z0-9_-]+)"\s*:(?:\s*"([^"]*)")?')


def scan_lines(text):
    id_line, entry_fields = {}, {}
    fields = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in KEY_RE.finditer(line):
            key, value = m.group(1), m.group(2)
            if key == "id" and value is not None and value not in id_line:
                id_line[value] = lineno
                fields = entry_fields.setdefault(value, {})
            fields.setdefault(key, []).append(lineno)
    return id_line, entry_fields


def validate_site(site, lang, id_re, where, seen_ids, seen_origins, favicon_names):
    check_fields(site, SITE_FIELDS, where)

    site_id = site.get("id")
    if isinstance(site_id, str):
        if not id_re.match(site_id):
            err(where, f"id must match {id_re.pattern}", site_id)
        if site_id in seen_ids:
            err(where, "duplicate id", f"{site_id} also in {seen_ids[site_id]}")
        else:
            seen_ids[site_id] = where

    icon = site.get("destination_icon")
    if isinstance(icon, str):
        iwhere = where.field("destination_icon")
        if "/" in icon or "\\" in icon:
            err(iwhere, "destination_icon must be a bare filename", icon)
        elif icon not in favicon_names:
            err(iwhere, f"destination_icon not found in favicons/{lang.lower()}/", icon)

    origins = site.get("origins")
    if isinstance(origins, list):
        if not origins:
            err(where.field("origins"), "origins must not be empty")
        origin_lines = where.fields.get("origin", [])
        for i, origin in enumerate(origins):
            oline = origin_lines[i] if i < len(origin_lines) else None
            owhere = where.at(f" origins[{i}]", line=oline, title=f"{where.title}, origin {i}")
            if not isinstance(origin, dict):
                err(owhere, "must be an object")
                continue
            check_fields(origin, ORIGIN_FIELDS, owhere)
            key = (origin.get("origin_base_url"), origin.get("origin_content_path"))
            if all(isinstance(k, str) for k in key):
                if key in seen_origins:
                    err(owhere, "duplicate origin", f"{key[0]}{key[1]} also in {seen_origins[key]}")
                else:
                    seen_origins[key] = owhere


def main():
    errors.clear()
    warnings.clear()

    if not os.path.isdir(DATA_DIR):
        print("error: data/ directory not found")
        return 1

    reject_symlinks()
    files, rejected = data_files()
    for filename in rejected:
        floc = Loc(f"data/{filename}", path=f"data/{filename}", title=filename)
        err(floc, "filename must match sites<LANG>.json (uppercase language code)", filename)
    if not files:
        err(None, "data/ contains no sites<LANG>.json files")

    seen_ids = {}
    seen_origins = {}
    used_icons = set()

    for lang, path in files:
        filename = os.path.basename(path)
        floc = Loc(f"data/{filename}", path=f"data/{filename}", title=filename)
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            sites = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            err(floc, "invalid JSON", e)
            continue

        if not isinstance(sites, list):
            err(floc, "top level must be a list")
            continue

        entry_line, entry_fields = scan_lines(text)

        favicon_lang_dir = os.path.join(FAVICON_DIR, lang.lower())
        if os.path.isdir(favicon_lang_dir):
            favicon_names = set(os.listdir(favicon_lang_dir))
        else:
            favicon_names = set()
            err(floc, f"favicons/{lang.lower()}/ directory not found")

        id_re = re.compile(rf"^{lang.lower()}-[a-z0-9_-]+$")
        ids_in_file = []
        for idx, site in enumerate(sites):
            where = floc.at(f"[{idx}]", title=f"entry {idx}")
            if not isinstance(site, dict):
                err(where, "must be an object")
                continue
            if isinstance(site.get("id"), str):
                site_id = site["id"]
                where = where.at(
                    f" '{site_id}'",
                    line=entry_line.get(site_id),
                    title=site_id,
                    fields=entry_fields.get(site_id),
                )
                ids_in_file.append(site_id)
            validate_site(site, lang, id_re, where, seen_ids, seen_origins, favicon_names)
            if isinstance(site.get("destination_icon"), str):
                used_icons.add((lang.lower(), site["destination_icon"]))

        for site_id in misplaced(ids_in_file):
            err(
                floc.at("", line=entry_line.get(site_id)),
                "entries must be sorted by id",
                f"{site_id} is out of order",
            )

    # Warn on unused favicons; do not fail.
    if os.path.isdir(FAVICON_DIR):
        for lang_dir in sorted(os.listdir(FAVICON_DIR)):
            lang_path = os.path.join(FAVICON_DIR, lang_dir)
            if lang_dir.startswith(".") or not os.path.isdir(lang_path):
                continue
            for icon in sorted(os.listdir(lang_path)):
                if icon.startswith(".") or not os.path.isfile(os.path.join(lang_path, icon)):
                    continue
                if (lang_dir, icon) not in used_icons:
                    path = f"favicons/{lang_dir}/{icon}"
                    warn(Loc(path, path=path, title="unused favicon"), "no site entry uses this favicon")

    report()
    return 1 if errors else 0


def report():
    for level, found in (("warning", warnings), ("error", errors)):
        for where, msg, detail in found:
            text = msg if detail is None else f"{msg}: {detail}"
            print(f"{level}: {where}: {text}" if where else f"{level}: {text}")
    if errors:
        print(f"\nValidation failed with {len(errors)} error(s).")
    else:
        print("Validation passed.")

    report_path = os.environ.get("VALIDATION_REPORT")
    if report_path:
        payload = {
            "errors": [
                {
                    "path": where.path if where else None,
                    "line": where.line if where else None,
                    "message": msg,
                }
                for where, msg, _ in errors
            ],
            "warnings": len(warnings),
        }
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)


if __name__ == "__main__":
    sys.exit(main())
