# OpenAI Agents SDK benchmark conversion

ไฟล์ `openai_agent/openai_agents_benchmark.ipynb` แปลงจาก
`deep_agent/2610051200_deepagents_benchmark.ipynb` เพื่อรันโจทย์ชุดเดียวกันด้วย
OpenAI Agents SDK (`openai-agents`, import ผ่าน `agents`) โดยไม่เรียก Deep Agents,
LangChain หรือ LangGraph มาทำงานแทน

## ขอบเขตที่คงจากต้นฉบับ

Notebook คงลำดับ Config, Smoke Test, Math 3 เคส, MCP 3 เคส, Web 2 เคส,
Planning 2 เคส, Long context 2 เคส, Session history 3 เคส, Error handling
2 เคส และส่วนสรุปผล รวม 17 เคสหลักกับ Smoke Test

User prompts, fixtures, business tool names, arguments, expected answers,
case IDs, case versions ที่ต้นฉบับระบุ และ tolerance ทางตัวเลขถูกคัดจาก cell
ต้นฉบับโดยตรง Metadata ของ Notebook บันทึก SHA-256 ของ Notebook ต้นฉบับและ
รายการตัวแปรที่คัดลอกไว้สำหรับ audit

คะแนนหรือบทวิเคราะห์ที่ผูกกับผลรัน Deep Agents รอบเก่าไม่ถูกนำมาใช้ เช่น
การกำหนดคะแนน Planning เป็น `True`, การระบุว่า Web ไม่เปิด Alphabet และ
failure analysis ของ `math_002` การทดลอง OpenAI ตรวจจากผลรันใหม่เท่านั้น

## Mapping ของ OpenAI Agents SDK

| ความสามารถ | การใช้งานใน Notebook |
|---|---|
| Agent loop | `Agent` และ `await Runner.run(...)` |
| Function tools | `@function_tool` โดยคงชื่อและ schema ของ business tools |
| MCP | `MCPServerStreamableHttp` เชื่อม FastMCP บน localhost |
| Hosted web | `WebSearchTool` ผ่าน Responses API พร้อม raw response evidence |
| Session history | `SQLiteSession`; in-memory สำหรับเคส 001/002 และไฟล์ SQLite สำหรับ restart |
| Final output | `RunResult.final_output` |
| Tool trace | `RunHooks.on_llm_end/on_tool_end`, raw model output และ server fixture log |
| Usage | `ModelSettings(preserve_raw_usage=True)`; ค่าไม่ปรากฏใช้ `null` |

API และ signatures ถูกตรวจด้วย `openai-agents==0.23.1`, `openai==3.24.0`,
`fastmcp==4.0.11` และ `mcp==2.3.0` อ้างอิงเอกสารทางการ:

- https://developers.openai.com/api/docs/guides/agents/sdk
- https://developers.openai.com/api/docs/guides/agents/running-agents
- https://developers.openai.com/api/docs/guides/agents/integrations-observability
- https://developers.openai.com/api/docs/guides/tools-web-search

## ความต่างของ SDK

`recursion_limit` ของ Deep Agents เป็นจำนวนขั้นตอนของ LangGraph ส่วน `max_turns`
ของ OpenAI Agents SDK เป็นจำนวนครั้งสูงสุดที่ runner เรียกโมเดล จึงไม่ใช่ค่าที่
เทียบกันแบบหนึ่งต่อหนึ่ง Notebook บันทึกทั้งค่าเดิมและค่าที่ใช้ใหม่พร้อมคำอธิบาย
และใช้ API retry กับ runner retry เป็นศูนย์เหมือนนโยบายเดิม

MCP ต้นฉบับใช้ in-memory adapter แต่ OpenAI Agents SDK รุ่นที่ตรวจรองรับ local
stdio หรือ Streamable HTTP จึงเปลี่ยนเป็น Streamable HTTP บน localhost ผ่าน MCP
จริง พร้อมแยก setup, agent และ cleanup latency

OpenAI Agents SDK ไม่มี `TodoListMiddleware` ของ Deep Agents จึงเพิ่ม custom
function tool `write_todos` ที่มีชื่อ, input schema, สถานะ และพฤติกรรมแทนรายการ
เหมือนต้นฉบับ พร้อมบันทึก system instruction เดิม ข้อความที่เพิ่ม และเหตุผลใน
แต่ละ run เครื่องมือนี้ถูกระบุชัดว่าเป็น custom tool

