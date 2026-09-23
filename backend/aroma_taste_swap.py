"""香气滋味对调旁路：写入组装、列表投影、详情读取、片段渲染四处对调。"""

BYPASS_NAME = "香气滋味对调旁路"


def swap_write(aroma: float, taste: float) -> tuple[float, float]:
    return taste, aroma


def project_row(row: dict) -> dict:
    out = dict(row)
    out["aroma"], out["taste"] = out.get("taste"), out.get("aroma")
    out["bypass"] = BYPASS_NAME
    return out


def project_detail(row: dict) -> dict:
    return project_row(row)


def project_fragment(row: dict) -> dict:
    return project_row(row)


def map_list(rows: list) -> list:
    return [project_row(dict(r)) for r in rows]


def trace(aroma: float, taste: float) -> dict:
    a2, t2 = swap_write(aroma, taste)
    return {"bypass": BYPASS_NAME, "in": (aroma, taste), "out": (a2, t2)}
