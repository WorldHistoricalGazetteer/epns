"""Source identifiers under https://w3id.org/whg-epns/ — one module, so the exporter, the site data and
the static files mint exactly the same IRI for the same source.

THE RULE (Stephen's decision, 27 Sep 2026, on the rule proposed from the corpus):

  national sources   https://w3id.org/whg-epns/source/<abbrev>
      A national archive series or a single printed work, which is the same source whichever county
      volume cites it: Domesday Book, the Patent Rolls, the Feet of Fines, Dugdale's Monasticon. One
      IRI across all 66 volumes, so the graph can say which places Domesday names, county-blind.

  class sources      https://w3id.org/whg-epns/source/<county>/<DEEP source id>
      Everything else, above all the abbreviations that name a KIND of record rather than a document:
      Ct (court rolls, of whichever manor the entry concerns), TA (a tithe award per parish), Deed,
      Rental, Terrier, EnclA, Map, PR, the county directories and the single-letter county sigla. "Ct"
      in Cheshire and "Ct" in Dorset are different documents, and one IRI would assert they are one.
      Keyed by DEEP's own per-county source id; where DEEP gave none, by the abbreviation.

  copy witnesses     <work IRI>/witness/<manuscript>-<copy-date>
      The copy a form is actually read in (ASC (A), c. 925), derived_from the work.

  county volumes     https://w3id.org/whg-epns/volume/<county>
  gazetteers         https://w3id.org/whg-epns/source/gazetteer/<name>
  the DEEP project   https://w3id.org/whg-epns/source/deep

Breadth alone cannot tell the two kinds apart: tithe awards are cited in 59 counties and court rolls
in 43, as widely as many national series. So NATIONAL is a curated list, drawn from the abbreviations
cited in 20 or more county volumes and kept only where the abbreviation names one work or one national
series. A county volume's own bibliography, which DEEP never digitised, is the authority; anything
not on this list is treated as county-specific, which errs towards more nodes rather than false merges.
"""
from __future__ import annotations

import re

W3ID = "https://w3id.org/whg-epns/"

# abbreviation -> expansion (None where the abbreviation is certainly national but its expansion is not
# given with confidence here; the county volume's list of abbreviations is the authority)
NATIONAL: dict[str, str | None] = {
    "DB": "Domesday Book (1086)", "Exon": "Exon Domesday, the Exeter Domesday (1086)",
    "ASC": "The Anglo-Saxon Chronicle", "BCS": "Birch, Cartularium Saxonicum (Anglo-Saxon charters)",
    "KCD": "Kemble, Codex Diplomaticus Aevi Saxonici (Anglo-Saxon charters)", "ASWills": "Anglo-Saxon Wills",
    "Bede": "Bede, Historia Ecclesiastica", "FF": "Feet of Fines (final concords of the royal courts)",
    "Ass": "Assize Rolls (pleas before the itinerant royal justices)", "Eyre": "Eyre Rolls", "Cur": "Curia Regis Rolls",
    "CurR": "Rotuli Curiae Regis", "P": "Pipe Rolls", "Pat": "Calendar of Patent Rolls", "PatR": "Rotuli Litterarum Patentium",
    "Cl": "Calendar of Close Rolls", "ClR": "Rotuli Litterarum Clausarum", "Ch": "Calendar of Charter Rolls",
    "ChR": "Rotuli Chartarum", "Fine": "Calendar of Fine Rolls", "FineR": "Excerpta e Rotulis Finium", "Orig": "Originalia Rolls",
    "OblR": "Rotuli de Oblatis et Finibus", "Lib": "Liberate Rolls", "Ipm": "Calendar of Inquisitions post mortem",
    "IpmR": "Inquisitions post mortem (Record Commission edition)", "Misc": "Calendar of Inquisitions Miscellaneous",
    "FA": "Feudal Aids (1284-1431)", "Fees": "The Book of Fees (Liber Feodorum)", "RH": "Rotuli Hundredorum (the Hundred Rolls)",
    "QW": "Placita de Quo Warranto", "Abbr": "Placitorum Abbreviatio", "RBE": "The Red Book of the Exchequer",
    "Tax": "Taxatio Ecclesiastica of Pope Nicholas IV (1291)", "NI": "Nonarum Inquisitiones (1340-1)",
    "VE": "Valor Ecclesiasticus (1535)", "SR": "Lay Subsidy Rolls", "LP": "Letters and Papers, Foreign and Domestic, of the Reign of Henry VIII",
    "AD": "Catalogue of Ancient Deeds", "BM": "British Museum charters and manuscripts (now British Library)",
    "AddCh": "Additional Charters, British Museum (now British Library)", "Add": "Additional Manuscripts, British Museum (now British Library)",
    "HarlCh": "Harleian Charters, British Museum (now British Library)", "Harl": "Harleian Manuscripts, British Museum (now British Library)",
    "CottCh": "Cotton Charters, British Museum (now British Library)", "Dugd": "Dugdale, Monasticon Anglicanum",
    "Pap": "Calendar of Papal Registers", "ECP": "Early Chancery Proceedings", "ChancP": None, "ChancR": None,
    "Banco": "De Banco Rolls (Court of Common Pleas)", "Recov": "Recovery Rolls (Court of Common Pleas)",
    "MinAcct": "Ministers' Accounts", "AOMB": "Augmentation Office Miscellaneous Books", "LRMB": "Land Revenue Miscellaneous Books",
    "ParlSurv": "Parliamentary Surveys", "For": "Forest proceedings (pleas of the forest)", "DuLa": "Duchy of Lancaster records",
    "Cor": "Coroners' Rolls", "CtRequests": "Court of Requests records", "Depositions": "Exchequer Depositions",
    "PCC": "Prerogative Court of Canterbury wills", "France": "Calendar of Documents preserved in France",
    "DKR": "Reports of the Deputy Keeper of the Public Records", "HMC": "Reports of the Historical Manuscripts Commission",
    "CartAnt": "Cartae Antiquae", "Inq aqd": "Inquisitions ad quod damnum", "Bracton": "Bracton's Note Book",
    "Leland": "Leland, Itinerary", "Camden": "Camden, Britannia", "Saxton": "Christopher Saxton's county maps (1570s)",
    "Speed": "John Speed's county maps (1610)", "Ogilby": "John Ogilby, Britannia (road maps, 1675)",
    "Morden": "Robert Morden's county maps", "Bowen": "Emanuel Bowen's county maps", "Cary": "John Cary's county maps",
    "OS": "Ordnance Survey maps", "O.S.": "Ordnance Survey maps", "Seld": "Selden Society publications",
}
# 'OS' and 'O.S.' are one survey
ALIAS = {"O.S.": "OS"}


