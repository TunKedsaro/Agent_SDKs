# Pi Agent benchmark conversion

Notebook `pi_agent/pi_agents_benchmark.ipynb` ใช้โจทย์จาก
`deep_agent/2610051200_deepagents_benchmark.ipynb` เป็นหลัก ครบ **17 เคส + Smoke**
ใช้ Python 3 (ipykernel) เรียก Node ESM ผ่าน `tsx` และ
`@earendil-works/pi-coding-agent==0.87.1` ซึ่งเป็น **coding-agent harness**
โดยมี `pi-agent-core==0.87.1` และ `pi-ai==0.87.1` เป็น dependency ภายใน
ไม่ได้สลับไปสร้าง agent loop ด้วย Python หรือเรียก API ตรงแทน Pi

## เริ่มรัน

จาก repository root:

```sh
docker compose build pi-agent
docker compose up -d pi-agent
```

เปิด Jupyter ที่ `http://localhost:8889` (หรือ `PI_JUPYTER_PORT` ที่ตั้งไว้)
ใช้ token `pi-agent-local` ตาม Dockerfile เดิม เปิด
`pi_agent/pi_agents_benchmark.ipynb` แล้วเลือก **Python 3 (ipykernel)**
รันสองเซลล์ใน **0.Config** ก่อน Config ตรวจ import, เวอร์ชันและ model catalog
โดยไม่เรียกโมเดล จากนั้นรัน **1.Create Pi Agent → Smoke test → ตรวจผล → บันทึก**
แล้วจึงไปหมวดถัดไป เซลล์ tag `run` เรียกโมเดลจริงและมีค่าใช้จ่าย

อ่าน `OPENAI_API_KEY` จาก environment ที่ Compose ส่งให้ ไม่พิมพ์หรือเขียน key
และไม่แก้ `.env` ใช้ `BENCHMARK_MODEL` เดิม เช่น `openai:gpt-6-luna`
ถ้ารุ่นที่ติดตั้งไม่มี model ID นี้จะบันทึก runtime_error ไม่มี fallback
สำหรับ provider อื่น ต้องตั้ง `PI_BENCHMARK_API_KEY_ENV` ให้ชี้ชื่อ environment variable
ของ provider นั้น และ Pi ต้องมี model ID ที่ขออยู่จริง

`PI_BENCHMARK_THINKING` default เป็น `medium` บันทึกทั้งค่าที่ขอและค่าที่ Pi ใช้จริง
ต้นฉบับไม่ได้ตั้ง provider reasoning level อย่างชัดเจน จึงห้ามอ้างว่า reasoning budget เท่ากัน
หากใช้ local Python แทน Docker ต้องมี Node ที่รองรับ Pi, dependencies จาก `npm ci`
ใน `pi_agent`, และ `jupyterlab`, `ipykernel`, `fastmcp==4.0.11` ใน Python kernel นั้น

Compose เพิ่ม anonymous mount ที่ `/workspace/pi_agent/node_modules` เพื่อไม่ให้
node_modules บน macOS ทับ Linux dependencies; Node จะ resolve จาก `/workspace/node_modules`
ที่ image ติดตั้งด้วย `npm ci` ไม่ต้องติดตั้ง JavaScript kernel

## ไฟล์และขอบเขต

| ไฟล์ | หน้าที่ |
|---|---|
| `pi_agent/pi_agents_benchmark.ipynb` | Prompt, fixtures, prepare/run/score/export แยกเซลล์ และจุด restart |
| `pi_agent/benchmark_bridge.mjs` | Pi session, tool allowlist, MCP adapter, trace, usage และ JSON IPC |
| `pi_agent/benchmark_tools.py` | Business functions คัดจากต้นฉบับ, custom write_todos, Python IPC worker และ FastMCP fixture |
| `pi_agent/benchmark_runtime.py` | เรียก process, timeout, scoring, review, session manifest, export และ summary |
| `pi_agent/test_benchmark.py` | Parity, notebook format, scoring, review, timeout/process cleanup และ export tests |
| `pi_agent/test_benchmark_bridge.mjs` | Pi loop กับ mock stream, real MCP และ native session tests โดยไม่เรียกโมเดล |

แก้ Pi Dockerfile เพื่อติดตั้ง FastMCP และเพิ่ม direct dependency
`@modelcontextprotocol/client==2.0.0` สำหรับ stdio transport
อัปเดต package-lock ด้วย npm; dependency ที่มีอยู่เดิมไม่ได้อัปเกรด
Compose เปลี่ยนเฉพาะ Pi: เอาข้อบังคับ Tavily key ออกและเพิ่ม node_modules mount
การแก้ OpenAI/Compose ส่วน OpenAI ที่มีอยู่ก่อนงานนี้คงไว้
ไม่ได้แก้ Deep/OpenAI notebook, shared cases/scoring, credentials หรือผลทดลองเก่า

