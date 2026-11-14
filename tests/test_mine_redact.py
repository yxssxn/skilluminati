"""Unit tests for skilluminati.redact. Stdlib unittest only. All names and values are invented.

Run: python -m pytest tests/test_mine_redact.py -v
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REDACT_PATH = REPO / "plugin" / "skilluminati" / "redact.py"


def _load():
    spec = importlib.util.spec_from_file_location("skilluminati_redact", REDACT_PATH)
    assert spec is not None and spec.loader is not None, REDACT_PATH
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


redact_mod = _load()


def R(text, names=()):
    """Redact with an explicit (possibly empty) deny-list so the real names file is never read."""
    return redact_mod.redact(text, names=list(names))


class EmailTests(unittest.TestCase):
    def test_positive(self):
        out, c = R("mail avery.quill+x@ondine-pty.example.au now")
        self.assertEqual(out, "mail [EMAIL] now")
        self.assertEqual(c["EMAIL"], 1)

    def test_negative(self):
        for s in ("decorator @dataclass here", "user@localhost", "price@2 each", "git@github"):
            out, c = R(s)
            self.assertEqual(out, s, s)
            self.assertEqual(c["EMAIL"], 0, s)


class PhoneTests(unittest.TestCase):
    def test_positive_au(self):
        for s in ("0412 345 678", "0412345678", "(02) 9876 5432", "02 9876 5432", "+61 412 345 678",
                  "+61 2 9876 5432", "+61412345678", "1300 123 456", "1800123456", "13 11 14"):
            out, c = R(f"call {s} today")
            self.assertEqual(out, "call [PHONE] today", s)
            self.assertEqual(c["PHONE"], 1, s)

    def test_positive_international(self):
        for s in ("+44 20 7946 0958", "+1 (415) 555-0132", "+65 6123 4567", "+33 1 23 45 67 89"):
            out, c = R(f"call {s} today")
            self.assertEqual(out, "call [PHONE] today", s)

    def test_negative(self):
        for s in ("ABN 12 345 678 901", "the year 2026", "port 8080", "build 20261107", "v0.4.1",
                  "order 1234567", "1,234,567 rows", "ISO 20261107T190000", "0x1f", "A4 paper"):
            out, c = R(s)
            self.assertEqual(out, s, s)
            self.assertEqual(c["PHONE"], 0, s)


class UrlTests(unittest.TestCase):
    def test_query_and_fragment_redacted(self):
        for s in ("https://example.com/cb?token=abc123", "http://example.com/a?x=1&y=2",
                  "https://example.com/doc#access_token=zzz", "wss://example.com/ws?key=k"):
            out, c = R(f"see {s} ok")
            self.assertEqual(out, "see [URL] ok", s)
            self.assertEqual(c["URL"], 1, s)

    def test_plain_urls_kept(self):
        for s in ("https://example.com", "https://example.com/docs/page.html", "http://localhost:8080/health",
                  "ftp://files.example.com/pub/"):
            out, c = R(f"see {s} ok")
            self.assertEqual(out, f"see {s} ok", s)
            self.assertEqual(c["URL"], 0, s)

    def test_not_a_url(self):
        out, c = R("ratio 3://4 and the string http:// alone")
        self.assertEqual(c["URL"], 0)


class KeyTests(unittest.TestCase):
    def test_positive(self):
        keys = [
            "sk" "-abc123def456ghi789jkl012mno345pq",
            "sk" "-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789",
            "sk" "-proj-abcdefghijklmnop1234567890",
            "gh" "p_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            "gi" "thub_pat_11ABCDEFG0abcdefghijklmnopqrstuvwxyz",
            "xo" "xb-1234567890-abcdefghijklmn",
            "xo" "xp-1234567890-1234567890-abcdefgh",
            "AK" "IAIOSFODNN7EXAMPLE",
            "AI" "za" "SyA1234567890abcdefghijklmnopqrstuv",
            "01" "23456789abcdef0123456789abcdef",                      # 32 hex
            "3f" "2a" "1b4c5d6e7f8091a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0",  # long hex
            "dG" "hp" "cyBpcyBhIHRlc3Qgb2YgYmFzZTY0IGVuY29kaW5nQUJD==",     # base64 blob
            "Be" "arer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abc",
        ]
        for k in keys:
            out, c = R(f"key {k} end")
            self.assertEqual(out, "key [KEY] end", k)
            self.assertEqual(c["KEY"], 1, k)

    def test_negative(self):
        for s in ("sk-short", "ghp_short", "AKIA1234", "deadbeefdeadbeef",                 # too short
                  "abcdefghijklmnopqrstuvwxyzabcdefghijklmnop",                             # letters only
                  "12345678901234567890123456789012345",                                     # digits only
                  "ask-the-team-about-the-risk-assessment-matrix-now",                      # hyphenated words
                  "/opt/someone/dev/some/really/long/path/to/a/file/name.py",               # path
                  "snake_case_identifier_that_is_quite_long_indeed_yes"):                   # identifier
            out, c = R(s)
            self.assertEqual(out, s, s)
            self.assertEqual(c["KEY"], 0, s)

    def test_key_inside_url_is_one_url(self):
        out, c = R("go https://example.com/auth?token=sk" "-abc123def456ghi789jkl012mno345pq now")
        self.assertEqual(out, "go [URL] now")
        self.assertEqual(c["KEY"], 0, "swallowed by the URL, not double counted")
        self.assertEqual(c["URL"], 1)


class MoneyTests(unittest.TestCase):
    def test_positive(self):
        for s in ("$5", "$1,200.50", "$ 300", "A$5k", "AU$12,000", "US$1.5m", "$2.5M", "$40K",
                  "AUD 2,000", "USD 1.5m", "AUD2500", "2,000 AUD", "500 EUR", "1.2k USD", "$0.99"):
            out, c = R(f"fee {s} ex GST")
            self.assertEqual(out, "fee [MONEY] ex GST", s)
            self.assertEqual(c["MONEY"], 1, s)

    def test_negative(self):
        for s in ("$HOME", "$PATH/bin", "version 2.0", "10 items", "port 8080", "5k run",
                  "USD rates vary", "AUDIO file", "the $variable", "2026-11-07"):
            out, c = R(s)
            self.assertEqual(out, s, s)
            self.assertEqual(c["MONEY"], 0, s)


class NameTests(unittest.TestCase):
    def test_deny_list_case_insensitive_and_whole_word(self):
        out, c = R("Met AVERY quill and Ondine Pty Ltd re the quillery.", names=["Avery Quill", "Ondine Pty Ltd"])
        self.assertEqual(out, "Met [NAME] and [NAME] re the quillery.")
        self.assertEqual(c["NAME"], 2)

    def test_deny_list_longest_first(self):
        out, c = R("Avery Quill-Barrow and Avery Quill", names=["Avery Quill", "Avery Quill-Barrow"])
        self.assertEqual(out, "[NAME] and [NAME]")

    def test_greeting_and_signoff_heuristic(self):
        cases = {
            "Hi Avery, quick one.": "Hi [NAME], quick one.",
            "Hello Avery Quill": "Hello [NAME]",
            "Dear Ondine,": "Dear [NAME],",
            "Thanks, Avery": "Thanks, [NAME]",
            "Thanks Avery!": "Thanks [NAME]!",
            "Kind regards, Avery Quill": "Kind regards, [NAME]",
            "Cheers,\nAvery": "Cheers,\n[NAME]",
            "Regards Avery.": "Regards [NAME].",
        }
        for src, want in cases.items():
            out, c = R(src)
            self.assertEqual(out, want, src)
            self.assertEqual(c["NAME"], 1, src)

    def test_greeting_negatives(self):
        for s in ("Hi there", "Thanks all", "Hi Monday morning", "Thanks again", "Best wishes for the week",
                  "Hello everyone", "Hi, how is the build", "thanks for the help", "Regards to the team",
                  "Dear Sir", "Dear Madam", "Hi Hermes", "Hi Support team"):
            out, c = R(s)
            self.assertEqual(c["NAME"], 0, s)
            self.assertEqual(out, s, s)

    def test_names_file_absent_is_fine(self):
        with tempfile.TemporaryDirectory() as d:
            out, c = redact_mod.redact("Hi Avery", names_file=Path(d) / "missing.txt")
            self.assertEqual(out, "Hi [NAME]")
            self.assertEqual(redact_mod.load_names(Path(d) / "missing.txt"), [])

    def test_names_file_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "names.txt"
            f.write_text("# comment\nAvery Quill\n\n  Ondine Pty Ltd  \n")
            self.assertEqual(redact_mod.load_names(f), ["Ondine Pty Ltd", "Avery Quill"])
            out, c = redact_mod.redact("ondine pty ltd owes avery quill", names_file=f)
            self.assertEqual(out, "[NAME] owes [NAME]")


class CombinedTests(unittest.TestCase):
    def test_none_passthrough(self):
        out, c = R(None)
        self.assertIsNone(out)
        self.assertEqual(sum(c.values()), 0)

    def test_all_types_once(self):
        text = ("Hi Avery, invoice $1,200 to avery@ondine.example, call 0412 345 678, "
                "token gh" "p_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789, portal https://example.com/p?sid=9. "
                "Thanks, Avery Quill")
        out, c = R(text, names=["Avery Quill"])
        for ph in ("[EMAIL]", "[PHONE]", "[KEY]", "[URL]", "[MONEY]", "[NAME]"):
            self.assertIn(ph, out, ph)
        for raw in ("avery@", "0412", "ghp_", "sid=9", "$1,200", "Avery"):
            self.assertNotIn(raw, out, raw)
        self.assertEqual(c, {"KEY": 1, "URL": 1, "EMAIL": 1, "PHONE": 1, "MONEY": 1, "PATH": 0, "NAME": 2})

    def test_merge_counts(self):
        a = {"KEY": 1, "URL": 0, "EMAIL": 2, "PHONE": 0, "MONEY": 0, "NAME": 0}
        b = {"KEY": 1, "URL": 1, "EMAIL": 0, "PHONE": 0, "MONEY": 3, "NAME": 1}
        self.assertEqual(redact_mod.merge_counts(a, b), {"KEY": 2, "URL": 1, "EMAIL": 2, "PHONE": 0, "MONEY": 3, "PATH": 0, "NAME": 1})

    def test_idempotent(self):
        text = "Hi Avery, $5k, 0412 345 678, a@b.example"
        once, _ = R(text)
        twice, c = R(once)
        self.assertEqual(once, twice)
        self.assertEqual(sum(c.values()), 0)


if __name__ == "__main__":
    unittest.main()
