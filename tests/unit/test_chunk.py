"""Chunker tests on synthetic units (fixture-based chunk counts live in test_parse_akn.py)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from lgx_core.models import CC_BY_4_0, ActFlag, DateWindow, SourceFormat, TextBasis, Unit, UnitType, sha256_hex
from lgx_core.textsize import estimate_tokens
from lgx_ingest.chunk import (
    MAX_TOKENS,
    chunk_id,
    chunk_units,
    is_table_annex,
    make_header,
    short_title,
    skip_reason,
    split_body,
)

V = "http://data.legilux.public.lu/eli/etat/leg/loi/2013/07/12/n1/consolide/20260416"
W = "http://data.legilux.public.lu/eli/etat/leg/loi/2013/07/12/n1"
WINDOW = DateWindow(lower=dt.date(2026, 4, 16), upper=dt.date(2027, 4, 16))


def unit(ordinal: int, text: str | None = "Texte.", **kw: Any) -> Unit:
    base: dict[str, Any] = {
        "eli_work": W,
        "eli_version": V,
        "ordinal": ordinal,
        "depth": 1,
        "unit_type": UnitType.ARTICLE,
        "anchor": f"art_{ordinal}",
        "num_raw": f"Art. {ordinal}.",
        "breadcrumb": "Chapitre 1er. – Dispositions générales",
        "text_plain": text,
        "doc_type": "LOI",
        "source_format": SourceFormat.XML,
        "source_url": "https://legilux.public.lu/filestore/eli/etat/leg/x.xml",
        "source_sha256": "0" * 64,
        "licence": CC_BY_4_0,
        "text_basis": TextBasis.CONSOLIDATED,
    }
    base.update(kw)
    return Unit(**base)


def test_one_chunk_per_article_with_breadcrumb_header() -> None:
    units = [unit(1, heading="Définitions", index_terms=["Fonds", "FIA"]), unit(2)]
    chunks = chunk_units(units, title_short="Loi AIFM", version_label="version du 16/04/2026", text_valid=WINDOW)
    assert [c.unit_ordinal for c in chunks] == [1, 2]
    c = chunks[0]
    assert c.header == (
        "Loi AIFM — version du 16/04/2026 > Chapitre 1er. – Dispositions générales > Art. 1. Définitions"
        " | Index : Fonds; FIA"
    )
    assert c.body == "Texte." and c.part_no == c.part_count == 1
    assert (c.valid_from, c.valid_to) == (dt.date(2026, 4, 16), dt.date(2027, 4, 16))
    assert c.content_sha256 == sha256_hex(c.header + c.body)
    assert c.text_basis is TextBasis.CONSOLIDATED and c.doc_type == "LOI" and c.searchable
    assert chunk_id(c) == "eli-etat-leg-loi-2013-07-12-n1-consolide-20260416:1:1"


def test_skips_stubs_preamble_hierarchy_modifying_and_table_annexes() -> None:
    table_md = "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
    units = [
        unit(1, unit_type=UnitType.PREAMBLE, anchor="preamble", num_raw=None),
        unit(2, unit_type=UnitType.CHAPTER, anchor="chapitre_1", text=None),
        unit(3, is_repealed=True, text="Abrogé ( L. 1er août 2018 )"),
        unit(4, is_modifying=True, contains_quoted_amendment=True),
        unit(5, unit_type=UnitType.ANNEX, anchor="attachment_1", text="a | b\n1 | 2\n3 | 4", text_md=table_md),
        unit(6, unit_type=UnitType.ANNEX, anchor="attachment_2", text="Parcelles 123/4567, 124/4568, 125/4569"),
        unit(7, text=None),
        unit(8, unit_type=UnitType.ANNEX, anchor="attachment_3", text="Formulaire type : nom, prénom, adresse."),
        unit(9, unit_type=UnitType.PARAGRAPH, anchor="body.p1", num_raw=None, text="Texte sans articles."),
    ]
    reasons = [skip_reason(u) for u in units]
    assert reasons == [
        "preamble",
        "chapter",
        "repealed",
        "modifying",
        "table_annex",
        "table_annex",
        "empty",
        None,
        None,
    ]
    assert is_table_annex(units[4]) and not is_table_annex(units[7])
    chunks = chunk_units(units, title_short="T", version_label="V", text_valid=WINDOW)
    assert [c.unit_ordinal for c in chunks] == [8, 9]


def test_long_article_split_at_paragraphs_with_repeated_header() -> None:
    para = "Le gestionnaire veille à ce que les informations soient communiquées. " * 40  # ~2,800 chars
    text = "\n".join(f"({i}) {para.strip()}" for i in range(1, 8))
    u = unit(5, text=text)
    assert estimate_tokens(text) > MAX_TOKENS
    chunks = chunk_units([u], title_short="T", version_label="V", text_valid=WINDOW)
    assert len(chunks) > 1
    assert {c.header for c in chunks} == {chunks[0].header}
    assert [c.part_no for c in chunks] == list(range(1, len(chunks) + 1))
    assert all(c.part_count == len(chunks) for c in chunks)
    assert all((c.n_tokens or 0) <= MAX_TOKENS for c in chunks)
    assert all(c.body.startswith("(") for c in chunks)  # every part starts at a paragraph boundary
    assert "\n".join(c.body for c in chunks) == text  # nothing lost or duplicated
    assert len({c.content_sha256 for c in chunks}) == len(chunks)


def test_oversize_single_paragraph_is_split_by_sentences_then_hard() -> None:
    sentence = "Le ministre arrête les modalités. "
    parts = split_body(sentence * 400, "header", 300)
    assert len(parts) > 1 and all(estimate_tokens("header\n" + p) <= 300 for p in parts)
    hard = split_body("x" * 5000, "h", 300)
    assert "".join(hard) == "x" * 5000


def test_short_articles_are_never_merged_and_output_is_deterministic() -> None:
    units = [unit(i, text=f"Court {i}.") for i in range(1, 30)]
    a = chunk_units(units, title_short="T", version_label="V", text_valid=WINDOW)
    b = chunk_units(units, title_short="T", version_label="V", text_valid=WINDOW)
    assert len(a) == 29 and a == b


def test_searchable_and_rank_prior_from_flags() -> None:
    sup = unit(1, flags=[ActFlag.SUPERSEDED_BY_CODE])
    amending = unit(2, flags=[ActFlag.IS_AMENDING_ACT])
    chunks = chunk_units([sup, amending], title_short="T", version_label="V", text_valid=WINDOW)
    assert [c.searchable for c in chunks] == [False, True]
    assert [c.rank_prior for c in chunks] == [1.0, 0.8]


def test_markdown_body_for_tables_and_open_window() -> None:
    u = unit(1, text="Tarif | 10 EUR", text_md="| Tarif | 10 EUR |\n|---|---|")
    (c,) = chunk_units([u], title_short="T", version_label="V")
    assert c.body.startswith("| Tarif") and c.valid_from == dt.date(1, 1, 1) and c.valid_to is None


def test_header_without_breadcrumb_and_short_title() -> None:
    u = unit(1, breadcrumb=None, num_raw="ANNEXE I", unit_type=UnitType.ANNEX)
    assert make_header(u, title_short="Loi", version_label="texte original") == "Loi — texte original > ANNEXE I"
    assert short_title(None, "loi/2013/07/12/n1") == "loi/2013/07/12/n1"
