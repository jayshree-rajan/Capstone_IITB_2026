import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analystdesk import lessons, memory

L1 = {"when": "comparing companies with different fiscal year ends",
      "do": "state each company's period end next to its figures", "applies_to": ["comparison", "fiscal-year"],
      "source_case": "d09", "source_issue": "x"}
L2 = {"when": "a bank's margin is asked for", "do": "use pre-tax margin and say why",
      "applies_to": ["bank", "margin"], "source_case": "d04", "source_issue": "y"}


class PlaybookTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(memory, "MEMORY_DB", Path(tmp.name) / "m.sqlite")
        p.start()
        self.addCleanup(p.stop)

    def test_pending_lessons_are_not_used_until_approved(self):
        k1 = asyncio.run(lessons.add_pending(L1))
        self.assertEqual(asyncio.run(lessons.relevant("Compare Microsoft and Alphabet margins")), [])
        self.assertTrue(asyncio.run(lessons.review(k1, True, "Jayshree")))
        used = asyncio.run(lessons.relevant("Compare Microsoft and Alphabet margins, fiscal year ends differ"))
        self.assertEqual([u["key"] for u in used], [k1])

    def test_rejected_lesson_kept_with_reason(self):
        k2 = asyncio.run(lessons.add_pending(L2))
        asyncio.run(lessons.review(k2, False, "Jayshree", "too specific to one case"))
        rej = asyncio.run(lessons.list_lessons("rejected"))
        self.assertEqual(rej[0]["review_reason"], "too specific to one case")
        self.assertEqual(asyncio.run(lessons.list_lessons("approved")), [])

    def test_frozen_playbook_blocks_changes(self):
        k1 = asyncio.run(lessons.add_pending(L1))
        asyncio.run(lessons.set_frozen(True, "Jayshree"))
        self.assertFalse(asyncio.run(lessons.review(k1, True, "Jayshree")))
        self.assertIsNone(asyncio.run(lessons.add_pending(L2)))

    def test_only_relevant_lessons_retrieved(self):
        for les in (L1, L2):
            asyncio.run(lessons.review(asyncio.run(lessons.add_pending(les)), True, "J"))
        used = asyncio.run(lessons.relevant("What was JPMorgan's bank pre-tax margin?"))
        self.assertEqual(used[0]["applies_to"], ["bank", "margin"])


if __name__ == "__main__":
    unittest.main()
