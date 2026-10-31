import unittest

from diagnose_x import technical_codes


class PrivateDiagnosis(unittest.TestCase):
    def test_only_known_technical_codes_leave_report(self):
        report = {"results": {"completionReason": "budget_limited", "realRows": 17,
                             "stopCauses": [{"code": "search_unavailable", "message": "private body"}]},
                  "userPricingTier": "private account value"}
        self.assertEqual(technical_codes(report, [], 100),
                         ["SOURCE_BUDGET_LIMITED", "SOURCE_SEARCH_UNAVAILABLE"])

    def test_arbitrary_report_values_never_become_output(self):
        report = {"results": {"completionReason": "private-secret-value",
                             "stopCauses": [{"code": "private account value"}]}}
        self.assertEqual(technical_codes(report, [], 100), ["SOURCE_UNCLASSIFIED"])

    def test_row_values_are_not_echoed_on_schema_failure(self):
        row = {"id": "123456789", "text": "private post", "author": "private author"}
        self.assertEqual(technical_codes({}, [row], 100), ["IMPORT_AUTHOR_SHAPE"])


if __name__ == "__main__":
    unittest.main()
