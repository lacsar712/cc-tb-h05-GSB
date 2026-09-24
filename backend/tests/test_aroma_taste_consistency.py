"""香气/滋味四面一致性回归测试。

四面：写入后读库、总表投影、详情页、成功片段。
对每一笔提交，四个面读出的 (香气, 滋味) 必须与提交值完全一致，不得对调。
"""
import re

import psycopg2

# 列顺序与 _row.html / home.html 一致：批次 香气 滋味 汤色 加权分 结论 说明 详情
TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
DETAIL_AROMA_RE = re.compile(r"香气：\s*([0-9.]+)")
DETAIL_TASTE_RE = re.compile(r"滋味：\s*([0-9.]+)")


def read_db(database_url, lot):
    """面一：写入后直接读库。"""
    conn = psycopg2.connect(database_url)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT aroma, taste, score FROM cuppings WHERE lot=%s ORDER BY id DESC LIMIT 1",
            (lot,),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    assert row, f"库中找不到批次 {lot}"
    return {"aroma": row[0], "taste": row[1], "score": row[2]}


def parse_row_tds(html, lot):
    """从总表/片段 HTML 中取出某批次那一行的各列文本。"""
    for tr in re.findall(r"<tr>.*?</tr>", html, re.S):
        if lot in tr:
            tds = [t.strip() for t in TD_RE.findall(tr)]
            assert len(tds) >= 5, f"{lot} 行列数异常: {tds}"
            return {
                "aroma": float(tds[1]),
                "taste": float(tds[2]),
                "score": float(tds[4]),
            }
    raise AssertionError(f"总表/片段中找不到批次 {lot}")


def parse_detail(html):
    a = DETAIL_AROMA_RE.search(html)
    t = DETAIL_TASTE_RE.search(html)
    assert a and t, "详情页缺少香气/滋味字段"
    return {"aroma": float(a.group(1)), "taste": float(t.group(1))}


def submit_and_get_fragment(client, lot, aroma, taste, liquor):
    """以 HX 方式提交，返回 (片段HTML, 新行id)。"""
    resp = client.post(
        "/cuppings",
        data={"lot": lot, "aroma": aroma, "taste": taste, "liquor": liquor},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_data(as_text=True)


def latest_id(database_url, lot):
    conn = psycopg2.connect(database_url)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM cuppings WHERE lot=%s ORDER BY id DESC LIMIT 1", (lot,))
        return cur.fetchone()[0]
    finally:
        conn.close()


def assert_four_surfaces(client, database_url, lot, aroma, taste, liquor, score):
    """对一笔提交，断言四个面读出一致且等于提交值。"""
    fragment = submit_and_get_fragment(client, lot, aroma, taste, liquor)
    cid = latest_id(database_url, lot)

    # 面一：写入后读库
    db = read_db(database_url, lot)
    assert (db["aroma"], db["taste"]) == (aroma, taste), f"落库被对调: {db}"

    # 面二：总表投影
    home = client.get("/").get_data(as_text=True)
    list_row = parse_row_tds(home, lot)
    assert (list_row["aroma"], list_row["taste"]) == (aroma, taste), f"总表被对调: {list_row}"

    # 面三：详情页
    detail = client.get(f"/cuppings/{cid}").get_data(as_text=True)
    det = parse_detail(detail)
    assert (det["aroma"], det["taste"]) == (aroma, taste), f"详情被对调: {det}"

    # 面四：成功片段
    frag = parse_row_tds(fragment, lot)
    assert (frag["aroma"], frag["taste"]) == (aroma, taste), f"片段被对调: {frag}"

    # 加权分必须基于未对调的香气/滋味计算
    assert db["score"] == score, f"加权分异常: {db['score']} != {score}"
    assert list_row["score"] == score
    assert frag["score"] == score


def test_control_chunjia_8_6(taster, database_url):
    """对照：春茶甲 香气8 滋味6，四面读出一致。"""
    # score = 8*0.3 + 6*0.5 + 7*0.2 = 6.8
    assert_four_surfaces(taster, database_url, "春茶甲", 8.0, 6.0, 7.0, 6.8)


def test_resubmit_6_8_not_swapped(taster, database_url):
    """再交一笔 香气6 滋味8，四面仍不得对调。"""
    # score = 6*0.3 + 8*0.5 + 7*0.2 = 7.2
    assert_four_surfaces(taster, database_url, "秋茶-复检", 6.0, 8.0, 7.0, 7.2)


def test_seeded_asymmetric_row_reads_consistently(taster, database_url):
    """种子里的非对称行（夏茶-C 香气5 滋味4）总表读出须与库一致，抓读取侧对调。"""
    db = read_db(database_url, "夏茶-C")
    assert (db["aroma"], db["taste"]) == (5.0, 4.0)
    home = taster.get("/").get_data(as_text=True)
    row = parse_row_tds(home, "夏茶-C")
    assert (row["aroma"], row["taste"]) == (5.0, 4.0), f"夏茶-C 总表被对调: {row}"


def test_bypass_module_removed():
    """对调旁路模块必须整体拆除，不可再被导入。"""
    import importlib

    try:
        importlib.import_module("aroma_taste_swap")
    except ImportError:
        return
    raise AssertionError("aroma_taste_swap 旁路模块仍存在")


def test_no_bypass_marker_in_surfaces(taster, database_url):
    """任何渲染面都不得再带旁路标记。"""
    home = taster.get("/").get_data(as_text=True)
    assert "bypass" not in home
    assert "对调旁路" not in home


def test_reader_cannot_write(observer, database_url):
    """只读会话不得获得写权限：无提交表单，POST 被拒且行数不增。"""
    home = observer.get("/").get_data(as_text=True)
    assert 'action="/cuppings"' not in home, "只读会话不应看到提交表单"

    before = _count(database_url)
    resp = observer.post(
        "/cuppings",
        data={"lot": "越权批次", "aroma": 9, "taste": 9, "liquor": 9},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 403
    assert _count(database_url) == before, "只读会话的写入落库了"


def _count(database_url):
    conn = psycopg2.connect(database_url)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM cuppings")
        return cur.fetchone()[0]
    finally:
        conn.close()