## แหล่งหลักและ parity

อ่านต้นฉบับทั้ง source, markdown และผลรัน แล้วใช้เฉพาะโจทย์/fixtures/เกณฑ์เดิม
ไม่ใช้คะแนนหรือคำตอบเก่าเป็นผลทดลอง Pi Source SHA-256:

```text
74a9cc6f1e13ce628463572a83f71ef6dd1432aae6475c735fce03ecc8fc4e42
```

`shared/benchmark_cases.json` เป็นชุดเก่า `long_prompt_math` และ `web_research_ptt`
ซึ่งต่างจาก 17 เคสใน Deep notebook และ `shared/scoring.py` ว่าง จึงไม่ได้ใช้แทนโจทย์หลัก
`pi_comparison.ipynb` เดิมมี smoke token/model hardcode และตัวอย่าง Tavily
ซึ่งไม่ตรงชุด benchmark นี้ จึงไม่ย้ายตัวอย่างนั้นมาเป็นเคสใหม่
OpenAI notebook/conversion notes ใช้อ้างอิง envelope/status เท่านั้น
ตัวแปร expected, สูตร, errors และ prompts ตัดสินจาก Deep notebook โดยตรง

Notebook metadata ระบุ source cells และ copied assignments สำหรับตรวจ AST parity
คง case IDs ทั้ง 17 เคส; `case_version` เป็น `"2.0"` เฉพาะ web ทั้งสองเคส
เคสที่ต้นฉบับไม่กำหนดเป็น null; fixture_version ที่ระบุไว้ยังคงแยกจาก case_version
Smoke ใช้ ID `smoke` ในผล Pi และคง prompt `Run the smoke test.` กับ fixed token
`DEEP_AGENTS_SDK_OK` เพื่อเทียบ literal เดียวกัน

คง custom system instructions, Thai whitespace, ตัวเลข, company/source IDs,
ลำดับ backup 2024 ก่อน 2025, expected rankings, missing-data policy และ tolerance ±0.01 ของ Math
Business functions คัดโดยตัดเฉพาะ LangChain decorator: ยังใช้ Python float arithmetic,
`round(value, 2)`, `str.casefold()`, exception และ error payload เดิม
Pi ได้ schemas จาก Python signatures/docstrings และเป็นผู้เลือก tools/arguments เอง
Bridge ไม่ได้รับ expected answers และไม่มีลำดับแก้โจทย์สำเร็จรูป

Long context ตรวจ UTF-8 **ทุก byte** และ SHA-256 ทั้ง prompt:

| เคส | Python code points | SHA-256 |
|---|---:|---|
| long_context_001 | 14,022 | `3e628fe8b42d626292c855a9c91f3e9bd3a910a4e2d44580d482cbf0199021ae` |
| long_context_002 | 61,782 | `34a271405c3e64dfa2c32efb8d11b716278bc2b910a775944fb27cf77ebac52e` |

Node บันทึก hash, UTF-16 length และ code-point count แยกกันในแต่ละ turn
จึงไม่ใช้ `string.length` เทียบกับ Python len โดยสมมติว่าหน่วยเดียวกัน

## ความสามารถและความต่างของ runtime

| หมวด | Pi implementation | สถานะของ integration |
|---|---|---|
| Smoke, Math 3 | Pi session + calculator allowlist | รองรับ; ยังไม่รันโมเดลจริง |
| MCP 3 | FastMCP stdio → pi-mcp-adapter host-managed → Pi | รองรับผ่าน adapter เพิ่ม |
| Web 2 | คงโจทย์และหลักฐานข้อจำกัด | unsupported ใน integration 0.87.1 ที่ตรวจ |
| Planning 2 | Pi + business tools + custom write_todos | รองรับ; คุณภาพแผน/คำตอบรอ review |
| Long context 2 | Pi ไม่มีเครื่องมือ | รองรับ; ตรวจ text parity แล้ว |
| History 3 | native SessionManager JSONL | รองรับ; ต่างจาก source in-memory/SQLite backend |
| Error 2 | original Python error fixtures เป็น Pi custom tools | รองรับ; แยก business retry จาก API retry |

