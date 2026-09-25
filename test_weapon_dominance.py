import pytest
import weapon_dominance as wd


def make_card(name="TestCard", group="TestGroup", init=5, mv=0,
              attacks=None, blocks=None, text="", persistence="0", extra=None):
    row = {
        "Name": name,
        "Group": group,
        "Initiative": str(init),
        "Movement": str(mv),
        "HighAttack": "0", "MidAttack": "0", "LowAttack": "0",
        "HighRange": "0", "MidRange": "0", "LowRange": "0",
        "HighBlock": "0", "MidBlock": "0", "LowBlock": "0",
        "HighDType": "cut", "MidDType": "cut", "LowDType": "cut",
        "Text": text,
        "Persistence": persistence,
    }
    if attacks:
        for z, (atk, rng) in attacks.items():
            row[f"{z}Attack"] = str(atk)
            row[f"{z}Range"] = str(rng)
    if blocks:
        for z, blk in blocks.items():
            row[f"{z}Block"] = str(blk)
    if extra:
        for k, v in extra.items():
            row[k] = str(v)
    return wd.Card(row)


class TestAbilityParsing:
    def test_parses_detriments(self):
        c_feint = make_card(text=r"\fullfeint")
        assert c_feint.feint
        assert not c_feint.reload
        assert not c_feint.committed
        assert c_feint.penalties() == {"feint"}

        c_reload = make_card(text=r"\fullreload")
        assert c_reload.reload
        assert c_reload.penalties() == {"reload"}

        c_committed = make_card(text=r"\fullcommitted")
        assert c_committed.committed
        assert c_committed.penalties() == {"committed"}

    def test_parses_improvements(self):
        c_gb = make_card(text=r"\fullguardbreak")
        assert c_gb.guardbreak
        assert not c_gb.closequarters
        assert c_gb.bonuses() == {"guard break"}

        c_cq = make_card(text=r"\fullclosequarters")
        assert c_cq.closequarters
        assert c_cq.bonuses() == {"close quarters"}

        c_hit = make_card(text=r"On Hit: target gets 2 \fullstunned")
        assert c_hit.on_hit["stunned"] == 2
        assert c_hit.bonuses() == {"stunned on hit"}

    def test_strips_recognized_leaving_residual(self):
        c = make_card(text=r"\fullguardbreak \\ Can only be used by J7R-Salryman")
        assert c.guardbreak
        assert c.rem_text == "Can only be used by J7R-Salryman"

        c2 = make_card(text=r"\guardbreak \\ On Hit: target gets 2 \fullstunned \\ \fullreload")
        assert c2.guardbreak
        assert c2.reload
        assert c2.on_hit["stunned"] == 2
        assert c2.rem_text == ""


