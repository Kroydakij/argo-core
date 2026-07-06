"""
core.board — board (kanban) guidata da configurazione.

Una board mostra le entita' raggruppate per stato corrente: le colonne e a
quali stati corrispondono sono CONFIGURATE, non cablate. Le entita' arrivano
dalla proiezione di core.events (stato_corrente), quindi la board e' una lettura
dell'event-sourcing: nessuno stato duplicato, la colonna e' funzione dell'ultimo
evento.

Disaccoppiato di proposito: dispone/renderizza una lista di righe-stato gia'
lette (dict con almeno 'entita' e 'stato'), senza sapere da dove vengono.
Rendering come stringhe HTML (html.escape della stdlib): niente Flask, testabile.

Configurazione:

    [[board.colonne]]
    titolo = "Disponibili";   stati = ["LIBERA"]
    [[board.colonne]]
    titolo = "In uso";        stati = ["IN_USO"]
    [[board.colonne]]
    titolo = "Fuori servizio"; stati = ["GUASTA"]
    # opzionale: campi extra mostrati su ogni tile
    # campi_tile = ["operatore"]

    b = Board.da_config(cfg["board"])
    html_board = b.render_html(events.stato_corrente(con))
"""
from __future__ import annotations

import html


class Board:
    """Layout di board validato alla costruzione (fail-fast)."""

    def __init__(self, colonne: list[dict], campi_tile: list[str] | None = None):
        if not colonne:
            raise ValueError("la board non ha colonne")
        self.colonne: list[dict] = []
        visti: dict[str, str] = {}
        for c in colonne:
            titolo = str(c.get("titolo", "")).strip()
            stati = list(c.get("stati") or [])
            if not titolo:
                raise ValueError(f"colonna senza titolo: {c!r}")
            if not stati:
                raise ValueError(f"colonna {titolo!r} senza stati")
            for s in stati:
                if s in visti:
                    raise ValueError(
                        f"stato {s!r} assegnato a due colonne "
                        f"({visti[s]!r} e {titolo!r}): ambiguo")
                visti[s] = titolo
            self.colonne.append({"titolo": titolo, "stati": stati})
        self.campi_tile = list(campi_tile or [])

    @classmethod
    def da_config(cls, sezione: dict) -> "Board":
        """Costruisce dalla sezione [board] della config. Fail-fast."""
        try:
            return cls(sezione["colonne"], sezione.get("campi_tile"))
        except KeyError as e:
            raise ValueError(f"config board: chiave mancante {e}") from e

    def disponi(self, stati_correnti: list[dict]) -> list[dict]:
        """Distribuisce le righe-stato nelle colonne configurate.

        -> [{"titolo", "stati", "entita": [riga, ...]}, ...] nell'ordine delle
        colonne. Le righe con uno stato non mappato in nessuna colonna NON
        vengono mostrate (la board e' una vista configurata, non l'universo).
        """
        idx: dict[str, int] = {}
        for i, c in enumerate(self.colonne):
            for s in c["stati"]:
                idx[s] = i
        out = [{"titolo": c["titolo"], "stati": c["stati"], "entita": []}
               for c in self.colonne]
        for r in stati_correnti:
            i = idx.get(r.get("stato"))
            if i is not None:
                out[i]["entita"].append(dict(r))
        return out

    def render_html(self, stati_correnti: list[dict]) -> str:
        """HTML della board. Ogni valore e' escapato."""
        colonne = self.disponi(stati_correnti)
        parti = ['<div class="board">']
        for c in colonne:
            parti.append(
                f'<div class="colonna"><h3>{html.escape(c["titolo"])} '
                f'<span class="conteggio">{len(c["entita"])}</span></h3>')
            parti.extend(self._tile(e) for e in c["entita"])
            parti.append("</div>")
        parti.append("</div>")
        return "\n".join(parti)

    def _tile(self, e: dict) -> str:
        nome = html.escape(str(e.get("entita", "")))
        stato = html.escape(str(e.get("stato", "")), quote=True)
        extra = " · ".join(
            html.escape(str(e[k])) for k in self.campi_tile
            if e.get(k) not in (None, ""))
        meta = f'<div class="meta">{extra}</div>' if extra else ""
        return (f'<div class="tile" data-stato="{stato}">'
                f'<div class="nome">{nome}</div>{meta}</div>')