ใช้ `createAgentSession`, `SessionManager`, `ModelRuntime`, `SettingsManager`
และ `DefaultResourceLoader` ตาม exports/types/examples ในแพ็กเกจที่ติดตั้ง
ตั้ง `tools` เป็น allowlist ที่ชัดเจนร่วมกับ `noTools:'builtin'`
ตรวจ active tool names ก่อนรัน ไม่มี file/shell tools หรือ `.pi/mcp.json`/skills ของผู้ใช้หลุดเข้ามา
Pi เก็บ authored instructions เดิมและเพิ่ม `<cwd>` section; บันทึก
`effective_instructions` จริงทุก run แทนการอ้างว่า system prompt ของทุก harness เหมือนกัน

`write_todos` เป็น custom tool ที่มี todos array ของ `{content, status}`
สถานะ pending/in_progress/completed และแทนรายการทั้งหมดตามการเรียกครั้งล่าสุด
ไม่มีแผนสำเร็จรูป ไม่ย้าย TodoListMiddleware หรือ built-in prompt ของ Deep Agents มาครอบ Pi
ความต่างที่ยังมี: ไม่ใช้ middleware guard ที่ปฏิเสธหลาย write_todos ใน assistant message เดียว
ตรวจเฉพาะแผนที่สังเกตได้, ลำดับ data calls และ updates ไม่วิเคราะห์ hidden reasoning

ทุก prompt จำกัด **40 model requests** ผ่าน wrapper ที่ public `agent.streamFunction`
ก่อนเรียก provider; ไม่มี retry จนผ่าน ไม่อาศัย exception จาก extension callback เพราะ
Pi จับ exception ของ callback บางชนิดแล้วทำงานต่อ
บันทึก source recursion_limit แยกกัน ไม่ถือว่าเท่ากับ request budget หรือ OpenAI max_turns
API retry และ harness retry เป็น 0; ปิด compaction/cache warming เพื่อลดการเรียกที่ซ่อนอยู่
Business retry เกิดจากการตัดสินใจของโมเดลหลังเห็น error flag ตามโจทย์เท่านั้น

Python → Node ใช้ argument list กับ JSON stdin/stdout ไม่มี shell interpolation
stdout เป็น NDJSON events และ final result; logs เป็น stderr
ถ้า process เกิน deadline จะ kill process group รวม child workers แล้วเก็บ partial trace/error
แต่ trace ไม่ครบทำให้ tool_call_count/usage ที่ไม่ทราบเป็น null
เวลา setup/agent/cleanup วัดใน Node; Python wall time รวม Node startup/imports
startup แยกวัดไม่ได้ให้ null ไม่หักส่วนต่างสอง clock แล้วเรียกว่า overhead
Python business worker IPC รวมอยู่ใน agent time ส่วน MCP discovery อยู่ใน setup

## MCP ที่ตรวจจริง

ใช้ `pi-mcp-adapter==3.2.0` export `createHostManagedMcpAdapter`
จาก `pi-mcp-adapter/host-managed` (TypeScript source โหลดผ่าน tsx)
โปรไฟล์นี้มี `ready()` เพื่อ connect/listTools และ freeze catalog,
`extensionFactory` เพื่อ register tools และ `close()` เพื่อ abort/ปิด transport
ไม่มี reconnect/resend อัตโนมัติ

Host-managed adapter ต้องมี approval broker; bridge อนุญาตเฉพาะสองชื่อของ
local fixture server `finance` ที่เคสเลือกไว้ ไม่อ่าน external MCP configuration
Adapter เติม `finance_` prefix; ตอน register กับ Pi map กลับชื่อ business เดิม
เก็บ original name, adapter name, call ID, arguments, raw MCP result และ decoded output
เพื่อให้ audit mapping ได้

`onToolCall` dispatch tools/call ผ่าน MCP จริงครั้งเดียวต่อ agent call
Business error เป็น JSON result เดิม ส่วน protocol/transport errors เก็บ delivery
`not_sent`, `may_have_run`, `server_error`, `invalid_result` ตาม adapter
ไม่ถือ network failure เป็น business retryable flag โดยปริยาย
FastMCP และ adapter ปิดใน finally; outer timeout ฆ่า subprocess group
Transport เปลี่ยนจาก source in-memory เป็น stdio จึงมี setup/serialization overhead

## เหตุผลที่ Web เป็น unsupported

ตรวจ source ของ `pi-ai==0.87.1` ที่ resolve จาก coding-agent ที่ติดตั้ง:

- `dist/api/openai-responses.d.ts`: public OpenAIResponsesOptions ไม่มี hosted web tool setting
- `dist/api/openai-responses-shared.js`: output slot รองรับ reasoning, message,
  function_call, custom_tool_call แต่ไม่ส่ง `web_search_call.action` ไป public trace
