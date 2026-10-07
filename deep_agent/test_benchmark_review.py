"""Offline regression checks for score provenance and failure handling."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest

from benchmark_review import (
    WEB_RUBRICS, final_citations, invoke_web_with_citation_check,
    official_company_url, review_report,
)


class FakeGrader:
    model_name = "test-grader"

    def __init__(self, failed_criterion=None, error=None):
        self.called = False
        self.failed_criterion = failed_criterion
        self.error = error

    def with_structured_output(self, *args, **kwargs):
        self.called = True
        return self

    async def ainvoke(self, messages):
        if self.error:
            raise self.error
        return {
            "parsed": {
                name: {"passed": name != self.failed_criterion, "evidence": "Test evidence"}
                for name in WEB_RUBRICS["web_native_001"]
            },
            "raw": SimpleNamespace(usage_metadata={"total_tokens": 7}),
        }


def web_record():
    return {
        "case_id": "web_native_001", "run_id": "new-run",
        "runtime_status": "completed", "task_passed": None,
        "answer": "Candidate answer", "latency_s": 8,
        "checks": {name: True for name in [
            "web_search_observed", "web_fetch_observed", "citations_observed",
            "no_other_tool_calls", "no_invalid_tool_calls", "final_answer_complete",
        ]},
        "opened_urls": ["https://www.microsoft.com/investor/reports/ar24/"],
        "citations": [{"url": "https://www.microsoft.com/investor/reports/ar24/"}],
        "review": {"run_id": "old-run", "task_passed": True},
    }


class ReviewTests(unittest.TestCase):
    def test_new_review_is_bound_to_current_run_without_mutating_original(self):
        record = web_record()
        before = deepcopy(record)
        graded = asyncio.run(review_report(record, FakeGrader()))
        self.assertEqual(record, before)
        self.assertTrue(graded["task_passed"])
        self.assertEqual(graded["review"]["run_id"], "new-run")
        self.assertEqual(graded["latency_s"], 8)
        self.assertIn("usage", graded["review"])

    def test_content_failure_cannot_become_pass(self):
        graded = asyncio.run(review_report(web_record(), FakeGrader("annual_revenues")))
        self.assertFalse(graded["task_passed"])
        self.assertEqual(graded["evaluation_status"], "failed")

    def test_grader_error_remains_pending(self):
        graded = asyncio.run(review_report(web_record(), FakeGrader(error=TimeoutError())))
        self.assertIsNone(graded["task_passed"])
        self.assertEqual(graded["evaluation_status"], "pending_review")

    def test_runtime_failure_skips_grader(self):
        record = web_record()
        record["runtime_status"] = "failed"
        grader = FakeGrader()
        graded = asyncio.run(review_report(record, grader))
        self.assertFalse(grader.called)
        self.assertFalse(graded["task_passed"])
        self.assertEqual(graded["evaluation_status"], "runtime_error")

    def test_web002_requires_both_companies(self):
        record = web_record()
        record["case_id"] = "web_native_002"
        grader = FakeGrader()
        graded = asyncio.run(review_report(record, grader))
        self.assertFalse(grader.called)
        self.assertFalse(graded["checks"]["opened_official_alphabet_source"])
        self.assertFalse(graded["task_passed"])

    def test_missing_workflow_evidence_cannot_pass(self):
        record = web_record()
        record["checks"] = {}
        grader = FakeGrader()
        graded = asyncio.run(review_report(record, grader))
        self.assertFalse(grader.called)
        self.assertFalse(graded["task_passed"])

    def test_citation_to_different_year_cannot_pass(self):
        record = web_record()
        record["citations"] = [{"url": "https://www.microsoft.com/investor/reports/ar23/index.html"}]
        grader = FakeGrader()
        graded = asyncio.run(review_report(record, grader))
        self.assertFalse(grader.called)
        self.assertFalse(graded["checks"]["cited_opened_microsoft_source"])

    def test_directory_index_and_tracking_query_are_same_page(self):
        record = web_record()
        record["citations"] = [{"url": "https://www.microsoft.com/investor/reports/ar24/index.html?utm_source=test"}]
        graded = asyncio.run(review_report(record, FakeGrader()))
        self.assertTrue(graded["task_passed"])

    def test_company_urls_reject_lookalikes_and_wrong_sec_company(self):
        self.assertFalse(official_company_url("https://microsoft.com.fake.test/ar24", "Microsoft"))
        self.assertFalse(official_company_url("https://www.sec.gov/Archives/edgar/data/789019/report.htm", "Alphabet"))
        self.assertTrue(official_company_url("https://www.sec.gov/Archives/edgar/data/1652044/report.htm", "Alphabet"))


class CitationRepairTests(unittest.TestCase):
    def test_wrong_year_citation_gets_one_repair_with_original_context(self):
        opened = "https://www.microsoft.com/investor/reports/ar24/"
        wrong = "https://www.microsoft.com/investor/reports/ar23/"
        draft = SimpleNamespace(type="ai", text=f"[Source]({wrong})", content_blocks=[
            {"type": "server_tool_call", "name": "web_search", "args": {"type": "open_page", "url": opened}},
        ])
        corrected = SimpleNamespace(type="ai", text=f"[Source]({opened})", content_blocks=[])

        class Agent:
            calls = []

            async def ainvoke(self, payload, config):
                self.calls.append(payload)
                if len(self.calls) == 1:
                    return {"messages": [draft]}
                return {"messages": [*payload["messages"], corrected]}

        agent = Agent()
        result = asyncio.run(invoke_web_with_citation_check(agent, "Find annual revenues", config={}))
        self.assertEqual(len(agent.calls), 2)
        self.assertIs(agent.calls[1]["messages"][0], draft)
        self.assertIn(opened, agent.calls[1]["messages"][-1]["content"])
        self.assertEqual(result["citation_repairs"][0]["draft_answer"], draft.text)
        self.assertEqual([c["url"] for c in final_citations(result["messages"][-1])], [opened])

    def test_repair_limit_does_not_loop_until_pass(self):
        class Agent:
            count = 0

            async def ainvoke(self, payload, config):
                self.count += 1
                return {"messages": [SimpleNamespace(type="ai", text="No sources", content_blocks=[])]}

        agent = Agent()
        result = asyncio.run(invoke_web_with_citation_check(agent, "Research", config={}))
        self.assertEqual(agent.count, 2)
        self.assertEqual(len(result["citation_repairs"]), 1)
        self.assertEqual(final_citations(result["messages"][-1]), [])


if __name__ == "__main__":
    unittest.main()
