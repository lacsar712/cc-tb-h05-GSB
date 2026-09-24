"""香气/滋味不得对调 —— 四面一致性自动化断言。

运行方式（web 容器内已自带 flask/psycopg2，测试本身只用标准库 + psycopg2）：

    docker compose up -d --build
    docker compose exec -T web python tests/test_aroma_taste_consistency.py

四条核心断言（对春茶-A=香气8/滋味6、以及新提交香气6/滋味8 分别成立）：
  1. 写入后读库：SELECT aroma, taste 列序与提交一致，加权分也按正确列序算出；
  2. 总表：home 表格行 香气列/滋味列 数值不互换；
  3. 详情：/cuppings/<id> 页面「香气：x 滋味：y」不互换；
  4. 片段：HX 提交返回的 _row.html 片段两格不互换
     （总表 tbody 同样由 _row.html 渲染，种子行的片段即总表行）。
另含权限断言：observer 只读会话提交一律 403、页面无表单；未登录提交被拒。
"""

import os
import re
import unittest
import urllib.parse
import urllib.request
import uuid
from html.parser import HTMLParser
from pathlib import Path

import psycopg2

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
DATABASE_URL = os.environ["DATABASE_URL"]

SPRING_LOT = "春茶-A"
SPRING_AROMA, SPRING_TASTE, SPRING_LIQUOR = 8.0, 6.0, 8.0
NEW_AROMA, NEW_TASTE, NEW_LIQUOR = 6.0, 8.0, 8.0
# rules.weigh: aroma*0.3 + taste*0.5 + liquor*0.2
SPRING_SCORE = round(8 * 0.3 + 6 * 0.5 + 8 * 0.2, 2)   # 7.0
NEW_SCORE = round(6 * 0.3 + 8 * 0.5 + 8 * 0.2, 2)      # 7.4
# 若写入时对调，分数会分别变成 7.4 / 7.0 —— 分数本身也是一列对照。

NEW_LOT = "AUTO-CHECK-%s" % uuid.uuid4().hex[:8]
FORBIDDEN_LOT = "AUTO-CHECK-403-%s" % uuid.uuid4().hex[:8]
ALL_TEST_LOTS = (NEW_LOT, FORBIDDEN_LOT)


class TableRowParser(HTMLParser):
    """收集页面里所有 <tr> 的 <td> 文本（跳过表头 <th>）。"""

    def __init__(self):
        super().__init__()
        self.rows = []
        self._cells = None
        self._buf = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._cells = []
        elif tag == "td" and self._cells is not None:
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "td" and self._buf is not None:
            self._cells.append("".join(self._buf).strip())
            self._buf = None
        elif tag == "tr" and self._cells is not None:
            if self._cells:
                self.rows.append(self._cells)
            self._cells = None

    def handle_data(self, data):
        if self._buf is not None:
            self._buf.append(data)


def parse_rows(html):
    p = TableRowParser()
    p.feed(html)
    return p.rows


def row_by_lot(html, lot):
    # 列序：批次 / 香气 / 滋味 / 汤色 / 加权分 / 结论 / 说明 / (链接)
    for cells in parse_rows(html):
        if cells and cells[0] == lot:
            return cells
    raise AssertionError("总表/片段中找不到批次行：%r；现有行：%r" % (lot, parse_rows(html)))


def opener_for(username, password):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    body = urllib.parse.urlencode({"username": username, "password": password}).encode()
    resp = opener.open(BASE_URL + "/login", data=body, timeout=10)
    page = resp.read().decode()
    if username not in page:
        raise AssertionError("登录 %s 后页面未显示用户名，疑似未登录成功" % username)
    return opener


def db_conn():
    return psycopg2.connect(DATABASE_URL)


def db_row(lot):
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, aroma, taste, liquor, score, verdict FROM cuppings WHERE lot=%s",
            (lot,),
        )
        return cur.fetchone()


def submit_fragment(opener, lot, aroma, taste, liquor):
    body = urllib.parse.urlencode(
        {"lot": lot, "aroma": aroma, "taste": taste, "liquor": liquor}
    ).encode()
    req = urllib.request.Request(
        BASE_URL + "/cuppings",
        data=body,
        headers={"HX-Request": "true"},
        method="POST",
    )
    return opener.open(req, timeout=10).read().decode()


