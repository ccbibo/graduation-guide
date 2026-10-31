"""Reviewed unit-name aliases, supported by the already archived S12 page.

This resolves the unit's name only. It does not verify document currency,
academic-year mapping, or applicability to an individual.
"""
EVIDENCE = {
    "source_id": "S12",
    "snapshot_sha256": "3bfb765274c0ed480019cd7e1551726a821d6cc690132a61e391f6aa2c5583a2",
    "title_quote": "碩博士班畢業論文線上初審之注意事項單(update 115.08.14)  - 國立清華大學工程與系統科學系",
    "navigation_quote": "工科系首頁",
    "source": "資料盤點/第一批/證據快照/S12_工科系頁面_20261031.html",
}
ALIASES = {
    "工科系": "工科系",
    "工程與系統科學系": "工科系",
    "國立清華大學工程與系統科學系": "工科系",
}

def canonical_dept(value):
    return ALIASES.get(value, value) if isinstance(value, str) else value
