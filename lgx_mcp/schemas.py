"""Tool contracts (Appendix D.2): names, descriptions, input schemas (verbatim) and output schemas.

Input schemas have no top-level ``oneOf``/``anyOf`` (OpenAI-compatible tool parsers reject them); combinations
such as ``citation`` XOR (``act_eli`` + ``article``) are enforced by the server (``BAD_INPUT``). Output
schemas are JSON Schema 2020-12, written inline (no ``$ref``) so every client validator resolves them.
"""

from __future__ import annotations

import copy
from typing import Any

DOC_TYPES = [
    "Constitution", "CODE", "CODE_RECUEIL", "RECUEIL", "LOI", "RGD", "AGD", "RMIN", "AMIN", "RGC", "RILR", "RCSF",
    "RBCL", "ST", "ACC", "AGC", "DGC", "RI", "AGDR", "CONV", "PROT",
]  # fmt: skip
ELI_PATTERN = r"^(http://data\.legilux\.public\.lu/eli/|legilux://eli/)"

# ------------------------------------------------------------------------------------------- inputs
SEARCH_INPUT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query"],
    "properties": {
        "query": {
            "type": "string",
            "minLength": 2,
            "maxLength": 1000,
            "description": "Question or keywords in French legal language (reformulate DE/EN questions into French).",
        },
        "filters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "doc_types": {"type": "array", "uniqueItems": True, "items": {"enum": DOC_TYPES}},
                "act_eli": {"type": "string", "pattern": r"^http://data\.legilux\.public\.lu/eli/etat/leg/"},
                "in_force_only": {
                    "type": "boolean",
                    "default": True,
                    "description": "true: only text applicable on as_of. false: also stored future versions and"
                    " not-yet-in-force acts (with warnings).",
                },
            },
        },
        "as_of": {"type": "string", "format": "date"},
        "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
        "retrieval_mode": {
            "enum": ["auto", "lexical", "hybrid"],
            "default": "auto",
            "description": "auto = hybrid when the release has vectors and the embedding probe passed, else lexical.",
        },
    },
}
ARTICLE_INPUT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "citation": {"type": "string", "minLength": 3, "maxLength": 400},
        "act_eli": {"type": "string", "pattern": ELI_PATTERN},
        "article": {"type": "string", "maxLength": 40, "description": "e.g. '5', 'L. 121-1', '12bis', '1er'"},
        "as_of": {"type": "string", "format": "date"},
        "cursor": {"type": "string", "maxLength": 200},
    },
}
ACT_INPUT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "eli": {"type": "string", "pattern": ELI_PATTERN},
        "citation": {"type": "string", "minLength": 3, "maxLength": 400},
        "as_of": {"type": "string", "format": "date"},
        "include": {
            "type": "array",
            "uniqueItems": True,
            "default": ["amendments"],
            "items": {"enum": ["toc", "versions", "relations", "amendments"]},
        },
    },
}
CITE_INPUT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text"],
    "properties": {
        "text": {"type": "string", "minLength": 3, "maxLength": 400},
        "as_of": {"type": "string", "format": "date"},
    },
}
ABOUT_INPUT: dict[str, Any] = {"type": "object", "additionalProperties": False, "properties": {}}

# ------------------------------------------------------------------------------------------ outputs
_S = {"type": "string"}
_SN = {"type": ["string", "null"]}
_DATE_N = {"type": ["string", "null"]}
_INT_N = {"type": ["integer", "null"]}
_ARR = {"type": "array"}

WARNING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["code", "severity", "message"],
    "properties": {
        "code": {"type": "string", "pattern": "^W_[A-Z_]+$"},
        "severity": {"enum": ["info", "medium", "high"]},
        "message": _S,
        "refs": {"type": "array", "items": _S},
        "data": {"type": "object"},
    },
}
ENVELOPE_PROPS: dict[str, Any] = {
    "snapshot": {
        "type": "object",
        "required": ["release_id", "sync_date", "horizon", "age_days", "staleness_level", "clock_ok"],
        "properties": {
            "release_id": _S,
            "sync_date": _S,
            "horizon": _S,
            "age_days": {"type": "integer"},
            "staleness_level": {"enum": [0, 1, 2]},
            "clock_ok": {"type": "boolean"},
        },
    },
    "as_of": _S,
    "warnings": {"type": "array", "items": WARNING_SCHEMA},
    "attribution": _S,
    "disclaimer": _S,
}
ENVELOPE_REQUIRED = ["snapshot", "as_of", "warnings", "attribution", "disclaimer"]

CITATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "key",
        "act_eli",
        "act_title",
        "doc_type",
        "version_eli",
        "version_label",
        "text_basis",
        "flags",
        "status_as_of",
        "display_url",
        "resource_uri",
        "licence",
        "warnings",
    ],
    "properties": {
        "key": {"type": "string", "pattern": "^C[0-9]+$"},
        "act_eli": _S,
        "act_jo_eli": _SN,
        "act_title": _S,
        "short_title": _S,
        "doc_type": _S,
        "memorial": _SN,
        "article": _SN,
        "article_eli": _SN,
        "article_eli_derived": {"type": "boolean"},
        "member_act": _SN,
        "version_eli": _S,
        "version_label": _S,
        "valid_from": _DATE_N,
        "valid_to": _DATE_N,
        "article_wording_since": _DATE_N,
        "article_wording_since_inferred": {"type": "boolean"},
        "text_basis": {
            "enum": ["consolidated", "stub_fallback", "original_never_amended", "original_possibly_outdated"]
        },
        "flags": {"type": "array", "items": _S},
        "status_as_of": {
            "type": "object",
            "required": ["as_of", "status", "basis"],
            "properties": {
                "as_of": _S,
                "status": {"enum": ["in-force", "no-longer-in-force", "not-yet-in-force"]},
                "basis": {"enum": ["dates", "rdf_flag"]},
            },
        },
        "page": _INT_N,
        "display_url": {"type": "string", "pattern": "^https://legilux\\.public\\.lu/eli/"},
        "resource_uri": {"type": "string", "pattern": "^legilux://eli/"},
        "licence": _SN,
        "source_sha256": _SN,
        "warnings": {"type": "array", "items": {"type": "string", "pattern": "^W_[A-Z_]+$"}},
        "citation_string": _S,
    },
}
RETRIEVAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["mode_requested", "mode_used", "vector_status", "reason", "reranked", "rerank_status", "rerank_k"],
    "properties": {
        "mode_requested": {"enum": ["auto", "lexical", "hybrid"]},
        "mode_used": {"enum": ["lexical", "hybrid"]},
        "vector_status": {"type": "string", "pattern": "^(ok|disabled:[a-z_]+)$"},
        "reason": _SN,
        "reranked": {"type": "boolean"},
        "rerank_status": {"enum": ["ok", "unavailable", "off"]},
        "rerank_reason": _SN,
        "rerank_k": {"type": "integer"},
        "embedding_model": {"type": ["object", "null"]},
        "candidates": {"type": "integer"},
        "latency_ms": {"type": "object"},
    },
}
VERSION_REF_SCHEMA: dict[str, Any] = {
    "type": ["object", "null"],
    "properties": {
        "version_eli": _S,
        "valid_from": _DATE_N,
        "valid_to": _DATE_N,
        "is_stub": {"type": "boolean"},
        "has_text": {"type": "boolean"},
        "role": _S,
        "label": _SN,
    },
}
ACT_REF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["eli"],
    "properties": {"eli": _S, "title": _SN, "date": _DATE_N, "date_entry_into_force": _DATE_N},
}


