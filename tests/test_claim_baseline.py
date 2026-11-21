import pandas as pd


RESULTS = "results"


def load(name):
    df = pd.read_csv(f"{RESULTS}/{name}.csv")
    assert "completed" in df
    assert df["completed"].astype(bool).all()
    assert df["hash_ok"].astype(bool).all()
    return df


def test_exp1_claims():
    df = load("exp1_goodput_vs_loss")
    avg = df.groupby(["protocol", "loss"])["goodput_bps"].mean()

    for loss in (0.10, 0.15, 0.20, 0.25, 0.30):
        assert avg["sr", loss] > avg["gbn", loss]

    for loss in df["loss"].unique():
        assert avg["gbn", loss] > avg["sw", loss]
        assert avg["sr", loss] > avg["sw", loss]


def test_exp2_stop_and_wait_window_and_claim():
    df = load("exp2_goodput_vs_window")
    avg = df.groupby(["protocol", "window"])["goodput_bps"].mean()

    sw_rows = df[df["protocol"] == "sw"]
    assert set(sw_rows["window"]) == {1}

    for window in (2, 4, 8, 16, 32):
        assert avg["gbn", window] > avg["sw", 1]
        assert avg["sr", window] > avg["sw", 1]


def test_exp3_reordering_retransmissions():
    df = load("exp3_retx_vs_reorder")
    avg = df.groupby(["protocol", "reorder"])["retransmissions"].mean()

    for reorder in df["reorder"].unique():
        assert avg["gbn", reorder] > avg["sr", reorder]


def test_exp4_all_rto_variants_complete():
    df = load("exp4_rto_variants")
    assert len(df) == 100

    totals = df.groupby(["rto", "reset_backoff", "loss"]).size()
    assert totals.sum() == 100
    assert (totals == 5).all()
