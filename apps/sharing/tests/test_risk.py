from django.test import SimpleTestCase

from apps.sharing.risk import assess_share


class RiskEngineTests(SimpleTestCase):
    def test_representative_scores_and_levels(self):
        cases = [
            (("group", "view", False), 20, "low", True, False),
            (("group", "view", True), 32, "low", True, False),
            (("group", "edit", False), 45, "medium", True, True),
            (("group", "edit", True), 57, "medium", True, True),
            (("public", "view", False), 45, "medium", False, False),   # public is always blocked
            (("public", "edit", True), 82, "high", False, False),      # the deck's headline example
            (("only_me", "view", False), 5, "low", True, False),
        ]
        for args, score, level, allowed, confirm in cases:
            r = assess_share(*args)
            self.assertEqual((r.score, r.level, r.allowed, r.requires_confirmation),
                             (score, level, allowed, confirm), args)

    def test_recommendation_is_least_privilege_and_absent_when_already_safe(self):
        r = assess_share("public", "edit", True)
        self.assertEqual(r.recommended, {"audience": "group", "permission": "view", "allow_download": False})
        self.assertIsNone(assess_share("group", "view", False).recommended)

    def test_invalid_input_raises(self):
        for args in (("world", "view", False), ("group", "admin", False), ("group", "view", "yes"),
                     ("group", "view", 1), (None, "view", False)):
            with self.assertRaises(ValueError, msg=args):
                assess_share(*args)