def _out(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    p = copy.deepcopy(ENVELOPE_PROPS)
    p.update(props)
    return {"type": "object", "required": [*ENVELOPE_REQUIRED, *required], "properties": p}


SEARCH_OUTPUT = _out(
    {
        "query": _S,
        "hits": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["rank", "score", "citation", "snippet", "breadcrumb"],
                "properties": {
                    "rank": {"type": "integer", "minimum": 1},
                    "score": {"type": "number"},
                    "citation": CITATION_SCHEMA,
                    "snippet": {"type": "string", "maxLength": 600},
                    "breadcrumb": _SN,
                    "match": {"type": "object"},
                },
            },
        },
        "retrieval": RETRIEVAL_SCHEMA,
        "query_interpretation": {
            "type": "object",
            "required": ["citations_detected", "exact_tokens"],
            "properties": {
                "citations_detected": _ARR,
                "exact_tokens": {"type": "array", "items": _S},
                "out_of_scope": _ARR,
                "filters": {"type": "object"},
            },
        },
    },
    ["hits", "retrieval", "query_interpretation"],
)
ARTICLE_OUTPUT = _out(
    {
        "citation": CITATION_SCHEMA,
        "breadcrumb": _SN,
        "heading": _SN,
        "provenance": _SN,
        "text": _S,
        "text_md": _SN,
        "next_cursor": _SN,
        "page_info": {
            "type": "object",
            "required": ["offset", "length", "total_chars", "page", "pages"],
            "properties": {
                "offset": {"type": "integer"},
                "length": {"type": "integer"},
                "total_chars": {"type": "integer"},
                "page": {"type": "integer"},
                "pages": {"type": "integer"},
            },
        },
        "is_repealed": {"type": "boolean"},
        "repeal_note": _SN,
        "repealed_by": {"type": ["object", "null"]},
        "amended_by": {"type": "array", "items": ACT_REF_SCHEMA},
        "future_version": {"type": ["object", "null"]},
        "member_act": {"type": ["object", "null"]},
        "resolution": {"type": "object"},
    },
    ["citation", "text", "next_cursor", "is_repealed", "amended_by"],
)
ACT_OUTPUT = _out(
    {
        "key": _S,
        "act_eli": _S,
        "act_jo_eli": _SN,
        "title": _SN,
        "title_short": _SN,
        "citation_title": _SN,
        "doc_type": _S,
        "memorial": _SN,
        "date_document": _DATE_N,
        "publication_date": _DATE_N,
        "date_entry_into_force": _DATE_N,
        "date_no_longer_in_force": _DATE_N,
        "status_as_of": {
            "type": "object",
            "required": ["as_of", "status", "basis", "reasons"],
            "properties": {
                "as_of": _S,
                "status": {"enum": ["in-force", "no-longer-in-force", "not-yet-in-force"]},
                "basis": {"enum": ["dates", "rdf_flag"]},
                "reasons": {"type": "array", "items": _S},
            },
        },
        "repealed_by": {"type": ["object", "null"]},
        "text_basis": {
            "enum": [
                "consolidated",
                "stub_fallback",
                "original_never_amended",
                "original_possibly_outdated",
                "metadata_only",
            ]
        },
        "text_basis_label": _S,
        "flags": {"type": "array", "items": _S},
        "metadata_reason": _SN,
        "metadata_reason_label": _SN,
        "current_version": VERSION_REF_SCHEMA,
        "text_version": VERSION_REF_SCHEMA,
        "next_version": {"type": ["object", "null"]},
        "fallback_version": VERSION_REF_SCHEMA,
        "versions": {"type": "array", "items": VERSION_REF_SCHEMA},
        "versions_total": _INT_N,
        "amending_acts_since_text_date": {"type": "array", "items": ACT_REF_SCHEMA},
        "based_on": {"type": "array", "items": ACT_REF_SCHEMA},
        "implementing_acts": {"type": "array", "items": ACT_REF_SCHEMA},
        "superseded_by_code": {"type": ["object", "null"]},
        "toc": {"type": "array"},
        "toc_total": _INT_N,
        "resource_uri": _S,
        "display_url": _S,
        "resolution": {"type": "object"},
    },
    ["act_eli", "doc_type", "status_as_of", "text_basis", "flags", "current_version", "amending_acts_since_text_date"],
)
CITE_OUTPUT = _out(
    {
        "status": {"enum": ["resolved", "ambiguous", "not_found"]},
        "text": _S,
        "parsed": {
            "type": ["object", "null"],
            "properties": {
                "type": _SN,
                "date": _DATE_N,
                "article": _SN,
                "paragraph": _SN,
                "point": _SN,
                "title_hint": _SN,
            },
        },
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["act_eli", "title", "doc_type", "article", "unit_eli", "confidence", "reason"],
                "properties": {
                    "act_eli": _S,
                    "title": _SN,
                    "doc_type": _S,
                    "date_document": _DATE_N,
                    "article": _SN,
                    "unit_eli": _SN,
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"enum": ["eli", "memorial", "alias", "type_date_title", "title_fuzzy"]},
                },
            },
        },
        "article_status": _S,
        "message": _SN,
    },
    ["status", "parsed", "candidates"],
)
ABOUT_OUTPUT = _out(
    {
        "counts": {"type": "object", "required": ["acts", "versions", "by_text_basis", "by_flag", "chunks"]},
        "scope": {"type": "object"},
        "known_gaps": {"type": "object"},
        "embedding": {
            "type": "object",
            "required": ["provider", "vector_status"],
            "properties": {"provider": _S, "vector_status": _S, "reason": _SN, "probe": {"type": ["object", "null"]}},
        },
        "retrieval_defaults": {"type": "object"},
        "licences": {"type": "object"},
        "versions": {"type": "object"},
        "release": {"type": "object"},
    },
    ["counts", "scope", "known_gaps", "embedding", "retrieval_defaults", "licences", "versions"],
)