Web ใช้ hosted `WebSearchTool` ของ OpenAI เท่านั้น การตรวจ `search` และ
`open_page` อ่านจาก raw `web_search_call.action.type` แยกกัน Citation ไม่ถูกนับ
เป็นหลักฐานว่าเปิดหน้าแล้ว เจ้าของเอกสารถูกจำแนกจาก hostname หรือ SEC CIK ที่
รู้แน่ชัด หากระบุไม่ได้จะเป็น `null` เพื่อรอตรวจ การเปิดสำเร็จและเนื้อหาที่รองรับ
คำตอบเป็น manual review ที่ผูกกับ `run_id` และ SHA-256 ของหลักฐาน

## ผลลัพธ์และการ review

ผลอยู่ที่ `/workspace/shared/results/openai_agents/`, state และ review templates
อยู่ที่ `/workspace/shared/state/openai_agents/` และสรุปอยู่ที่
`/workspace/shared/summaries/` ทุก export ใช้ไฟล์ใหม่และคง `run_id` ของ run เดิม

`task_passed` มีสามค่า: `true` ผ่านทุกเกณฑ์, `false` มีเกณฑ์จำเป็นไม่ผ่าน และ
`null` เมื่อ runtime ล้มเหลวจนประเมินคำตอบไม่ได้หรือยังรอตรวจ Runtime error,
partial trace และ partial usage ถูกเก็บแยกจากคะแนนคำตอบ Usage ที่ SDK ไม่เปิดเผย
ไม่ถูกแทนด้วยศูนย์

Manual review template เริ่มทุกช่องเป็น `null` และบังคับให้ `run_id` กับ
`evidence_sha256` ตรงกับผลปัจจุบัน ต้องระบุผู้ตรวจ เหตุผล และหลักฐานก่อนนำคะแนน
มาใช้ การ review สร้าง revision chain เพื่อให้ summary ตรวจ duplicate export และ
ผลที่ขัดกันได้

## วิธีรัน

จาก repository root:

```bash
docker compose up --build openai-agent
```

`docker-compose.yml` เอา `TAVILY_API_KEY` ออกจาก environment ของบริการ
`openai-agent` เพราะ Notebook ใช้ OpenAI hosted web tool และไม่ใช้ Tavily ส่วน
volume `./shared:/workspace/shared` เดิมถูกต้องอยู่แล้วจึงคงไว้

เปิด `http://localhost:8888/lab?token=agent-sdk-local` แล้วเปิด
`openai_agents_benchmark.ipynb` เริ่มรันหัวข้อ `0.Config` และ `1.Create Agent /
Smoke Test` ก่อน จากนั้นรันทีละหมวดตามลำดับ

เมื่อถึง `history_003` ให้ทำตาม cell `Restart Kernel` อย่างเคร่งครัด: หลัง seed
ถูกบันทึก เลือก Kernel → Restart Kernel, รันเฉพาะ Config แล้วข้ามไปที่ cell กู้
session หลัง restart ห้ามใช้ Restart and Run All เพราะจะสร้าง seed run ใหม่

## การตรวจที่ทำโดยไม่เรียกโมเดล

- ตรวจรูปแบบ Notebook, Python syntax รวม top-level `await`, outputs และ
  execution counts
- โหลด imports และสร้าง Agent/function tools ใน environment ของเวอร์ชันที่ pin
- ตรวจ schemas ของ function tools
- เปิด FastMCP ผ่าน localhost Streamable HTTP, list tools และเรียก fixture จริง
- ปิดและเปิด `SQLiteSession` ใหม่แล้วตรวจว่าประวัติจาก disk เหมือนเดิม
- ตรวจ prompts และ function fixture เทียบกับ Notebook ต้นฉบับ
- ทดสอบ helper สำหรับ runtime failure, pending review, evidence hash, revision และ
  JSON export ด้วยข้อมูลจำลอง

ไม่ได้เรียก OpenAI model API และไม่ได้รัน benchmark จริง จึงยังไม่มีผลคุณภาพ,
latency หรือ token usage ของ 17 เคส การยืนยัน raw hosted-web trace กับพฤติกรรม
ของโมเดลจริงต้องทำระหว่าง benchmark run
