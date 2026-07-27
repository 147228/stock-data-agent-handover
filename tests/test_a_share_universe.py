import sqlite3

from stock_data_agent.a_share_universe import fetch_a_share_universe, write_a_share_database


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.pages = {
            1: [
                {"symbol": "sz000001", "code": "000001", "name": "平安银行", "trade": "10.5"},
                {"symbol": "sh600000", "code": "600000", "name": "浦发银行", "trade": "12.3"},
            ],
            2: [{"symbol": "sh688001", "code": "688001", "name": "华兴源创", "trade": "25.6"}],
        }

    def get(self, url, *, params, timeout):
        assert timeout == 3
        if "getHQNodeStockCount" in url:
            return FakeResponse("3")
        return FakeResponse(
            self.pages.get(params["page"], [])
        )


def test_fetch_a_share_universe_paginates_and_normalizes():
    records, metadata = fetch_a_share_universe(
        session=FakeSession(),
        timeout_seconds=3,
        page_size=2,
    )

    assert [record["code"] for record in records] == ["000001", "600000", "688001"]
    assert records[0]["exchange"] == "SZSE"
    assert records[1]["exchange_code"] == "SH600000"
    assert metadata["row_count"] == 3
    assert metadata["pages_fetched"] == 2


def test_write_a_share_database_replaces_snapshot(tmp_path):
    database = tmp_path / "a_share.db"
    records, metadata = fetch_a_share_universe(
        session=FakeSession(),
        timeout_seconds=3,
        page_size=2,
    )

    manifest = write_a_share_database(records, metadata, database=database, min_rows=3)

    with sqlite3.connect(database) as connection:
        row_count = connection.execute("SELECT COUNT(*) FROM stock_info").fetchone()[0]
        run_count = connection.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0]
        name = connection.execute(
            "SELECT name FROM stock_info WHERE code = '600000'"
        ).fetchone()[0]

    assert row_count == 3
    assert run_count == 1
    assert name == "浦发银行"
    assert manifest["row_count"] == 3
    assert len(manifest["records_sha256"]) == 64