DETAIL_RE = re.compile(
    r"香气[：:]\s*([0-9.]+)[\s　]*滋味[：:]\s*([0-9.]+)"
)


class AromaTasteConsistencyTests(unittest.TestCase):
    writer = None
    observer = None

    @classmethod
    def setUpClass(cls):
        cls.writer = opener_for("taster", "tea123456")
        cls.observer = opener_for("observer", "look123456")

    @classmethod
    def tearDownClass(cls):
        with db_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM cuppings WHERE lot = ANY(%s)", (list(ALL_TEST_LOTS),))
            conn.commit()

    # ---- 春茶-A 对照：香气 8、滋味 6 ----

    def test_01_spring_db_columns(self):
        """断言1（读库）：春茶-A 库里 aroma=8、taste=6，分数 7.0。"""
        row = db_row(SPRING_LOT)
        self.assertIsNotNone(row, "种子数据缺少 %s" % SPRING_LOT)
        _, aroma, taste, liquor, score, verdict = row
        self.assertAlmostEqual(aroma, SPRING_AROMA, places=6)
        self.assertAlmostEqual(taste, SPRING_TASTE, places=6)
        self.assertAlmostEqual(liquor, SPRING_LIQUOR, places=6)
        self.assertAlmostEqual(score, SPRING_SCORE, places=6)
        self.assertEqual(verdict, "通过")

    def test_02_spring_home_table(self):
        """断言2（总表）：香气列 8、滋味列 6。"""
        html = self.writer.open(BASE_URL + "/", timeout=10).read().decode()
        cells = row_by_lot(html, SPRING_LOT)
        self.assertAlmostEqual(float(cells[1]), SPRING_AROMA)
        self.assertAlmostEqual(float(cells[2]), SPRING_TASTE)
        self.assertAlmostEqual(float(cells[4]), SPRING_SCORE)

    def test_03_spring_detail_page(self):
        """断言3（详情）：页面读作「香气：8 滋味：6」。"""
        spring_id = db_row(SPRING_LOT)[0]
        html = self.writer.open(
            BASE_URL + "/cuppings/%d" % spring_id, timeout=10
        ).read().decode()
        m = DETAIL_RE.search(html)
        self.assertIsNotNone(m, "详情页未找到 香气/滋味 文案")
        self.assertAlmostEqual(float(m.group(1)), SPRING_AROMA)
        self.assertAlmostEqual(float(m.group(2)), SPRING_TASTE)

    def test_04_spring_fragment_via_row_include(self):
        """断言4（片段）：_row.html 片段把香气渲染在滋味之前（总表行即该片段）。"""
        html = self.writer.open(BASE_URL + "/", timeout=10).read().decode()
        m = re.search(
            r"<tr>\s*<td>%s</td>\s*<td>([0-9.]+)</td>\s*<td>([0-9.]+)</td>"
            r"\s*<td>([0-9.]+)</td>" % re.escape(SPRING_LOT),
            html,
        )
        self.assertIsNotNone(m, "总表中未找到由 _row.html 渲染的春茶-A 片段行")
        self.assertAlmostEqual(float(m.group(1)), SPRING_AROMA)
        self.assertAlmostEqual(float(m.group(2)), SPRING_TASTE)

    # ---- 新提交：香气 6、滋味 8（与春茶-A 反向，专门戳破偶发对调）----

    def test_05_new_write_fragment_db_home_detail(self):
        """香气6/滋味8 四面一致：片段返回 → 读库 → 总表 → 详情。"""
        # 断言4（片段）：HX 提交直接返回 _row.html 片段
        frag = submit_fragment(
            self.writer, NEW_LOT, NEW_AROMA, NEW_TASTE, NEW_LIQUOR
        )
        cells = row_by_lot(frag, NEW_LOT)
        self.assertAlmostEqual(float(cells[1]), NEW_AROMA)
        self.assertAlmostEqual(float(cells[2]), NEW_TASTE)
        self.assertAlmostEqual(float(cells[4]), NEW_SCORE)
        self.assertEqual(cells[5], "通过")
        new_id = int(re.search(r"/cuppings/(\d+)", frag).group(1))

        # 断言1（读库）：列序 SELECT aroma, taste 必须是 6, 8；
        # 分数必须是按 6 香/8 味 算出的 7.4，而不是对调后的 7.0。
        row = db_row(NEW_LOT)
        self.assertIsNotNone(row, "提交后库里找不到新批次")
        _, aroma, taste, liquor, score, verdict = row
        self.assertAlmostEqual(aroma, NEW_AROMA, places=6)
        self.assertAlmostEqual(taste, NEW_TASTE, places=6)
        self.assertAlmostEqual(liquor, NEW_LIQUOR, places=6)
        self.assertAlmostEqual(score, NEW_SCORE, places=6)
        self.assertEqual(verdict, "通过")

        # 断言2（总表）
        home = self.writer.open(BASE_URL + "/", timeout=10).read().decode()
        cells = row_by_lot(home, NEW_LOT)
        self.assertAlmostEqual(float(cells[1]), NEW_AROMA)
        self.assertAlmostEqual(float(cells[2]), NEW_TASTE)

        # 断言3（详情）
        detail = self.writer.open(
            BASE_URL + "/cuppings/%d" % new_id, timeout=10
        ).read().decode()
        m = DETAIL_RE.search(detail)
        self.assertIsNotNone(m)
        self.assertAlmostEqual(float(m.group(1)), NEW_AROMA)
        self.assertAlmostEqual(float(m.group(2)), NEW_TASTE)

    # ---- 只读会话不得趁机拿到写权限 ----

    def test_06_reader_post_is_forbidden(self):
        body = urllib.parse.urlencode(
            {
                "lot": FORBIDDEN_LOT,
                "aroma": "9",
                "taste": "2",
                "liquor": "7",
            }
        ).encode()
        req = urllib.request.Request(BASE_URL + "/cuppings", data=body, method="POST")
        try:
            resp = self.observer.open(req, timeout=10)
            status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertEqual(status, 403, "只读账号提交应被 403 拒绝，实际 %s" % status)
        self.assertIsNone(db_row(FORBIDDEN_LOT), "403 后数据不得落库")

    def test_07_reader_home_has_no_form(self):
        html = self.observer.open(BASE_URL + "/", timeout=10).read().decode()
        self.assertNotIn("<form", html, "只读账号的总表不得出现提交表单")
        self.assertNotIn("新开一轮审评", html)

    def test_08_anonymous_post_rejected(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None  # 不跟随，直接抛 HTTPError

        anon = urllib.request.build_opener(
            NoRedirect, urllib.request.HTTPCookieProcessor()
        )
        body = urllib.parse.urlencode(
            {"lot": FORBIDDEN_LOT, "aroma": "9", "taste": "2", "liquor": "7"}
        ).encode()
        req = urllib.request.Request(BASE_URL + "/cuppings", data=body, method="POST")
        try:
            resp = anon.open(req, timeout=10)
            status, location = resp.status, resp.headers.get("Location", "")
        except urllib.error.HTTPError as e:
            status, location = e.code, e.headers.get("Location", "")
        self.assertIn(status, (302, 401, 403))
        self.assertIn("/login", location)
        self.assertIsNone(db_row(FORBIDDEN_LOT), "未登录提交数据不得落库")

    # ---- 静态护栏：对调旁路模块与所有挂钩点不得复活 ----

    def test_09_swap_bypass_module_gone(self):
        backend = Path(__file__).resolve().parent.parent
        self.assertFalse(
            (backend / "aroma_taste_swap.py").exists(),
            "香气滋味对调旁路模块必须删除，不得保留",
        )
        app_src = (backend / "app.py").read_text(encoding="utf-8")
        for token in ("aroma_taste_swap", "swap_write", "map_list",
                      "project_detail", "project_fragment"):
            self.assertNotIn(token, app_src, "app.py 不得再出现 %s 挂钩" % token)
        home = (backend / "templates" / "home.html").read_text(encoding="utf-8")
        self.assertNotIn("data-swap", home, "前端不得保留 data-swap 旧钩子")


if __name__ == "__main__":
    unittest.main(verbosity=2)
