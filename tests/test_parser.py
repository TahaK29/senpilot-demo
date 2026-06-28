import unittest

from app.parser import parse_email_request


class ParserTests(unittest.TestCase):
    def test_valid_request_with_prefixed_matter(self) -> None:
        parsed = parse_email_request("Can you give me Other Documents files from M12205?")
        self.assertEqual(parsed.matter_number, "M12205")
        self.assertEqual(parsed.document_type, "Other Documents")
        self.assertFalse(parsed.clarification_needed)

    def test_valid_request_with_numeric_matter(self) -> None:
        parsed = parse_email_request("Please send key docs for 12383")
        self.assertEqual(parsed.matter_number, "M12383")
        self.assertEqual(parsed.document_type, "Key Documents")
        self.assertFalse(parsed.clarification_needed)

    def test_missing_document_type_needs_clarification(self) -> None:
        parsed = parse_email_request("Please send the files for M12205")
        self.assertEqual(parsed.matter_number, "M12205")
        self.assertIsNone(parsed.document_type)
        self.assertTrue(parsed.clarification_needed)


if __name__ == "__main__":
    unittest.main()

