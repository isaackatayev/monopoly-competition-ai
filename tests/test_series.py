import unittest
from arena import example
from series_player import competition_player


class SeriesTests(unittest.TestCase):
    def setUp(self):
        self.player = competition_player("adaptive-v3",True)

    def packet(self,game,seat,**extra):
        p=example("buy")
        return {**p,"game_id":game,"player":seat,"opponent_id":"league-rival",**extra}

    def test_results_apply_once_across_seat_changes(self):
        self.player.choose(self.packet("one",0))
        second=self.packet("two",1,previous_game_id="one",previous_winner=0)
        reply=self.player.choose(second)
        self.assertEqual(self.player.games["two",1].evidence,((1.,0.),(0.,0.),(0.,0.)))
        self.assertEqual(reply,self.player.choose(second))
        self.player.choose({**second,"request_id":2})
        self.assertEqual(self.player.games["two",1].evidence,((1.,0.),(0.,0.),(0.,0.)))
        with self.assertRaises(ValueError):
            self.player.choose({**second,"request_id":3,"previous_winner":1})
        with self.assertRaises(ValueError):
            self.player.choose(self.packet("another",1,previous_game_id="one",previous_winner=1))

    def test_unlinked_games_and_unknown_opponents_start_fresh(self):
        self.player.choose(self.packet(10,0))
        self.player.choose(self.packet(11,0,previous_game_id=10,previous_winner=None))
        entered=self.player.games[11,0]
        self.assertEqual(entered.evidence,((0,1.),(0,0),(0,0)))
        for game,extra in ((12,{}),(13,dict(previous_game_id=11,previous_winner=0,opponent_id="new"))):
            self.player.choose(self.packet(game,0,**extra))
            self.assertEqual(self.player.games[game,0].profile,0)
            self.assertEqual(self.player.games[game,0].evidence,((0,0),(0,0),(0,0)))

    def test_reject_invalid_result_and_decision_without_learning(self):
        self.player.choose(self.packet(1,0))
        for winner in (False,-1,2,"1"):
            with self.assertRaises(ValueError):
                self.player.choose(self.packet(2,1,previous_game_id=1,previous_winner=winner))
        with self.assertRaises((KeyError,ValueError)):
            self.player.choose(self.packet(2,1,previous_game_id=1,previous_winner=0,state={}))
        self.assertFalse(self.player.games[1,0].result)
        self.assertNotIn((2,1),self.player.games)
