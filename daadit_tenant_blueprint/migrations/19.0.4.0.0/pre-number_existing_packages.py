# -*- coding: utf-8 -*-
"""Geef bestaande momentopnamen een vestiging en een pakketnummer.

De opnamen die er al staan zijn precies de terugrolpunten die je nog wil
hebben; zonder nummer zijn ze dat niet. Nummeren gebeurt vóór het laden
van het model, omdat ``vestiging`` en ``package_number`` verplicht zijn en
een NULL de kolom anders niet NOT NULL kan worden \u2014 en de unieke
combinatie (vestiging, pakketnummer) anders niet te leggen is.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT column_name FROM information_schema.columns
         WHERE table_name = 'daadit_config_snapshot'
    """)
    columns = {row[0] for row in cr.fetchall()}
    if not columns:
        # De tabel bestaat nog niet (verse database, of het model werd in
        # dezelfde upgrade pas geladen). Zonder deze uitstap draait de
        # ALTER TABLE hieronder op een niet-bestaande relatie en breekt de
        # hele module-upgrade af; de ORM legt de kolommen daarna zelf aan.
        return
    if "vestiging" not in columns:
        cr.execute(
            "ALTER TABLE daadit_config_snapshot ADD COLUMN vestiging varchar"
        )
    if "package_number" not in columns:
        cr.execute(
            "ALTER TABLE daadit_config_snapshot "
            "ADD COLUMN package_number integer"
        )
    cr.execute(
        "UPDATE daadit_config_snapshot SET vestiging = %s "
        " WHERE vestiging IS NULL", (cr.dbname,),
    )
    # Oudste opname wordt pakket 1: de nummering loopt met de tijd mee,
    # anders zegt "terug naar pakket 12" iets anders dan een mens leest.
    cr.execute("""
        WITH numbered AS (
            SELECT id, row_number() OVER (
                       PARTITION BY vestiging ORDER BY create_date, id
                   ) AS nr
              FROM daadit_config_snapshot
        )
        UPDATE daadit_config_snapshot snapshot
           SET package_number = numbered.nr
          FROM numbered
         WHERE numbered.id = snapshot.id
           AND snapshot.package_number IS NULL
    """)
