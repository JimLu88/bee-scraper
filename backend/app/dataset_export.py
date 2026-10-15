"""Pure result shaping shared by the scraper's existing API."""
import csv
import io
import json


def lookup(item: dict, path: str):
    value = item
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def transform(items: list[dict], fields: dict[str, str], contains: str = "",
              unique_by: list[str] | None = None, sort_by: str = "", descending: bool = False) -> list[dict]:
    rows, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            continue
        row = {name: lookup(item, path) for name, path in fields.items()} if fields else dict(item)
        if contains and contains.casefold() not in json.dumps(row, ensure_ascii=False).casefold():
            continue
        if unique_by:
            key = json.dumps([row.get(name) for name in unique_by], sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
        rows.append(row)
    if sort_by:
        def key(row):
            value = row.get(sort_by)
            return (value is None, 0 if isinstance(value, (int, float)) else 1,
                    value if isinstance(value, (int, float)) else str(value).casefold())
        rows.sort(key=key, reverse=descending)
    return rows


def to_csv(rows: list[dict], fields: list[str] | None = None) -> str:
    names = fields or list(dict.fromkeys(key for row in rows for key in row))
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=names, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                         for key, value in row.items()})
    return "\ufeff" + output.getvalue()