class TestDominanceLogic:
    def test_identical_stats_feint_vs_no_feint(self):
        a = make_card(name="A", group="G1", init=5, mv=0, attacks={"High": (2, 0)}, text="")
        b = make_card(name="B", group="G2", init=5, mv=0, attacks={"High": (2, 0)}, text=r"\fullfeint")

        assert wd.comparable(a, b)
        wins_a = wd.dominates(a, b)
        assert wins_a is not None
        assert ("no feint", 1) in wins_a
        assert wd.describe_wins(wins_a) == "no feint"

        # B cannot dominate A
        assert wd.dominates(b, a) is None

    def test_identical_stats_reload_vs_no_reload(self):
        a = make_card(name="A", group="G1", init=5, mv=0, text="", persistence="0")
        b = make_card(name="B", group="G2", init=5, mv=0, text=r"\fullreload", persistence=r"\infty")

        assert wd.comparable(a, b)
        wins_a = wd.dominates(a, b)
        assert wins_a is not None
        assert ("no reload", 1) in wins_a
        assert wd.describe_wins(wins_a) == "no reload"
        assert wd.dominates(b, a) is None

    def test_identical_stats_committed_vs_no_committed(self):
        a = make_card(name="A", group="G1", init=5, mv=0, text="")
        b = make_card(name="B", group="G2", init=5, mv=0, text=r"\fullcommitted")

        wins_a = wd.dominates(a, b)
        assert wins_a is not None
        assert ("no committed", 1) in wins_a
        assert wd.describe_wins(wins_a) == "no committed"
        assert wd.dominates(b, a) is None

    def test_identical_stats_guardbreak_vs_plain(self):
        a = make_card(name="A", group="G1", init=5, mv=0, text=r"\fullguardbreak")
        b = make_card(name="B", group="G2", init=5, mv=0, text="")

        wins_a = wd.dominates(a, b)
        assert wins_a is not None
        assert ("guard break", 1) in wins_a
        assert wd.describe_wins(wins_a) == "guard break"
        assert wd.dominates(b, a) is None

    def test_identical_stats_on_hit_status_vs_plain(self):
        a = make_card(name="A", group="G1", init=5, mv=0, text=r"On Hit: target gets 2 \fullslowed")
        b = make_card(name="B", group="G2", init=5, mv=0, text="")

        wins_a = wd.dominates(a, b)
        assert wins_a is not None
        assert ("slowed on hit", 2) in wins_a
        assert wd.describe_wins(wins_a) == "+2 slowed on hit"
        assert wd.dominates(b, a) is None

    def test_higher_status_dominates_lower_status(self):
        a = make_card(name="A", group="G1", init=5, mv=0, text=r"On Hit: target gets 2 \fullstunned")
        b = make_card(name="B", group="G2", init=5, mv=0, text=r"On Hit: target gets 1 \fullstunned")

        wins_a = wd.dominates(a, b)
        assert wins_a is not None
        assert ("stunned on hit", 1) in wins_a
        assert wd.describe_wins(wins_a) == "+1 stunned on hit"
        assert wd.dominates(b, a) is None

    def test_bonus_vs_penalty_is_double_win(self):
        # A has Guard Break (bonus), B has Reload (penalty)
        a = make_card(name="A", group="G1", init=5, mv=0, text=r"\fullguardbreak", persistence="0")
        b = make_card(name="B", group="G2", init=5, mv=0, text=r"\fullreload", persistence=r"\infty")

        assert wd.comparable(a, b)
        wins_a = wd.dominates(a, b)
        assert wins_a is not None
        desc = wd.describe_wins(wins_a)
        assert "guard break" in desc
        assert "no reload" in desc
        assert wd.dominates(b, a) is None

    def test_different_penalties_are_incomparable(self):
        # Feint vs Committed
        a = make_card(name="A", group="G1", init=5, mv=0, text=r"\fullfeint")
        b = make_card(name="B", group="G2", init=5, mv=0, text=r"\fullcommitted")

        assert not wd.comparable(a, b)
        assert wd.dominates(a, b) is None
        assert wd.dominates(b, a) is None

    def test_different_bonuses_are_incomparable(self):
        # Guard break vs On Hit stunned
        a = make_card(name="A", group="G1", init=5, mv=0, text=r"\fullguardbreak")
        b = make_card(name="B", group="G2", init=5, mv=0, text=r"On Hit: target gets 2 \fullstunned")

        assert not wd.comparable(a, b)
        assert wd.dominates(a, b) is None
        assert wd.dominates(b, a) is None

        # Stunned vs Slowed
        c = make_card(name="C", group="G3", init=5, mv=0, text=r"On Hit: target gets 2 \fullslowed")
        assert not wd.comparable(b, c)
        assert wd.dominates(b, c) is None

    def test_same_penalty_compares_on_stats(self):
        # Both have Reload, A has higher initiative
        a = make_card(name="A", group="G1", init=6, mv=0, text=r"\fullreload")
        b = make_card(name="B", group="G2", init=4, mv=0, text=r"\fullreload")

        assert wd.comparable(a, b)
        wins_a = wd.dominates(a, b)
        assert wins_a is not None
        assert wd.describe_wins(wins_a) == "+2 init"

    def test_different_residual_text_incomparable(self):
        a = make_card(name="A", group="G1", init=5, mv=0, text=r"Hits all adjacent enemies")
        b = make_card(name="B", group="G2", init=5, mv=0, text="")

        assert not wd.comparable(a, b)
        assert wd.dominates(a, b) is None

    def test_persistence_ignored_for_comparison(self):
        a = make_card(name="A", group="G1", init=5, mv=0, text="", persistence="0")
        b = make_card(name="B", group="G2", init=4, mv=0, text="", persistence="2")

        assert wd.comparable(a, b)
        wins = wd.dominates(a, b)
        assert wins is not None
        assert wd.describe_wins(wins) == "+1 init"


class TestLiveCsv:
    def test_weapon_actions_cross_clusters(self):
        cards = wd.load_cards("Weapon actions.csv")
        clusters = wd.check_cross(cards)
        assert len(clusters) == 5

        # Check cluster 1: Assault Rifle From the hip loses to Stun Bow Straight shot & Low blow
        c1 = [c for c in clusters if "Assault Rifle" in c["groups"] and "Stun Bow" in c["groups"] and len(c["groups"]) == 2][0]
        fth = [card for card in c1["cards"] if card.name == "From the hip"][0]
        assert id(fth) in c1["beats"]

        # Check Spear Sweep vs Spear Thrust in within
        within = wd.check_within(cards)
        spear_set = [w for w in within if w[0] == "Spear"][0]
        assert len(spear_set[2]) == 1  # 1 beaten pair
        a, b, ws = spear_set[2][0]
        assert a.name == "Sweep"
        assert b.name == "Thrust"
        assert "slowed on hit" in wd.describe_wins(ws)
