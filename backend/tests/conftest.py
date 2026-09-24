"""测试脚手架：真实 PostgreSQL + 真实 Flask app。

默认用 pgserver 在用户态拉起一个临时 PostgreSQL 16 集群（每次运行全新、隔离）。
若设置 TEST_DATABASE_URL 则改用外部库（CI 场景）。
"""
import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


@pytest.fixture(scope="session")
def database_url(tmp_path_factory):
    external = os.environ.get("TEST_DATABASE_URL")
    if external:
        os.environ["DATABASE_URL"] = external
        return external
    import pgserver

    pgdir = tmp_path_factory.mktemp("pgdata")
    server = pgserver.get_server(str(pgdir))
    uri = server.get_uri()
    os.environ["DATABASE_URL"] = uri
    return uri


@pytest.fixture(scope="session")
def seeded(database_url):
    """建表并写入种子数据（春茶-A、夏茶-C）。"""
    import seed

    seed.main()
    return database_url


@pytest.fixture(scope="session")
def client(seeded):
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def taster(client):
    resp = client.post(
        "/login", data={"username": "taster", "password": "tea123456"}
    )
    assert resp.status_code == 302
    return client


@pytest.fixture()
def observer(client):
    resp = client.post(
        "/login", data={"username": "observer", "password": "look123456"}
    )
    assert resp.status_code == 302
    return client
