"""
core.forms — form-engine dichiarativo: un modulo, due funzioni.

Un form e' una lista di campi dichiarati (nome, tipo, vincoli). Da quella
descrizione il core sa fare due cose:

  valida(campi, dati)  -> (puliti, errori)   validazione lato server
  render_html(campi..) -> str                HTML dei controlli, pre-compilato

Cosi' la definizione del form vive in un posto solo e non si sfasa mai tra
"cosa mostro" e "cosa accetto": stesso elenco di campi per entrambi.

Render puro (stringhe HTML con html.escape della stdlib), quindi zero Flask e
testabile senza dipendenze. Il modulo inserisce l'output dentro il proprio
<form ...> ... <button> nel template.

Tipi supportati: text, textarea, number, date, select, checkbox.

Definizione:

    CAMPI = [
        {"nome": "targa",  "label": "Targa",  "tipo": "text",   "obbligatorio": True},
        {"nome": "ore",    "label": "Ore",    "tipo": "number", "min": 0},
        {"nome": "reparto","label": "Reparto","tipo": "select",
         "opzioni": ["A", "B"], "obbligatorio": True},
    ]
    puliti, errori = forms.valida(CAMPI, request.form)
    html_campi = forms.render_html(CAMPI, valori=puliti, errori=errori)
"""
from __future__ import annotations

import html
from datetime import datetime

TIPI = ("text", "textarea", "number", "date", "select", "checkbox")


def _valida_definizione(campi: list[dict]) -> None:
    """Fail-fast sulla DEFINIZIONE del form (errore del programmatore, non utente)."""
    if not campi:
        raise ValueError("il form non ha campi")
    visti: set[str] = set()
    for c in campi:
        nome = c.get("nome")
        if not nome:
            raise ValueError(f"campo senza 'nome': {c!r}")
        if nome in visti:
            raise ValueError(f"nome campo duplicato: {nome!r}")
        visti.add(nome)
        tipo = c.get("tipo", "text")
        if tipo not in TIPI:
            raise ValueError(f"tipo non supportato per {nome!r}: {tipo!r}")
        if tipo == "select" and not c.get("opzioni"):
            raise ValueError(f"il select {nome!r} non ha 'opzioni'")


def valida(campi: list[dict], dati) -> tuple[dict, dict]:
    """Valida i `dati` inviati contro la definizione `campi`.

    Ritorna (puliti, errori): `puliti` = valori normalizzati per i campi validi,
    `errori` = {nome: messaggio} per quelli non validi. Form valido <=> errori
    vuoto. `dati` puo' essere un dict o un oggetto con .get (es. request.form).
    """
    _valida_definizione(campi)
    puliti: dict = {}
    errori: dict = {}
    for c in campi:
        nome, tipo = c["nome"], c.get("tipo", "text")
        obblig = bool(c.get("obbligatorio", False))

        if tipo == "checkbox":
            puliti[nome] = _as_bool(dati.get(nome))
            continue

        raw = dati.get(nome)
        val = "" if raw is None else str(raw).strip()
        if not val:
            if obblig:
                errori[nome] = "campo obbligatorio"
            else:
                puliti[nome] = None
            continue

        if tipo == "number":
            try:
                num = float(val)
            except ValueError:
                errori[nome] = "deve essere un numero"
                continue
            if "min" in c and num < c["min"]:
                errori[nome] = f"minimo {c['min']}"
            elif "max" in c and num > c["max"]:
                errori[nome] = f"massimo {c['max']}"
            else:
                puliti[nome] = int(num) if float(num).is_integer() else num
        elif tipo == "select":
            if val not in [str(o) for o in c["opzioni"]]:
                errori[nome] = "opzione non valida"
            else:
                puliti[nome] = val
        elif tipo == "date":
            try:
                datetime.strptime(val, "%Y-%m-%d")
                puliti[nome] = val
            except ValueError:
                errori[nome] = "data non valida (YYYY-MM-DD)"
        else:  # text, textarea
            maxlen = c.get("maxlen")
            if maxlen and len(val) > maxlen:
                errori[nome] = f"massimo {maxlen} caratteri"
            else:
                puliti[nome] = val
    return puliti, errori


def render_html(campi: list[dict], valori: dict | None = None,
                errori: dict | None = None) -> str:
    """HTML dei controlli del form, pre-compilato con `valori` ed `errori`.

    Non emette il tag <form> ne' il bottone: il modulo li mette nel template,
    cosi' controlla action/method e lo stile. Ogni valore/attributo e' escapato.
    """
    _valida_definizione(campi)
    valori = valori or {}
    errori = errori or {}
    out: list[str] = []
    for c in campi:
        nome, tipo = c["nome"], c.get("tipo", "text")
        label = html.escape(str(c.get("label", nome)))
        req = " required" if c.get("obbligatorio") else ""
        v = valori.get(nome)
        controllo = _controllo(nome, tipo, c, v, req)
        err = errori.get(nome)
        blocco_err = (f'<span class="errore">{html.escape(str(err))}</span>'
                      if err else "")
        out.append(
            f'<div class="campo">'
            f'<label for="{_attr(nome)}">{label}</label>'
            f'{controllo}{blocco_err}</div>')
    return "\n".join(out)


def _controllo(nome: str, tipo: str, c: dict, v, req: str) -> str:
    n = _attr(nome)
    if tipo == "textarea":
        return f'<textarea id="{n}" name="{n}"{req}>{html.escape("" if v is None else str(v))}</textarea>'
    if tipo == "checkbox":
        checked = " checked" if _as_bool(v) else ""
        return f'<input type="checkbox" id="{n}" name="{n}"{checked}>'
    if tipo == "select":
        opts = []
        for o in c["opzioni"]:
            o_s = str(o)
            sel = " selected" if v is not None and str(v) == o_s else ""
            opts.append(f'<option value="{_attr(o_s)}"{sel}>{html.escape(o_s)}</option>')
        return f'<select id="{n}" name="{n}"{req}>{"".join(opts)}</select>'
    # input text / number / date
    ttype = {"text": "text", "number": "number", "date": "date"}[tipo]
    extra = ""
    if tipo == "number":
        if "min" in c:
            extra += f' min="{_attr(str(c["min"]))}"'
        if "max" in c:
            extra += f' max="{_attr(str(c["max"]))}"'
    val = "" if v is None else _attr(str(v))
    return f'<input type="{ttype}" id="{n}" name="{n}" value="{val}"{extra}{req}>'


def _attr(s: str) -> str:
    """Escape per un valore che finisce dentro un attributo con doppi apici."""
    return html.escape(str(s), quote=True)


def _as_bool(v) -> bool:
    """Interpreta un valore-checkbox eterogeneo (form HTML, JSON, None)."""
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "on", "si", "yes")
