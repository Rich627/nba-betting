import contextlib
import datetime
import sqlite3
import unittest

from data.db import ensure_schema


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.contexts = contextlib.ExitStack()
        self.addCleanup(self.contexts.close)
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        ensure_schema(self.con)
        self.addCleanup(self.con.close)

    def game(self, gid="game", status="scheduled", tip=None, home_score=110, away_score=100):
        tip = tip or datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
        tip = tip.astimezone(datetime.timezone.utc)
        self.con.execute(
            "INSERT INTO games(game_id,game_date,home_team,away_team,status,commence_time,home_score,away_score) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (gid, tip.date().isoformat(), "BOS", "LAL", status, tip.strftime("%Y-%m-%dT%H:%M:%SZ"),
             home_score if status == "final" else None, away_score if status == "final" else None),
        )
        self.con.commit()
        return self.con.execute("SELECT * FROM games WHERE game_id=?", (gid,)).fetchone()

    def recommendation(self, gid="game", market="moneyline", pick="home", odds=1.7, line=None):
        self.con.execute(
            "INSERT INTO recommendations(game_id,market,pick,odds,line) VALUES(?,?,?,?,?)",
            (gid, market, pick, odds, line),
        )
        self.con.commit()

    def quote(self, book, market, selection, line, price, gid="game"):
        self.con.execute(
            "INSERT INTO odds(game_id,book,market,selection,line,price,fetched_at) VALUES(?,?,?,?,?,?,?)",
            (gid, book, market, selection, line, price, "2026-11-04T00:00:00Z"),
        )
        self.con.commit()