def slug(s: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s or "").strip("-")


def is_national(text: str | None) -> bool:
    return bool(text) and text in NATIONAL


def national_iri(text: str) -> str:
    return f"{W3ID}source/{slug(ALIAS.get(text, text))}"


def class_iri(cc: str, sid: str | None, text: str | None) -> str:
    return f"{W3ID}source/{cc}/{slug(sid) if sid else 'x-' + slug(text)}"


def work_iri(cc: str, sid: str | None, text: str | None) -> str:
    return national_iri(text) if is_national(text) else class_iri(cc, sid, text)


def witness_iri(work: str, ms: str | None, ctext: str | None) -> str:
    """A '?' is kept, as 'q': '?14' (doubtfully 14th century) and '14' are different claims about the copy.
    Every other difference slug() drops is typographic ('c. 1000' / 'c.1000'), checked across the corpus."""
    return f"{work}/witness/{slug(ms) or 'copy'}-{slug((ctext or '').replace('?', ' q ')) or 'undated'}"


def copy_label(ctext: str) -> str:
    """The printed copy date with its abbreviation spacing made uniform ('c.1230' -> 'c. 1230'), so that
    one witness IRI carries one label; the MADS keeps the printed form."""
    return re.sub(r"\b([A-Za-z]+)\.\s*(?=\S)", r"\1. ", ctext)


def volume_iri(cc: str) -> str:
    return f"{W3ID}volume/{cc}"


GAZETTEER_IRI = {k: f"{W3ID}source/gazetteer/{k}" for k in ("geonames", "unlock", "kepn", "epns")}
DEEP_IRI = f"{W3ID}source/deep"


def national_description(text: str) -> dict:
    """County-independent, because one IRI is shared by every volume that cites it: no county, no
    DEEP source id, no roman/italic (volumes differ on that; each citation's MADS keeps it)."""
    exp = NATIONAL.get(text)
    return {"@id": national_iri(text), "title": ALIAS.get(text, text),
            "citation": (f"{exp}. " if exp else "") + f"A national source, abbreviated '{ALIAS.get(text, text)}' in the English Place-Name Society survey volumes",
            "authorityType": "source"}


def class_description(cc: str, county: str, sid: str | None, text: str | None, style: str | None) -> dict:
    conv = ("set in italics in the volume: the survey's convention for an unpublished manuscript source" if style == "italic"
            else "set in roman type in the volume: a printed edition or calendar")
    return {"@id": class_iri(cc, sid, text), "title": text or sid,
            "citation": f"Source abbreviation '{text}' in the {county} volume (DEEP source id {sid or 'none'}); {conv}",
            "authorityType": "source"}


def registry(con) -> dict:
    """(county, key) -> class-source description, where key is DEEP's source id or 'x-'+slug(text).
    Title and style are the MODAL ones for the key, so every citation of one IRI describes it
    identically (331 DEEP ids carry more than one text; the variants stay in the MADS)."""
    county = dict(con.execute("SELECT county_code, title FROM place WHERE type='county'").fetchall())
    out = {}
    for cc, sid, text, style in con.execute("SELECT county_code, source_id, source_text, source_style FROM source").fetchall():
        out[(cc, sid)] = class_description(cc, county.get(cc, cc), sid, text, style)
    for cc, text, style in con.execute("""
            SELECT p.county_code, a.source_text, arg_max(a.source_style, n) FROM (
              SELECT place_id, source_text, source_style, count(*) OVER (PARTITION BY source_text, source_style) n
              FROM attestation WHERE source_id IS NULL AND source_text IS NOT NULL) a
            JOIN place p USING (place_id) GROUP BY 1, 2""").fetchall():
        out[(cc, "x-" + slug(text))] = class_description(cc, county.get(cc, cc), None, text, style)
    return out