- parser ไม่เก็บ output_text URL annotations เป็น citation events
- `onPayload`/`before_provider_request` แก้ request ได้ แต่ไม่ทำให้ evidence ที่ขาดปรากฏ

จึงไม่เติม `{"type":"web_search"}` ลง payload แล้วอ้างว่ารองรับ native benchmark
ไม่เรียก Responses API ตรงเพื่อทำงานแทน Pi และไม่ใช้ Tavily, MCP หรือ custom fetch ในหมวดนี้
ทั้งสองเคสเก็บ prompt/instructions/version/evidence พร้อม runtime_status และ
evaluation_status เป็น unsupported, task_passed เป็น null และไม่เรียกโมเดล

ทางเลือกในอนาคตคือเพิ่ม provider adapter ที่คง Pi loop และส่งต่อ raw hosted events/citations
หรือใช้ Pi รุ่นที่รองรับครบ ต้องทำเป็น experiment variant แยกและตรวจ source ใหม่ก่อนรัน
เมื่อรองรับแล้ว Web 001 ต้อง search และเปิดแหล่ง Microsoft; Web 002 ต้องเปิดทั้ง
Microsoft กับ Alphabet สอง MS URLs ไม่ผ่าน และ domain sec.gov อย่างเดียวไม่ยืนยันบริษัท
ต้องแยก open attempt จาก successful retrieval/content support; citations ไม่ใช่หลักฐาน fetch
เจ้าของเอกสารหรือเนื้อหาที่ยังยืนยันไม่ได้ให้ pending_review

## History และ Restart

history_001 seed/recall ภายใน Node invocation เดียวและบันทึก JSONL
history_002 process ใหม่โหลดไฟล์นั้นก่อน correction/recall ไม่อ้างว่าเป็น in-memory
เก็บ parent_run_id, session path/ID/hash และตรวจ original context ก่อน model call
Usage มาจาก new assistant messages ใน invocation นั้นเท่านั้น

history_003 seed สร้าง manifest ของ run/session นี้ใต้ Pi state แล้ว export pending result
หยุดที่เซลล์ **Restart Kernel** ซึ่งตั้งใจ raise เพื่อกัน Run All ข้ามจุดทดสอบ
Restart Python kernel → รัน Config → ข้ามมาเลือก manifest ของ seed ล่าสุด
เปลี่ยน MANIFEST_INDEX ได้หากต้องการ seed รอบอื่น ไม่มี hardcoded path จาก SDK อื่น

Resume ตรวจ Python PID/instance, Node PID, model/provider/thinking setting,
package versions, JSONL byte hash และ restored user/assistant messages **ก่อน model call**
มี marker กัน resume รอบเดียวซ้ำ; หากต้องทดลองใหม่ให้ seed ใหม่
คำถาม resume คัดต้นฉบับและไม่มี seed values/expected answers ป้อนซ้ำ
seed_execution แยก trace/usage/latency จาก resume; top-level usage ของ history_003
เป็น resume ใหม่เท่านั้น ไม่รวม seed หรือข้อความที่อ่านกลับมาซ้ำ

## Scoring และไฟล์ผล

Paths ใน container ใช้ shared mount ระดับ repository:

```text
/workspace/shared/results/pi_agent/
/workspace/shared/state/pi_agent/
/workspace/shared/summaries/
```

ไม่สร้าง `pi_agent/shared`; local path resolve จาก parent ของโฟลเดอร์ Pi
ทุก category export เอง ไม่อ้าง output_path จากหมวดก่อนหน้า
ใช้ envelope `schema_version, saved_at_utc, sdk, category, results`, sdk=`pi_agent`
run ID สร้างก่อนเริ่ม การเรียก run cell ซ้ำสร้าง experiment ID ใหม่และล้าง review
การ export ผลเดิมซ้ำรักษา ID แต่เขียนไฟล์ชื่อใหม่เสมอ
ถ้ารัน history_002 ซ้ำหลัง source session ถูกแก้ไข hash เก่าจะไม่ตรง ต้องเริ่ม history_001 ใหม่

