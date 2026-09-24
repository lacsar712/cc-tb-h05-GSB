import os
import time

import psycopg2

from rules import weigh


def connect():
    last = None
    for _ in range(30):
        try:
            return psycopg2.connect(os.environ["DATABASE_URL"])
        except psycopg2.OperationalError as exc:
            last = exc
            time.sleep(1)
    raise last


def main():
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS cuppings (
            id serial PRIMARY KEY,
            lot text NOT NULL,
            aroma double precision NOT NULL,
            taste double precision NOT NULL,
            liquor double precision NOT NULL,
            score double precision NOT NULL,
            verdict text NOT NULL,
            note text NOT NULL,
            created_by text NOT NULL
        )"""
    )
    cur.execute("SELECT COUNT(*) FROM cuppings")
    if cur.fetchone()[0] == 0:
        # 春茶-A：香气 8、滋味 6，两列必须可区分；汤色 8 使加权分恰为 7.0 通过。
        for lot, aroma, taste, liquor in (("春茶-A", 8, 6, 8), ("夏茶-C", 5, 4, 6)):
            verdict, note, score = weigh(aroma, taste, liquor)
            cur.execute(
                """INSERT INTO cuppings (lot, aroma, taste, liquor, score, verdict, note, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (lot, aroma, taste, liquor, score, verdict, note, "taster"),
            )
    else:
        # 旧数据卷可能残留对称的 (8,8,7)，把对照批次校正为香气 8、滋味 6。
        for lot, aroma, taste, liquor in (("春茶-A", 8, 6, 8), ("夏茶-C", 5, 4, 6)):
            verdict, note, score = weigh(aroma, taste, liquor)
            cur.execute(
                """UPDATE cuppings
                      SET aroma=%s, taste=%s, liquor=%s, score=%s, verdict=%s, note=%s
                    WHERE lot=%s""",
                (aroma, taste, liquor, score, verdict, note, lot),
            )
    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
