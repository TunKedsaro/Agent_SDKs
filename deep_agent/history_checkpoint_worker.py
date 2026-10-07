"""Resume history_003 in a fresh process; only SQLite supplies agent history."""
import argparse
import asyncio

async def main(manifest_filename, report_filename):
    import asyncio
    import json
    import math
    import os
    import time

    from pathlib import Path
    from datetime import datetime, timezone
    from importlib.metadata import version
    from uuid import uuid4

    from langchain_openai import ChatOpenAI
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from deepagents import create_deep_agent

    manifest_path = Path(manifest_filename)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    db_path = Path(manifest["db_path"])

    assert manifest["case_id"] == "history_003"
    assert db_path.is_file(), "ไม่พบฐานข้อมูล checkpoint"

    pid_before = manifest["process_id_before"]
    pid_after = os.getpid()

    print("PID before:", pid_before)
    print("PID after:", pid_after)

    assert pid_after != pid_before, (
        "PID ยังเหมือนเดิม ยังยืนยันการเปลี่ยน process ไม่ได้ "
        "ต้องใช้ Python process ใหม่"
    )

    versions_after = {
        name: version(name)
        for name in manifest["versions_before_restart"]
    }

    assert versions_after == manifest["versions_before_restart"], (
        "เวอร์ชันแพ็กเกจเปลี่ยนระหว่างการทดสอบ"
    )

    print("New process verified.")
    print("Package versions unchanged.")
    print("Thread:", manifest["thread_id"])
    assert os.environ.get("OPENAI_API_KEY"), (
        "ยังไม่มี OPENAI_API_KEY ใน kernel นี้ "
        "ให้โหลด environment ด้วยวิธีเดิมก่อน"
    )

    restored_model = ChatOpenAI(
        model=manifest["model"],
        use_responses_api=True,
        timeout=60,
        max_retries=3,
    )

    print("Model:", restored_model.model_name)
    resume_prompt = """
    จากข้อมูลที่ให้ไว้ก่อนหน้านี้ในบทสนทนานี้
    ตอบ JSON object เท่านั้น โดยมี keys:
    company, year, revenue_million, net_profit_million,
    net_margin_pct, reference_code

    ใช้ข้อมูลและรหัสอ้างอิงจากประวัติเดิม
    ห้ามเดา ห้ามเพิ่ม keys และห้ามใช้ tools
    หากไม่พบข้อมูลของ field ใด ให้ใช้ null
    """

    resume_config = {
        "configurable": {"thread_id": manifest["thread_id"]},
        "recursion_limit": 20,
    }

    resume_result = None
    resume_error = None
    resume_status = "running"
    restored_messages = []
    resume_agent_s = None

    resume_started_at = time.perf_counter()

    try:
        async with AsyncSqliteSaver.from_conn_string(
            str(db_path)
        ) as saver:
            restored_agent = create_deep_agent(
                model=restored_model,
                tools=[],
                system_prompt=manifest["system_prompt"],
                checkpointer=saver,
            )

            snapshot = await restored_agent.aget_state(resume_config)
            restored_messages = snapshot.values.get("messages", [])

            restored_ids = {message.id for message in restored_messages}
            seed_ids = set(manifest["seed_message_ids"])

            assert seed_ids and None not in seed_ids
            assert seed_ids.issubset(restored_ids), (
                "กู้คืนข้อความเดิมจาก checkpoint ได้ไม่ครบ"
            )
            assert not snapshot.next, "Checkpoint มีงานที่ยังไม่จบ"

            print("Messages restored before model call:", len(restored_messages))

            agent_started_at = time.perf_counter()
            try:
                resume_result = await asyncio.wait_for(
                    restored_agent.ainvoke(
                        {
                            "messages": [
                                {"role": "user", "content": resume_prompt}
                            ]
                        },
                        config=resume_config,
                    ),
                    timeout=120,
                )
            finally:
                resume_agent_s = time.perf_counter() - agent_started_at

        resume_status = "completed"

    except Exception as exc:
        resume_status = "failed"
        resume_error = {
            "type": type(exc).__name__,
            "message": str(exc),
        }

    finally:
        resume_latency_s = time.perf_counter() - resume_started_at

    print("Runtime status:", resume_status)
    print("Resume latency:", round(resume_latency_s, 2), "seconds")

    if resume_error:
        print("Error:", resume_error)
    resume_messages = (
        resume_result.get("messages", [])
        if resume_result is not None else []
    )

    resume_last = resume_messages[-1] if resume_messages else None
    resume_answer = (
        resume_last.text.strip()
        if isinstance(resume_last, AIMessage) else ""
    )

    try:
        resume_parsed = json.loads(resume_answer)
    except json.JSONDecodeError:
        resume_parsed = None

    expected_resume = {
        "company": "Vega",
        "year": 2025,
        "revenue_million": 3200,
        "net_profit_million": 480,
        "net_margin_pct": 15,
        "reference_code": "VEGA-73-KP",
    }

    actual_resume = (
        resume_parsed if isinstance(resume_parsed, dict) else {}
    )

    def resume_value_matches(key, expected):
        actual = actual_resume.get(key)

        if type(expected) is int:
            return (
                type(actual) in (int, float)
                and math.isfinite(actual)
                and actual == expected
            )

        return type(actual) is str and actual == expected

    value_checks = {
        key: resume_value_matches(key, expected)
        for key, expected in expected_resume.items()
    }

    restored_ids = {message.id for message in restored_messages}
    new_ai_messages = [
        message for message in resume_messages
        if isinstance(message, AIMessage)
        and message.id not in restored_ids
    ]

    resume_tool_calls = [
        call for message in new_ai_messages
        for call in message.tool_calls
    ]

    resume_checks = {
        "process_changed": pid_after != pid_before,
        "versions_unchanged": (
            versions_after == manifest["versions_before_restart"]
        ),
        "seed_messages_restored_before_call": (
            bool(manifest["seed_message_ids"])
            and set(manifest["seed_message_ids"]).issubset(restored_ids)
        ),
        "exact_json_structure": (
            isinstance(resume_parsed, dict)
            and set(resume_parsed) == set(expected_resume)
        ),
        "all_values_correct": all(value_checks.values()),
        "no_tool_calls": len(resume_tool_calls) == 0,
        "no_invalid_tool_calls": not any(
            message.invalid_tool_calls for message in new_ai_messages
        ),
        "final_answer_complete": (
            isinstance(resume_last, AIMessage)
            and bool(resume_answer)
            and not resume_last.tool_calls
        ),
    }

    history_003_report = {
        "case_id": "history_003",
        "run_id": manifest["run_id"],
        "sdk": "deepagents",
        "model": manifest["model"],
        "versions": versions_after,
        "versions_before_restart": manifest["versions_before_restart"],
        "runtime_status": resume_status,
        "error": resume_error,
        "thread_id": manifest["thread_id"],
        "configuration": {
            "checkpointer": "AsyncSqliteSaver",
            "restart_type": "fresh_python_process",
            "process_id_before": pid_before,
            "process_id_after": pid_after,
            "recursion_limit": 20,
            "timeout_s": 120,
        },
        "checks": resume_checks,
        "value_checks": value_checks,
        "task_passed": (
            resume_status == "completed"
            and all(resume_checks.values())
        ),
        "seed_latency_s": manifest["seed_latency_s"],
        "resume_latency_s": round(resume_latency_s, 2),
        "resume_agent_s": (
            round(resume_agent_s, 2)
            if resume_agent_s is not None else None
        ),
        # เวลาทำงานสองช่วง ไม่รวมเวลารอผู้ใช้ restart
        "latency_s": round(
            manifest["seed_latency_s"] + resume_latency_s, 2
        ),
        "system_prompt": manifest["system_prompt"],
        "resume_prompt": resume_prompt,
        "answer": resume_parsed,
        "raw_answer": resume_answer,
        "tool_call_count": len(resume_tool_calls),
        "seed_trace": manifest["seed_trace"],
        "restored_message_ids": [
            message.id for message in restored_messages
        ],
        "messages_after_resume": [
            message.model_dump(mode="json")
            for message in resume_messages
        ],
        "usage_after_restart": [
            message.usage_metadata for message in new_ai_messages
        ],
    }


    history_003_report["started_at_utc"] = manifest["prepared_at_utc"]
    Path(report_filename).write_text(json.dumps(history_003_report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.manifest, args.report))
