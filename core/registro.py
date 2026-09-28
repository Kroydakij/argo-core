"""
core.registro — il registro dei moduli della suite (comune/core.sqlite).

Kernel, stdlib pura. Proprietario unico: la shell (core.shell), che lo
aggiorna scansionando i manifest (ADR-003). I moduli lo LEGGONO in sola
lettura per costruire il menu della cornice comune (ADR-001): menu_per()
restituisce solo le voci che l'utente ha il permesso di vedere.

Righe della tabella `moduli`:
  origine  'manifest' (scoperto dalla scansione) | 'manuale' (legacy, a mano)
  stato    'ok' | 'errore' (manifest/config non validi) | 'assente' (sparito)
  attivo   scelta dell'amministratore (disattivo = fuori dal menu)
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from . import manifest as coremanifest
from . import migrate, migrazioni

PRIMA_PORTA_MODULI = 4701

#: permesso che mostra nel menu i moduli legacy senza manifest (ADR-000).
PERMESSO_LINK_ESTERNI = "core.link_esterni"


# --- schema (core.migrazioni) ------------------------------------------------

def _p1_registro(con: sqlite3.Connection) -> None:
    migrate.ensure_table(con, """CREATE TABLE IF NOT EXISTS moduli (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL UNIQUE,
        porta INTEGER NOT NULL,
        descrizione TEXT NOT NULL DEFAULT '',
        attivo INTEGER NOT NULL DEFAULT 1,
        creato_il TEXT DEFAULT (datetime('now','localtime'))
    )""")


def _p2_manifest(con: sqlite3.Connection) -> None:
    """Colonne per i moduli scoperti dai manifest (ADR-003). Additivo."""
    for col, tipo in (("origine", "TEXT NOT NULL DEFAULT 'manuale'"),
                      ("versione", "TEXT"),
                      ("titolo", "TEXT"),
                      ("manifest_json", "TEXT"),
                      ("stato", "TEXT NOT NULL DEFAULT 'ok'"),
                      ("errore", "TEXT"),
                      ("scansionato_il", "TEXT")):
        migrate.ensure_column(con, "moduli", col, tipo)


PASSI_CORE = [
    migrazioni.Passo(1, "registro moduli", _p1_registro),
    migrazioni.Passo(2, "moduli dai manifest", _p2_manifest),
]


def migra(con: sqlite3.Connection) -> None:
    """Schema su una connessione gia' aperta, idempotente, SENZA backup ne'
    versione (test in memoria). La shell usa migrazioni.applica(PASSI_CORE)."""
    for p in PASSI_CORE:
        p.funzione(con)
    migrate.rebuild_views(con, {})
    con.commit()


# --- lettura -------------------------------------------------------------------

def lista_moduli(con) -> list[dict]:
    """Moduli del registro. Il manifest completo non esce: se ne espone il menu."""
    out = []
    for r in con.execute("SELECT * FROM moduli ORDER BY porta"):
        d = dict(r)
        grezzo = d.pop("manifest_json", None)
        d["menu"] = json.loads(grezzo)["menu"] if grezzo else []
        out.append(d)
    return out


def manifesti(con) -> list[dict]:
    """Manifest (dict) dei moduli validi: catalogo di permessi ed eventi."""
    return [json.loads(r[0]) for r in con.execute(
        "SELECT manifest_json FROM moduli WHERE origine='manifest' AND stato='ok' "
        "AND manifest_json IS NOT NULL ORDER BY nome")]


def menu_per(con, permessi, host: str) -> list[dict]:
    """Voci di menu visibili a chi ha `permessi`, con URL assoluto sullo
    stesso host (il cookie di sessione vale solo se l'host coincide).

    Una voce entra se il modulo e' ok e attivo e l'utente ha il permesso
    dichiarato per quella voce nel manifest. I moduli legacy senza manifest
    entrano come link alla radice se l'utente ha core.link_esterni."""
    permessi = set(permessi)
    voci = []
    for m in lista_moduli(con):
        if not m["attivo"] or m["stato"] != "ok":
            continue
        base = f"http://{host}:{m['porta']}"
        if m["origine"] == "manuale":
            if PERMESSO_LINK_ESTERNI in permessi:
                voci.append({"modulo": m["nome"], "titolo": m["nome"],
                             "url": base + "/", "esterno": True})
            continue
        for v in m["menu"]:
            if v["permesso"] in permessi:
                voci.append({"modulo": m["nome"], "titolo": v["titolo"],
                             "url": base + v["percorso"], "esterno": False})
    return voci


def porte(con) -> set[int]:
    return {r[0] for r in con.execute("SELECT porta FROM moduli WHERE porta > 0")}


def prossima_porta(con) -> int:
    r = con.execute("SELECT MAX(porta) FROM moduli WHERE porta >= ?",
                    (PRIMA_PORTA_MODULI,)).fetchone()[0]
    return (r + 1) if r else PRIMA_PORTA_MODULI


# --- scrittura (solo shell) ----------------------------------------------------

def registra_scansione(con, esiti: list[coremanifest.Scansione]) -> dict:
    """Scrive nel registro l'esito di manifest.scansiona(). Un modulo rotto
    resta visibile come 'errore' (fuori dal menu); un modulo da manifest non
    piu' trovato su disco diventa 'assente'."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conteggi = {"ok": 0, "errore": 0, "assente": 0}
    visti = set()
    for s in esiti:
        visti.add(s.nome)
        if s.manifest is not None:
            m = s.manifest
            con.execute(
                "INSERT INTO moduli (nome, porta, descrizione, origine, versione, "
                "titolo, manifest_json, stato, errore, scansionato_il) "
                "VALUES (?,?,?,'manifest',?,?,?,'ok',NULL,?) "
                "ON CONFLICT(nome) DO UPDATE SET porta=excluded.porta, "
                "descrizione=excluded.descrizione, origine='manifest', "
                "versione=excluded.versione, titolo=excluded.titolo, "
                "manifest_json=excluded.manifest_json, stato='ok', errore=NULL, "
                "scansionato_il=excluded.scansionato_il",
                (m.nome, s.porta, m.descrizione, m.versione, m.titolo,
                 json.dumps(m.to_dict(), ensure_ascii=False), ts))
            conteggi["ok"] += 1
        else:
            con.execute(
                "INSERT INTO moduli (nome, porta, origine, stato, errore, "
                "scansionato_il) VALUES (?,?,'manifest','errore',?,?) "
                "ON CONFLICT(nome) DO UPDATE SET origine='manifest', "
                "stato='errore', errore=excluded.errore, "
                "scansionato_il=excluded.scansionato_il, "
                "porta=CASE WHEN excluded.porta > 0 THEN excluded.porta "
                "ELSE moduli.porta END",
                (s.nome, s.porta or 0, s.errore, ts))
            conteggi["errore"] += 1
    for r in con.execute("SELECT nome FROM moduli WHERE origine='manifest' "
                         "AND stato <> 'assente'").fetchall():
        if r[0] not in visti:
            con.execute("UPDATE moduli SET stato='assente', scansionato_il=? "
                        "WHERE nome=?", (ts, r[0]))
            conteggi["assente"] += 1
    con.commit()
    return conteggi


def upsert_modulo(con, nome: str, porta: int, descrizione: str = "") -> None:
    """Registrazione manuale di un modulo LEGACY (senza manifest)."""
    con.execute(
        "INSERT INTO moduli (nome, porta, descrizione) VALUES (?,?,?) "
        "ON CONFLICT(nome) DO UPDATE SET porta=excluded.porta, "
        "descrizione=excluded.descrizione",
        (nome.strip(), int(porta), descrizione.strip()))
    con.commit()


def toggle_modulo(con, nome: str, attivo: bool) -> None:
    con.execute("UPDATE moduli SET attivo=? WHERE nome=?",
                (1 if attivo else 0, nome))
    con.commit()
