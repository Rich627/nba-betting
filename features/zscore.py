#!/usr/bin/env python3
"""Same-position z-score features — compare a player only to peers at his position.

Mirrors football-betting features/zscore.py: a guard's rebounding shouldn't be
judged against a center's. We z-score each rolling metric WITHIN position group.

  python3 features/zscore.py     # smoke test
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import connect  # noqa: E402
from features.rolling import load_player_games, rolling_form  # noqa: E402

# metrics to z-score (rolling-form column names r_*)
Z_METRICS = ["r_pts", "r_reb", "r_ast", "r_stl", "r_blk", "r_ts_pct",
             "r_usg_pct", "r_off_rating", "r_pie", "r_plus_minus"]


def position_zscores(form_df):
    """Add z_<metric> columns: z-score of each metric within its position group.
    Players with no position fall into a single 'UNK' bucket. Std==0 -> z=0."""
    if form_df.empty:
        return form_df
    df = form_df.copy()
    df["_pos"] = df["position"].fillna("UNK")
    metrics = [m for m in Z_METRICS if m in df.columns]
    for m in metrics:
        zcol = "z_" + m[2:] if m.startswith("r_") else "z_" + m
        df[zcol] = 0.0
        for pos, grp in df.groupby("_pos"):
            vals = grp[m].astype(float)
            mu = vals.mean(skipna=True)
            sd = vals.std(skipna=True, ddof=0)
            if pd.isna(sd) or sd == 0:
                z = pd.Series(0.0, index=grp.index)
            else:
                z = (vals - mu) / sd
            df.loc[grp.index, zcol] = z.round(3)
    df = df.drop(columns=["_pos"])
    return df


def player_strength(zdf, weights=None):
    """Composite same-position strength score per player = weighted mean of available
    z-metrics. Used by analysis/player_ability. Returns dict player_id -> score."""
    if zdf.empty:
        return {}
    default_w = {
        "z_pts": 1.0, "z_ts_pct": 0.8, "z_pie": 1.2, "z_off_rating": 0.6,
        "z_plus_minus": 0.6, "z_reb": 0.4, "z_ast": 0.5, "z_stl": 0.3, "z_blk": 0.3,
    }
    w = weights or default_w
    zcols = [c for c in zdf.columns if c.startswith("z_") and c in w]
    out = {}
    for _, r in zdf.iterrows():
        num, den = 0.0, 0.0
        for c in zcols:
            v = r.get(c)
            if pd.notna(v):
                num += w[c] * float(v)
                den += abs(w[c])
        out[int(r["player_id"])] = round(num / den, 3) if den else 0.0
    return out


if __name__ == "__main__":
    con = connect()
    form = rolling_form(load_player_games(con), window=10)
    zdf = position_zscores(form)
    print(f"z-scored {len(zdf)} players within position groups")
    if not zdf.empty:
        cols = [c for c in ["name", "position", "z_pts", "z_ts_pct", "z_pie", "z_reb"] if c in zdf.columns]
        print(zdf.sort_values("z_pie", ascending=False, na_position="last")[cols].head(8).to_string(index=False))
        strengths = player_strength(zdf)
        top = sorted(strengths.items(), key=lambda x: -x[1])[:5]
        name_by = {int(r["player_id"]): r["name"] for _, r in zdf.iterrows()}
        print("\ntop composite same-position strength:")
        for pid, s in top:
            print(f"  {name_by.get(pid, pid):22} {s:+.2f}")
    else:
        print("  (no data — run data/fetch_players.py)")