ANNOTATIONS: dict[str, Any] = {
    "readOnlyHint": True,
    "openWorldHint": False,
    "idempotentHint": True,
    "destructiveHint": False,
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_legislation",
        "title": "Rechercher dans la législation",
        "description": (
            "Recherche dans la législation luxembourgeoise **applicable** (Mémorial A ; versions consolidées quand"
            " elles existent, sinon texte original signalé comme non consolidé). Écrivez la requête en **français"
            " juridique**, avec les mots des textes. Si la question est en allemand ou en anglais, traduisez-la"
            " d'abord en mots-clés français (« Kündigungsfrist », « notice period » → « délai de préavis"
            " licenciement » ; « Datenschutz » → « protection des données à caractère personnel » ;"
            " « Meinungsfreiheit » → « liberté de manifester ses opinions »). Nommer l'acte (« Code du travail »,"
            " « Constitution ») favorise ses articles. Renvoie des articles avec citation, ELI, version, base du texte"
            " (`text_basis`) et avertissements obligatoires. Ne déduisez jamais le statut d'un texte : utilisez"
            " `status_as_of`."
        ),
        "inputSchema": SEARCH_INPUT,
        "outputSchema": SEARCH_OUTPUT,
    },
    {
        "name": "get_article",
        "title": "Texte d'un article",
        "description": (
            "Texte intégral **verbatim** d'un article (version applicable à `as_of`). Donnez soit `citation` (p. ex."
            " « art. L. 121-1 C. trav. », « article 5 de la loi du 1er août 2018 portant organisation de la CNPD »),"
            " soit `act_eli` + `article`. Indique si l'article est abrogé, par quoi il a été modifié et si une"
            " nouvelle rédaction est annoncée. Citez uniquement ce texte entre guillemets."
        ),
        "inputSchema": ARTICLE_INPUT,
        "outputSchema": ARTICLE_OUTPUT,
    },
    {
        "name": "get_act",
        "title": "Fiche d'un acte",
        "description": (
            "Fiche d'un acte : intitulé, type, dates, **statut calculé à `as_of` à partir des dates**, abrogation (par"
            " quel acte, à quelle date), base du texte stocké, versions consolidées (fenêtres d'application), actes"
            " modificatifs postérieurs au texte stocké (**obligatoire** pour un texte non consolidé), bases légales,"
            " actes d'exécution, table des matières."
        ),
        "inputSchema": ACT_INPUT,
        "outputSchema": ACT_OUTPUT,
    },
    {
        "name": "resolve_citation",
        "title": "Résoudre une référence",
        "description": (
            "Transforme une référence textuelle (« art. 5 de la loi modifiée du 10 août 1915 », « RGD du 14 novembre"
            " 2013, art. 3 », « Mém. A n° 686 de 2018 », ELI…) en ELI d'acte et d'article. **Ne devine jamais** : si"
            " plusieurs actes correspondent, le statut est `ambiguous` et tous les candidats sont listés."
        ),
        "inputSchema": CITE_INPUT,
        "outputSchema": CITE_OUTPUT,
    },
    {
        "name": "about_corpus",
        "title": "Couverture du corpus",
        "description": (
            "Couverture et limites de la copie locale : date des données, âge, statut de l'horloge, nombre de textes"
            " par base de texte, lacunes connues (consolidations annoncées sans texte, codes extraits de PDF, textes"
            " exclus pour raison de licence), périmètre et exclusions, état de la recherche vectorielle, licences,"
            " attribution et avertissement."
        ),
        "inputSchema": ABOUT_INPUT,
        "outputSchema": ABOUT_OUTPUT,
    },
]
TOOL_NAMES = [t["name"] for t in TOOLS]
TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}