task_passed เป็น true เมื่อครบเกณฑ์, false เมื่อมีเกณฑ์ไม่ผ่าน, null เมื่อยังประเมินไม่ได้
แยก evaluation_status passed/failed/pending_review/runtime_error/unsupported
Planning ตรวจโครงสร้าง/ลำดับอัตโนมัติและรอ review ด้านคุณภาพ, สูตร, sources และ missing data
การตรวจสูตรหมายถึง arithmetic tool arguments/metrics; รายละเอียดการเขียน ×100 ในคำอธิบาย
บันทึกเป็น quality observation ตาม source ไม่ยกเป็นเงื่อนไขบังคับใหม่
MCP 001 คงเงื่อนไขมี successful search→fetch chain อย่างน้อยหนึ่งชุด; MCP 002 คง
เงื่อนไขพบ empty search อย่างน้อยหนึ่งครั้งและไม่มี fetch ไม่บังคับให้ทุก search ว่าง
Planning ต้องเขียนแผนใน assistant message ก่อน data calls; สอง calls ใน message เดียวไม่ถือว่าผ่าน
Tool execution errors ที่ agent แก้ต่อได้เก็บเป็น observation ไม่ตัดคะแนนเพิ่มโดยลำพัง
ส่วน trace ที่ไม่ครบให้ pending/null และ MCP transport/protocol failure เป็น runtime_error
ไม่คัด Planning=True, Web=False หรือ Math failure analysis ของผล Deep Agents เก่า
Review ผูก run_id และ hash ของ answer/trace; เปลี่ยนหลักฐานแล้ว review เดิมใช้ไม่ได้
Review เก็บชื่อผู้ตรวจ, notes, timestamp, revisions; export ไม่ต้อง assert ว่าผ่าน

Pi อาจ normalize usage ที่ provider ไม่ให้เป็น zero จึงเก็บ raw_sdk_usage พร้อมคำอธิบาย
aggregate usage ให้ null เมื่อ response ไม่สมบูรณ์หรือไม่มี usage ที่ใช้ได้
input ของ Pi แยก cacheRead/cacheWrite ออก จึงห้ามเทียบ input field กับ SDK อื่นโดยไม่รวมความหมาย
reasoning tokens ที่ SDK ไม่เปิดเผยใช้ null และไม่อ่าน hidden reasoning
Raw trace เก็บ observable assistant/tool events; thinking blocks ไม่ถูกส่งออกใน benchmark result
Native session อาจเก็บ provider context/signature เพื่อ replay ตามกลไก Pi

Summary อ่านเฉพาะผล Pi, แยก case_version, deduplicate run_id และเลือก review revision ล่าสุด
แสดง pending/runtime_error/unsupported แยกจาก failed
pass_rate_evaluated_only ใช้เฉพาะ passed+failed เป็น denominator ไม่ซ่อน unsupported

## การตรวจงาน

ทดสอบโดยไม่ใช้ API key จริงและไม่เรียกโมเดล:

```sh
python -m unittest discover -s pi_agent -p test_benchmark.py -v
cd pi_agent
PI_TEST_PYTHON=/path/to/kernel/python node --import tsx test_benchmark_bridge.mjs
```

ตรวจ notebook nbformat/syntax/outputs, 17+1 inventory, AST parity ของ prompts,
custom instructions, expected values และ business functions, UTF-8 hashes ของ long prompts
ทดสอบ Pi loop จริงด้วย synthetic stream ที่ inject เฉพาะใน test code,
tool/error/schema allowlist, request budget, Thai Unicode, native session เปิดใน child process ใหม่,
usage ไม่รวม seed, MCP discovery และ calls จริง, timeout kill รวม child,
review invalidation, status semantics, unique export และ summary dedup/version separation
Test artifacts อยู่ temp directories ไม่สร้างผล benchmark ปลอมใต้ shared

สร้าง Pi Docker image สำเร็จด้วย Python 3.11 และทดสอบซ้ำใน container ที่ปิด network
การตรวจเหล่านี้ไม่ยืนยันคุณภาพคำตอบของโมเดลจริง, provider account permissions,
latency/cost ของ benchmark จริง หรือ live hosted web support ของรุ่นอื่น
ไม่ได้รัน benchmark จริง, ไม่ commit และไม่ push

## เอกสารอ้างอิงที่ตรวจประกอบ installed source

- [Pi repository และ package separation](https://github.com/earendil-works/pi)
- [Pi MCP Adapter](https://github.com/nicobailon/pi-mcp-adapter)
- Installed `pi-coding-agent/examples/sdk/05-tools.ts`, `11-sessions.ts`, `12-full-control.ts`
- Installed `pi-coding-agent/dist/core/sdk.d.ts`, `model-runtime.d.ts`, `settings-manager.d.ts`, `session-manager.d.ts`
- Installed `pi-mcp-adapter/host-managed.ts`, `types.ts`, `tool-approval.ts`, `README.md`

เว็บอ้างอิงอาจเป็นรุ่นใหม่กว่า; signatures และข้อสรุป capability ใน Notebook นี้ยึด installed
package/lockfile รุ่นที่ระบุ ไม่ใช้ความสามารถบน main branch มาสมมติว่ารุ่นนี้มีแล้ว
