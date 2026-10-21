from django.test import SimpleTestCase

from apps.sharing.policy import evaluate_share_policy
from apps.sharing.risk import assess_share
from apps.vault.models import Material


class RiskEngineTests(SimpleTestCase):
    def test_representative_scores_and_levels(self):
        cases = [
            (("group", "view", False), 20, "low"),
            (("group", "view", True), 32, "low"),
            (("group", "edit", False), 45, "medium"),
            (("group", "edit", True), 57, "medium"),
            (("public", "view", False), 45, "medium"),
            (("public", "edit", True), 82, "high"),
            (("only_me", "view", False), 5, "low"),
        ]
        for args, score, level in cases:
            r = assess_share(*args)
            self.assertEqual((r.score, r.level), (score, level), args)

    def test_recommendation_is_least_privilege_and_absent_when_already_safe(self):
        r = assess_share("public", "edit", True)
        self.assertEqual(r.recommended, {"audience": "group", "permission": "view", "allow_download": False})
        self.assertIsNone(assess_share("group", "view", False).recommended)

    def test_invalid_input_raises(self):
        for args in (("world", "view", False), ("group", "admin", False), ("group", "view", "yes"),
                     ("group", "view", 1), (None, "view", False)):
            with self.assertRaises(ValueError, msg=args):
                assess_share(*args)


class SharingPolicyTests(SimpleTestCase):
    def test_complete_policy_matrix(self):
        expected = {
            Material.Sensitivity.PUBLIC: [(True, False), (True, False), (True, True), (True, True)],
            Material.Sensitivity.INTERNAL: [(True, False), (True, True), (True, True), (True, True)],
            Material.Sensitivity.CONFIDENTIAL: [(True, True), (False, False), (False, False), (False, False)],
            Material.Sensitivity.HIGHLY_CONFIDENTIAL: [(False, False)] * 4,
        }
        configs = [("view", False), ("view", True), ("edit", False), ("edit", True)]
        for sensitivity, outcomes in expected.items():
            for config, outcome in zip(configs, outcomes):
                with self.subTest(sensitivity=sensitivity, config=config):
                    decision = evaluate_share_policy(sensitivity, *config)
                    self.assertEqual((decision.allowed, decision.requires_confirmation), outcome)

    def test_unclassified_and_invalid_values_fail_closed(self):
        self.assertFalse(evaluate_share_policy(None, "view", False).allowed)
        for args in (("confidential", "admin", False), ("internal", "view", "false")):
            with self.assertRaises(ValueError):
                evaluate_share_policy(*args)
