"""
core.export — export CSV apribile con doppio click in Excel (locale italiano).

Le due scelte che contano, prese una volta per tutti i moduli:
  - delimitatore ";" (l'Excel in locale italiano ignora la ",");
  - encoding utf-8 con BOM (utf-8-sig), altrimenti Excel storpia gli accenti.
"""
from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Mapping


def csv_bytes(rows: Iterable, headers: list[str] | None = None,
              *, delimiter: str = ";", bom: bool = True) -> bytes:
    """Serializza righe in CSV. Accetta sqlite3.Row, dict o sequenze.

    headers: se None, viene dedotto dalla prima riga (keys() per Row/dict);
    per sequenze pure va passato esplicitamente.
    """
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter)
    it = iter(rows)
    first = next(it, None)
    if first is not None:
        if headers is None:
            headers = list(first.keys()) if hasattr(first, "keys") else None
        if headers:
            w.writerow(headers)
        for r in ([first], it):
            for row in r:
                if isinstance(row, Mapping):
                    w.writerow([row.get(h) for h in headers or row.keys()])
                else:
                    w.writerow(list(row))
    data = buf.getvalue().encode("utf-8-sig" if bom else "utf-8")
    return data


def csv_response(rows: Iterable, filename: str, headers: list[str] | None = None,
                 *, delimiter: str = ";", bom: bool = True):
    """flask.Response pronta al download. Import di Flask lazy: il resto di
    core resta usabile anche in script senza Flask (pipeline di import, test).
    """
    from flask import Response  # lazy: vedi docstring
    if not filename.lower().endswith(".csv"):
        filename += ".csv"
    out = Response(csv_bytes(rows, headers, delimiter=delimiter, bom=bom),
                   mimetype="text/csv")
    out.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return out
