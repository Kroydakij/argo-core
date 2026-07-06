"""
core.notify — notifiche email via SMTP interno (relay senza autenticazione).

Perche' SMTP: i webhook Teams/cloud sono spesso bloccati dalle policy
aziendali; un relay SMTP interno sulla porta 25 e' quasi sempre disponibile.

Config attesa (sezione "smtp" del config.json del modulo):

    {"smtp": {"enabled": true, "server": "smtp.azienda.it", "port": 25,
              "from": "modulo@azienda.it", "to": ["capo@azienda.it"]}}

Log: se il chiamante passa log_con (connessione al PROPRIO database),
ogni tentativo di invio viene tracciato in una tabella append-only
`notifiche_log` di quel database. Il log sta nel DB del modulo mittente,
non in un DB centrale: la regola di ownership resta intatta.
"""
from __future__ import annotations

import smtplib
import sqlite3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

LOG_DDL = """CREATE TABLE IF NOT EXISTS notifiche_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT DEFAULT (datetime('now','localtime')),
    oggetto TEXT NOT NULL,
    destinatari TEXT NOT NULL,
    esito TEXT NOT NULL,
    errore TEXT
)"""


def send_email(cfg: dict, subject: str, body_text: str, body_html: str | None = None,
               *, log_con: sqlite3.Connection | None = None) -> bool:
    """Invia una mail secondo la sezione smtp della config. Ritorna True/False.

    Non solleva mai: una notifica fallita non deve far fallire l'operazione
    che la origina. L'esito finisce nel log (se richiesto) e su stdout.
    """
    smtp_cfg = (cfg or {}).get("smtp", {})
    to_list = smtp_cfg.get("to", [])
    if not smtp_cfg.get("enabled", False):
        _log(log_con, subject, to_list, "DISABILITATO", None)
        return False
    server, port = smtp_cfg.get("server", ""), smtp_cfg.get("port", 25)
    from_ = smtp_cfg.get("from", "")
    if not server or not to_list:
        _log(log_con, subject, to_list, "CONFIG_INCOMPLETA", None)
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"], msg["From"], msg["To"] = subject, from_, ", ".join(to_list)
        msg.attach(MIMEText(body_text, "plain"))
        if body_html:
            msg.attach(MIMEText(body_html, "html"))
        with smtplib.SMTP(server, port, timeout=5) as s:
            s.sendmail(from_, to_list, msg.as_string())
        print(f"[MAIL] inviata: {subject}")
        _log(log_con, subject, to_list, "OK", None)
        return True
    except Exception as e:                       # noqa: BLE001 — mai propagare
        print(f"[MAIL] fallita: {subject} ({e})")
        _log(log_con, subject, to_list, "ERRORE", str(e))
        return False


def _log(con, oggetto: str, destinatari: list, esito: str, errore: str | None) -> None:
    if con is None:
        return
    try:
        con.execute(LOG_DDL)
        con.execute(
            "INSERT INTO notifiche_log (oggetto, destinatari, esito, errore) "
            "VALUES (?,?,?,?)",
            (oggetto, ", ".join(destinatari or []), esito, errore))
        con.commit()
    except Exception as e:                       # il log non deve mai rompere
        print(f"[MAIL] log fallito: {e}")
